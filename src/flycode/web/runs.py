"""One active agent, durable run history and a thread-safe event journal."""

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from ..agent import run_agent
from ..brain.shadow import ShadowBackend
from ..domain import TaskSpec
from ..environment import SoftwareEnvironment
from ..llm import HeuristicCandidateGenerator, make_lmstudio_cortex
from ..policy import ControlMode
from ..policy.rollout import RolloutConfig
from ..research.benchmark import ControllerSpec
from ..research.cortex import CortexConfig
from ..research.validation import authorize
from ..telemetry import TelemetryLogger
from ..telemetry.events import AgentEvent
from .artifacts import ArtifactCatalogue, default_artifact_root

ACTIVE = {"queued", "running", "stopping"}


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10000)
    project: str = Field(default="demo", max_length=1000)
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
    model: str = Field(default="qwen/qwen3.5-9b", min_length=1, max_length=200)
    decision_model: str = Field(default="jev-style-qwen3.5-2b-decision", min_length=1, max_length=200)

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
        self.records: dict[str, RunRecord] = {}
        self.events: dict[str, list[AgentEvent]] = {}
        self.stops: dict[str, Event] = {}
        self.catalogue = ArtifactCatalogue(artifact_root or default_artifact_root())
        for path in sorted(self.data_dir.glob("*/record.json"), key=lambda item: item.stat().st_mtime)[-50:]:
            try:
                record = RunRecord.model_validate_json(path.read_text())
                if path.parent.name != record.id:
                    continue
                journal = path.with_name("events.jsonl")
                events = []
                if journal.exists():
                    for line in journal.read_text().splitlines():
                        try:
                            events.append(AgentEvent.model_validate_json(line))
                        except ValueError:
                            break  # A process can exit partway through the final append.
                self.records[record.id], self.events[record.id] = record, events
                if record.status in ACTIVE:
                    record.status = "interrupted"
                    record.error = "Server stopped before the run completed"
                    record.result = None
                    self._save(record)
            except (OSError, ValueError):
                continue

    def project_path(self, project: str) -> Path:
        if Path(project).is_absolute():
            raise ValueError("Select a project inside the workspace")
        path = (self.workspace / project).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_dir():
            raise ValueError("Project must be an existing directory inside the workspace")
        if any(part.startswith(".") and part != "." for part in Path(project).parts):
            raise ValueError("Hidden directories are not projects")
        return path

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
        repo = None if request.project == "demo" else self.project_path(request.project)
        if request.llm == "heuristic" and repo is not None:
            raise ValueError("Offline demonstration is available only in the demo project")
        with self.lock:
            if any(record.status in ACTIVE for record in self.records.values()):
                raise RuntimeError("An agent is already running; stop it before starting another")
            brain, cortex_config, memory, admission = self._prepare(request, repo)
            run_id = uuid4().hex
            record = RunRecord(id=run_id, request=request)
            self.records[run_id], self.events[run_id], self.stops[run_id] = record, [], Event()
            self._save(record)
            self.emit(run_id, "status", {"phase": "queued"})
            if admission is not None:
                self.emit(run_id, "admission", admission)
            Thread(target=self._execute, args=(run_id, repo, brain, cortex_config, memory),
                   daemon=True, name=f"flycode-{run_id[:8]}").start()
            return record.model_dump(mode="json")

    def _prepare(self, request: RunRequest, repo: Path | None):
        self.catalogue.refresh()
        active = self.catalogue.resolve(request.controller_id or request.brain)
        observer = self.catalogue.resolve(request.observer_id) if request.observer_id else None
        if request.learning and active.brain != "functional":
            raise ValueError("Онлайн-память доступна для Functional Fly")
        config = CortexConfig(cortex=request.llm, model=request.model, decision_model=request.decision_model,
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

    def _execute(self, run_id: str, repo: Path | None, brain, config: CortexConfig, memory: Path | None) -> None:
        record, stop = self.records[run_id], self.stops[run_id]
        request = record.request
        sink = lambda kind, data: self.emit(run_id, kind, data)
        try:
            with self.lock:
                if not stop.is_set():
                    record.status = "running"
                self._save(record)
            cortex = (HeuristicCandidateGenerator() if request.llm == "heuristic" else make_lmstudio_cortex(
                model=request.model, decision_model=request.decision_model,
                base_url=config.base_url, timeout=config.request_timeout,
                max_model_calls=config.max_model_calls, on_event=sink, should_stop=stop.is_set,
            ))
            task = TaskSpec(id=run_id, task=request.task, fixture="toy_arithmetic_bug" if repo is None else None,
                            seed=request.seed, max_steps=request.max_steps,
                            max_tool_calls=request.max_tool_calls or request.max_steps)
            result = asyncio.run(run_agent(
                SoftwareEnvironment(repo or self.data_dir / run_id / "repository", seed=request.seed),
                brain, cortex, task,
                control_mode=ControlMode.HARD_CONTROL if request.execution_mode == "autonomous" else ControlMode.BIAS_CONTROL,
                autonomous_guard=request.execution_mode == "autonomous",
                telemetry=TelemetryLogger(self.data_dir / run_id / "telemetry"), run_id=run_id,
                on_event=sink, should_stop=stop.is_set,
            ))
            if memory is not None:
                brain.save_learning(memory)
                self.emit(run_id, "memory_saved", {"profile": request.memory_profile})
            with self.lock:
                record.result = result.model_dump(mode="json")
                record.status = "stopped" if stop.is_set() else "completed" if result.success else "incomplete"
        except Exception as exc:  # noqa: BLE001 - a worker must persist unexpected failures for the UI
            with self.lock:
                record.status = "stopped" if stop.is_set() else "failed"
                record.error = str(exc) if isinstance(exc, ValueError) else f"Run failed: {type(exc).__name__}"
        finally:
            brain.close()
            with self.lock:
                self._save(record)
                self.emit(run_id, "finished", record.model_dump(mode="json"))

    def close(self) -> None:
        with self.lock:
            for run_id, record in self.records.items():
                if record.status in ACTIVE:
                    self.stops[run_id].set()
