"""One active agent, durable run history and a thread-safe event journal."""

import asyncio
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from ..agent import run_agent
from ..brain.shadow import ShadowBackend
from ..domain import TaskSpec
from ..llm import HeuristicCandidateGenerator, make_lmstudio_cortex
from ..llm.lmstudio import validate_model_id
from ..orchestration.sources import Sources
from ..orchestration.store import Store, StoreError, default_state_path
from ..policy import ControlMode
from ..policy.rollout import RolloutConfig
from ..research.benchmark import ControllerSpec
from ..research.cortex import CortexConfig
from ..research.validation import authorize
from ..telemetry import TelemetryLogger
from ..telemetry.events import AgentEvent
from ..verification.custody import locked
from .artifacts import ArtifactCatalogue, default_artifact_root
from .execution import LegacyEnvironment, SourceExecution
from .projects import ProjectRegistry

ACTIVE = {"queued", "running", "stopping"}


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10000)
    project: str | None = Field(default="demo", max_length=1000)
    llm: Literal["lmstudio", "heuristic"] = "lmstudio"
    brain: Literal["functional", "full", "reduced"] = "functional"
    controller_id: str | None = Field(default=None, max_length=100)
    observer_id: str | None = Field(default=None, max_length=100)
    influence: float = Field(default=0., ge=0, le=1, allow_inf_nan=False)
    execution_mode: Literal["standard", "autonomous"] = "standard"
    admission_id: str | None = Field(default=None, max_length=100)
    learning: bool = False
    memory_profile: str = Field(default="default", min_length=1, max_length=64)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    max_steps: int = Field(default=25, ge=1, le=100)
    max_tool_calls: int | None = Field(default=None, ge=1, le=1000)
    max_model_calls: int | None = Field(default=None, ge=1, le=1000)
    request_timeout: float = Field(default=120., gt=0, le=600, allow_inf_nan=False)
    model: str = Field(default="qwen/qwen3.5-9b", min_length=1, max_length=200)
    decision_model: str | None = Field(default="jev-style-qwen3.5-2b-decision", min_length=1, max_length=200)

    _model_ids = field_validator('model', 'decision_model')(validate_model_id)

    @model_validator(mode="after")
    def compatible_modes(self):
        RolloutConfig(influence=self.influence)
        if not all(char.isalnum() or char in "_-" for char in self.memory_profile):
            raise ValueError("Имя памяти: только буквы, цифры, дефис и подчёркивание")
        if self.learning and (self.influence or self.execution_mode == "autonomous"):
            raise ValueError("Допуск проверяет замороженную политику; отключите обучение")
        if self.influence and (not self.observer_id or self.execution_mode == "autonomous"):
            raise ValueError("Влияние требует наблюдателя и обычного режима")
        return self

    @field_validator("task")
    @classmethod
    def nonempty_task(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe the task")
        return value.strip()


class RunRecord(BaseModel):
    id: str
    request: RunRequest
    status: str = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    result: dict | None = None
    error: str = ""
    source_claim_id: str | None = None


def bounded(value):
    """Bound tool output retained by the browser journal, including nested payloads."""
    if isinstance(value, str):
        return value if len(value) <= 16000 else value[:16000] + "\n… output truncated"
    if isinstance(value, dict):
        return {key: bounded(item) for key, item in list(value.items())[:300]}
    if isinstance(value, list):
        return [bounded(item) for item in value[:300]]
    return value


class RunManager:
    def __init__(self, workspace: Path, data_dir: Path, artifact_root: Path | None = None) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.projects_file = self.data_dir / "projects.json"
        self.projects = (ProjectRegistry.model_validate_json(self.projects_file.read_text(encoding="utf-8"))
                         if self.projects_file.exists() else ProjectRegistry())
        self.records: dict[str, RunRecord] = {}
        self.events: dict[str, list[AgentEvent]] = {}
        self.stops: dict[str, Event] = {}
        self.source_recovery_error = ''
        self.catalogue = ArtifactCatalogue(artifact_root or default_artifact_root())
        paths = sorted(self.data_dir.glob("*/record.json"), key=lambda item: item.stat().st_mtime)
        visible = set(paths[-50:])
        for path in paths:
            try:
                record = RunRecord.model_validate_json(path.read_text())
                if path.parent.name != record.id:
                    continue
                journal = path.with_name("events.jsonl")
                events = []
                if path in visible and journal.exists():
                    for line in journal.read_text().splitlines():
                        try:
                            events.append(AgentEvent.model_validate_json(line))
                        except ValueError:
                            break  # A process can exit partway through the final append.
                if path in visible:
                    self.records[record.id], self.events[record.id] = record, events
                if record.status in ACTIVE | {'reconciling', 'interrupted'}:
                    before = record.model_dump_json()
                    if record.status in ACTIVE:
                        record.status = "interrupted"
                        record.error = "Server stopped before the run completed"
                    store = Store(default_state_path())
                    try:
                        if record.source_claim_id is None:
                            record.source_claim_id = self._quarantine(store, record)
                        try:
                            Sources(store).settle(record.source_claim_id)
                        except StoreError as error:
                            reason = f'source retained: {error.code}'
                            if reason not in record.error:
                                record.error += f'; {reason}'
                    except (OSError, ValueError) as error:
                        self.source_recovery_error = f'Source ownership needs reconciliation: {error}'
                        record.status = 'reconciling'
                        record.error = self.source_recovery_error
                    finally:
                        store.close()
                    if record.model_dump_json() != before:
                        self._save(record)
            except (OSError, ValueError):
                continue

    def project_path(self, project: str) -> Path:
        with self.lock:
            registered = self.projects.resolve(project)
        if registered is not None:
            return registered
        if Path(project).is_absolute():
            raise ValueError("Select a project inside the workspace")
        path = (self.workspace / project).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_dir():
            raise ValueError("Project must be an existing directory inside the workspace")
        if any(part.startswith(".") and part != "." for part in Path(project).parts):
            raise ValueError("Hidden directories are not projects")
        return path

    def _quarantine(self, store, record):
        existing = store.db.execute("SELECT id FROM source_claims WHERE owner_kind='legacy' AND owner_id=?",
                                    (record.id,)).fetchone()
        if existing is not None:
            return existing['id']
        try:
            root = (self.data_dir / record.id / 'repository' if record.request.project in (None, 'demo')
                    else self.project_path(record.request.project))
            reference = Sources(store).prepare(root)
        except (OSError, ValueError):
            reference = Sources(store).prepare(Path('/'))
        path = self.data_dir / record.id / f'recovery-{uuid4().hex}.lock'
        with locked(path, create=True) as descriptor:
            info = os.fstat(descriptor)
            return Sources(store).acquire(reference, owner_kind='legacy', owner_id=record.id,
                coordinator_path=path, coordinator_identity=(info.st_dev, info.st_ino), quarantine=True).id

    def _save(self, record: RunRecord) -> None:
        directory = self.data_dir / record.id
        directory.mkdir(exist_ok=True)
        temporary = directory / "record.tmp"
        temporary.write_text(record.model_dump_json(), encoding="utf-8")
        temporary.replace(directory / "record.json")

    def snapshot(self, run_id: str) -> dict:
        with self.lock:
            return self.records[run_id].model_dump(mode="json")

    def history(self) -> list[dict]:
        with self.lock:
            return [record.model_dump(mode="json") for record in reversed(list(self.records.values()))]

    def emit(self, run_id: str, kind: str, data: dict) -> None:
        with self.lock:
            journal = self.events[run_id]
            event = AgentEvent(id=len(journal) + 1, kind=kind, data=bounded(data))
            with (self.data_dir / run_id / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(event.model_dump_json() + "\n")
            journal.append(event)

    def start(self, request: RunRequest) -> dict:
        run_id = uuid4().hex
        repo = (self.data_dir / run_id / "repository" if request.project is None else
                None if request.project == "demo" else self.project_path(request.project))
        if request.llm == "heuristic" and repo is not None:
            raise ValueError("Offline demonstration is available only in the demo project")
        with self.lock:
            if self.source_recovery_error:
                raise RuntimeError(self.source_recovery_error)
            if any(record.status in ACTIVE for record in self.records.values()):
                raise RuntimeError("An agent is already running; stop it before starting another")
            brain, cortex_config, memory, admission = self._prepare(request, repo)
            directory = self.data_dir / run_id
            directory.mkdir(mode=0o700)
            actual = repo or directory / 'repository'
            actual.mkdir(parents=True, exist_ok=True)
            try:
                execution = SourceExecution(actual, directory, run_id, claim_root=directory if repo is None else None)
            except BaseException as error:
                brain.close()
                if isinstance(error, StoreError) and error.code == 'source_busy':
                    raise RuntimeError('Source folder is occupied; wait for confirmed stop (source_busy)') from error
                raise
            record = RunRecord(id=run_id, request=request, source_claim_id=execution.claim.id)
            self.records[run_id], self.events[run_id], self.stops[run_id] = record, [], Event()
            thread = None
            try:
                self._save(record)
                self.emit(run_id, "status", {"phase": "queued"})
                if admission is not None:
                    self.emit(run_id, "admission", admission)
                thread = Thread(target=self._execute, args=(run_id, repo, brain, cortex_config, memory, execution),
                                daemon=True, name=f"flycode-{run_id[:8]}")
                thread.start()
            except BaseException as error:
                self.stops[run_id].set()
                record.status, record.error = 'failed', str(error)
                if thread is None or isinstance(error, RuntimeError) and thread.ident is None:
                    brain.close()
                    execution.finish()
                else:
                    with execution.sources() as sources:
                        sources.mark_uncertain(execution.claim.id, 'thread_start_unknown')
                    record.status = 'reconciling'
                self._save(record)
                raise
            return record.model_dump(mode="json")

    def _prepare(self, request: RunRequest, repo: Path | None):
        self.catalogue.refresh()
        active = self.catalogue.resolve(request.controller_id or request.brain)
        observer = self.catalogue.resolve(request.observer_id) if request.observer_id else None
        if request.learning and active.brain != "functional":
            raise ValueError("Онлайн-память доступна для Functional Fly")
        config = CortexConfig(cortex=request.llm, model=request.model, decision_model=request.decision_model,
                              request_timeout=request.request_timeout,
                              max_model_calls=request.max_model_calls or request.max_steps * 2)
        spec = ControllerSpec(id="browser", **active.model_dump(), observer=observer,
                              influence=request.influence, autonomous=request.execution_mode == "autonomous")
        brain = None
        try:
            brain = active.create(request.seed)
            brain.set_learning_mode(request.learning)
            memory = None
            if request.learning:
                key = hashlib.sha256(f'{request.memory_profile}:{request.seed}:{request.controller_id or request.brain}'
                                     .encode()).hexdigest()
                memory = self.data_dir / 'memory' / key
                if not memory.resolve().is_relative_to(self.data_dir):
                    raise ValueError("Папка памяти выходит за каталог приложения")
                for child in ('learning.json', 'learning.tmp'):
                    if not (memory / child).resolve().is_relative_to(self.data_dir):
                        raise ValueError("Файл памяти выходит за каталог приложения")
                if (memory / 'learning.json').exists():
                    brain.load_learning(memory)
            if observer is not None:
                brain = ShadowBackend(brain, observer.create(request.seed), influence=request.influence)
            admission = None
            if request.influence or spec.autonomous:
                admission = authorize(self.catalogue.admission(request.admission_id), identity=spec.identity(brain),
                    config=config, fixture='toy_arithmetic_bug' if repo is None else None, seed=request.seed,
                    control='hard' if spec.autonomous else 'bias', max_steps=request.max_steps,
                    max_tool_calls=request.max_tool_calls or request.max_steps,
                    mode='autonomous' if spec.autonomous else 'bounded',
                    influence=1. if spec.autonomous else request.influence, task=request.task)
            request.brain = cast(Literal["functional", "full", "reduced"], active.brain)
            return brain, config, memory, admission
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            if brain is not None:
                brain.close()
            raise ValueError(str(exc)) from exc

    def stop(self, run_id: str) -> dict:
        with self.lock:
            record = self.records[run_id]
            if record.status in ACTIVE:
                self.stops[run_id].set()
                record.status = "stopping"
                self._save(record)
                self.emit(run_id, "status", {"phase": "stopping"})
            return record.model_dump(mode="json")

    def _execute(self, run_id: str, repo: Path | None, brain, config: CortexConfig, memory: Path | None,
                 execution: SourceExecution) -> None:
        record, stop = self.records[run_id], self.stops[run_id]
        request = record.request
        sink = lambda kind, data: self.emit(run_id, kind, data)
        result_data = None
        outcome, failure = 'failed', ''
        try:
            with self.lock:
                if not stop.is_set():
                    record.status = "running"
                self._save(record)
            cortex = (HeuristicCandidateGenerator() if request.llm == "heuristic" else make_lmstudio_cortex(
                model=request.model, decision_model=request.decision_model,
                base_url=config.base_url, timeout=config.request_timeout,
                max_model_calls=config.max_model_calls, on_event=sink, should_stop=stop.is_set,
                primary_request_executor=execution.executor(config.base_url, request.model, 'primary',
                    stop.is_set, config.request_timeout, sink),
                decision_request_executor=execution.executor(config.base_url, request.decision_model, 'scorer',
                    stop.is_set, config.request_timeout, sink) if request.decision_model is not None else None,
            ))
            task = TaskSpec(id=run_id, task=request.task, fixture="toy_arithmetic_bug" if repo is None else None,
                            seed=request.seed, max_steps=request.max_steps,
                            max_tool_calls=request.max_tool_calls or request.max_steps)
            result = asyncio.run(run_agent(
                LegacyEnvironment(execution, seed=request.seed, should_stop=stop.is_set),
                brain, cortex, task,
                control_mode=ControlMode.HARD_CONTROL if request.execution_mode == "autonomous" else ControlMode.BIAS_CONTROL,
                autonomous_guard=request.execution_mode == "autonomous",
                telemetry=TelemetryLogger(self.data_dir / run_id / "telemetry"), run_id=run_id,
                on_event=sink, should_stop=stop.is_set,
            ))
            if memory is not None:
                brain.save_learning(memory)
                self.emit(run_id, "memory_saved", {"profile": request.memory_profile})
            result_data = result.model_dump(mode="json")
            outcome = 'completed' if result.success else 'incomplete'
        except Exception as exc:  # noqa: BLE001 - a worker must persist unexpected failures for the UI
            failure = str(exc) if isinstance(exc, ValueError) else f"Run failed: {type(exc).__name__}"
        finally:
            brain.close()
            try:
                execution.finish()
            except (OSError, ValueError) as error:
                outcome = 'reconciling'
                failure = f'Source retained until effects are reconciled: {getattr(error, "code", type(error).__name__)}'
            with self.lock:
                terminal = record.model_copy(update={'result': result_data, 'error': failure,
                    'status': outcome if outcome == 'reconciling' else 'stopped' if stop.is_set() else outcome})
                self._save(terminal)
                self.records[run_id] = terminal
                self.emit(run_id, "finished", terminal.model_dump(mode="json"))

    def close(self) -> None:
        with self.lock:
            for run_id, record in self.records.items():
                if record.status in ACTIVE:
                    self.stops[run_id].set()
