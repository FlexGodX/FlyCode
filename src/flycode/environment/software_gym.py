"""SoftwareGym - the software-engineering environment (plan §6-8).

`SoftwareEnvironment` wraps a repository on disk and exposes the
reset/observe/step contract every agent loop (baseline or fly-controlled)
drives. It never hands out the full repository; every consumer only ever
sees `Observation`.
"""

from pathlib import Path

from ..cognition import NoveltyModule, ValenceModule
from ..domain import Observation, ObservationDelta, TaskSpec, ToolAction, Transition
from ..tools import register_environment_tools, run_tool
from . import fixtures
from .observer import observe

MAX_LOG_LINES = 40
MAX_ERROR_LINES = 20
INFRA_TOOLS = frozenset(
    {
        "docker_compose",
        "docker_logs",
        "postgres_status",
        "postgres_query",
        "alembic",
        "http_request",
        "service_health",
    }
)


class SoftwareEnvironment:
    """Async environment over a single repository (plan §6)."""

    def __init__(self, repo_path: str | Path, seed: int = 0) -> None:
        self.repo_root = Path(repo_path)
        self.seed = seed
        self.task: TaskSpec | None = None
        self._valence = ValenceModule()
        self._novelty = NoveltyModule()
        self._reset_state()
        register_environment_tools()

    def _reset_state(self) -> None:
        self._step_number = 0
        self._tool_calls = 0
        self._build: dict = {}
        self._tests: dict = {}
        self._docker: dict = {}
        self._database: dict = {}
        self._services: dict = {}
        self._recent_logs: list[str] = []
        self._infra_errors: dict[str, str] = {}
        self._recent_errors: list[str] = []
        self._modified_files: set[str] = set()
        self._created_files: set[str] = set()
        self._deleted_files: set[str] = set()
        self._observation: Observation | None = None
        self._done = False

    # ------------------------------------------------------------ lifecycle
    async def reset(self, task: TaskSpec) -> Observation:
        self.task = task
        self._reset_state()
        self._valence.reset()
        self._novelty.reset()

        if task.fixture:
            fixture = fixtures.materialize(task.fixture, self.repo_root)
            if fixture.database_type != "none":
                self._database["required"] = True
            urls = [url for url in (fixture.health_check_url, fixture.api_endpoint) if url]
            if urls:
                self._services["required_urls"] = list(dict.fromkeys(urls))

        if any((self.repo_root / name).exists() for name in
               ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")):
            self._docker["required"] = True
        if (self.repo_root / "alembic.ini").exists():
            self._database["migration_required"] = True
        criteria = task.success_criteria
        if criteria.get("database"):
            self._database["required"] = True
        if criteria.get("service_urls"):
            self._services["required_urls"] = list(criteria["service_urls"])

        self._observation = observe(
            self.repo_root,
            task=task.task,
            task_id=task.id,
            step_number=self._step_number,
            resource_state=self._resource_state(),
            docker=self._docker,
            database=self._database,
            services=self._services,
        )
        return self._observation

    async def observe(self) -> Observation:
        if self._observation is None:
            raise RuntimeError("SoftwareEnvironment.observe() called before reset()")
        return self._observation

    async def step(self, action: ToolAction) -> Transition:
        if self._observation is None:
            raise RuntimeError("SoftwareEnvironment.step() called before reset()")
        previous = self._observation
        self._tool_calls += 1

        if action.tool == "stop":
            result_success, stdout, stderr, error_message, payload = True, "", "", "", {}
            self._done = True
        else:
            result = run_tool(action.tool, self.repo_root, **action.arguments)
            result_success, stdout, stderr = result.success, result.stdout, result.stderr
            error_message, payload = result.error_message, result.payload
            self._apply_side_effects(action, result_success, payload, error_message)

        self._step_number += 1
        current = observe(
            self.repo_root,
            task=previous.task,
            task_id=previous.task_id,
            step_number=self._step_number,
            build=self._build,
            tests=self._tests,
            docker=self._docker,
            database=self._database,
            services=self._services,
            recent_logs=list(self._recent_logs),
            recent_errors=list(self._recent_errors),
            resource_state=self._resource_state(),
            modified_files=sorted(self._modified_files),
            created_files=sorted(self._created_files),
            deleted_files=sorted(self._deleted_files),
        )
        self._observation = current

        signals = self._valence.evaluate(action, previous, current)
        novelty_state = self._novelty.evaluate(current)
        delta = self._compute_delta(previous, current, action)

        transition = Transition(
            previous=previous,
            action=action,
            current=current,
            utility=signals.utility,
            aversion=signals.aversion,
            novelty=novelty_state.novelty,
            information_gain=novelty_state.novelty,
            risk=action.expected_risk,
            cost=action.expected_cost,
            success=result_success,
            error_message=error_message,
            stdout=stdout,
            stderr=stderr,
            payload=payload,
            delta=delta,
            tool_call_index=self._tool_calls,
        )
        return transition

    def is_done(self) -> bool:
        return self._done

    # ------------------------------------------------------------ bookkeeping
    def _apply_side_effects(
        self, action: ToolAction, success: bool, payload: dict, error_message: str
    ) -> None:
        if action.tool == "run_tests":
            self._tests = dict(payload)
        elif action.tool == "create_file" and success:
            path = payload.get("path") or action.arguments.get("path")
            if path:
                self._created_files.add(path)
        elif action.tool == "edit_file" and success:
            path = payload.get("path") or action.arguments.get("path")
            if path:
                self._modified_files.add(path)
        elif action.tool == "docker_compose":
            if action.arguments.get("action", "ps") in ("up", "down", "restart", "stop"):
                self._tests = {**self._tests, "status": "unknown"}
                self._database.pop("connected", None)
                self._database.pop("migration_applied", None)
                self._services.pop("checked_urls", None)
                self._services.pop("app_running", None)
            self._docker = {
                **self._docker,
                "running": payload.get("running", False),
                "last_action": action.arguments.get("action", "ps"),
                # recovery.py reads a non-empty `error` as "container crashed"
                "error": error_message or "",
            }
        elif action.tool in ("postgres_status", "postgres_query"):
            self._database = {
                **self._database,
                "connected": payload.get("connected", success),
                "available": True,
            }
        elif action.tool == "alembic":
            self._database = {**self._database, "migration_failed": not success}
            if action.arguments.get("action", "current") == "upgrade":
                self._database["migration_applied"] = payload.get("migration_applied", False)
        elif action.tool in ("service_health", "http_request"):
            url = action.arguments.get("url", "")
            checked = {**self._services.get("checked_urls", {}), url: success}
            self._services = {**self._services, "checked_urls": checked,
                              "app_running": all(checked.values())}

        if success and action.tool in ("edit_file", "create_file"):
            self._tests = {**self._tests, "status": "unknown"}
            self._services.pop("checked_urls", None)
            self._services.pop("app_running", None)
            if self._docker.get("required"):
                self._docker.pop("running", None)
                self._docker.pop("last_action", None)
            if self._database.get("migration_required"):
                self._database.pop("migration_applied", None)

        log_line = f"{action.tool} -> {'ok' if success else 'error'}"
        self._recent_logs.append(log_line)
        self._recent_logs = self._recent_logs[-MAX_LOG_LINES:]

        # `recent_errors` reflects the observed system's health (infra/service
        # failures), not the agent's own tool-usage mistakes (a bad path, a
        # stale old_text) or test failures - those are already surfaced via
        # `tests`/`build`, so double-counting them here would make `is_healthy`
        # stick to False long after the underlying issue is fixed.
        #
        # Errors are tracked per tool so a later success for the *same* tool
        # clears its stale error: a repo whose Postgres was down on step 1 and
        # is up on step 7 must still be able to reach `is_healthy` - that is
        # exactly what the Phase 4 acceptance ("app fully running") asks for.
        if action.tool in INFRA_TOOLS:
            key = (f"http:{action.arguments.get('url', '')}"
                   if action.tool in ("http_request", "service_health") else action.tool)
            if success:
                self._infra_errors.pop(key, None)
            elif error_message:
                self._infra_errors[key] = error_message
            self._recent_errors = list(self._infra_errors.values())[-MAX_ERROR_LINES:]

    def _resource_state(self) -> dict:
        max_calls = self.task.max_tool_calls if self.task else None
        return {"tool_calls": self._tool_calls, "tool_call_limit": max_calls}

    @staticmethod
    def _compute_delta(previous: Observation, current: Observation, action: ToolAction) -> ObservationDelta:
        prev_failing = previous.failing_tests
        cur_failing = current.failing_tests
        touched = []
        path = action.arguments.get("path")
        if path and action.is_mutating:
            touched.append(path)

        new_errors = [e for e in current.recent_errors if e not in previous.recent_errors]
        resolved_errors = [e for e in previous.recent_errors if e not in current.recent_errors]

        return ObservationDelta(
            tests_status_changed=previous.tests_status != current.tests_status,
            build_status_changed=previous.build_status != current.build_status,
            new_failing_tests=max(0, cur_failing - prev_failing),
            fixed_tests=max(0, prev_failing - cur_failing),
            files_touched=touched,
            new_errors=new_errors,
            resolved_errors=resolved_errors,
        )
