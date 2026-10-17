"""Canonical local execution adapter; host code supplies its private workspace."""

from collections.abc import Callable
from pathlib import Path

from ..domain import TaskSpec, ToolAction
from ..environment import SoftwareEnvironment
from ..tools.filesystem import ToolResult
from .inputs import Inputs
from .store import Store, StoreError
from .tools import Tools


class AttemptEnvironment(SoftwareEnvironment):
    def __init__(self, root: Path, store: Store, scope, attempt_id: str, fence: int, *,
                 message_executor: Callable[[ToolAction], ToolResult] | None = None,
                 artifact_executor: Callable[[ToolAction], ToolResult] | None = None):
        self.store, self.scope, self.attempt_id, self.fence = store, scope, attempt_id, fence
        self.message_executor = message_executor
        self.artifact_executor = artifact_executor
        self._blocked_result: ToolResult | None = None
        _, task, _ = store._owned(scope, attempt_id, fence, running=True)
        if task.permissions is None or task.permissions.read_scopes != ('.',):
            raise StoreError('permission', 'Scoped observations are not implemented; full read permission is required')
        self.resolving = Inputs(store).conflict(scope, task) is not None
        self.tools = Tools(store)
        super().__init__(Path(root).resolve())

    async def reset(self, task: TaskSpec):
        if task.fixture:
            raise StoreError('permission', 'Canonical execution cannot rematerialize a fixture')
        _, canonical, _ = self.store._owned(self.scope, self.attempt_id, self.fence, running=True)
        return await super().reset(task.model_copy(update={'id': canonical.id, 'task': canonical.objective}))

    async def step(self, action: ToolAction):
        if self._observation is None:
            raise RuntimeError('AttemptEnvironment.step() called before reset()')
        if action.tool == 'stop':
            # Local control has no external effect and remains available after budget exhaustion.
            return await super().step(action)
        if action.tool == 'send_message' or (action.tool == 'read_file' and (self.resolving or 'artifact_id' in action.arguments)):
            self.store._owned(self.scope, self.attempt_id, self.fence, running=True)
            # The owning host admits and debits this action before publishing over RPC.
            return await super().step(action)
        try:
            call, action = self.tools.begin(self.scope, self.attempt_id, self.fence, root=self.repo_root, action=action)
        except StoreError as error:
            if error.code != 'resolution_inputs_required':
                raise
            self._blocked_result = ToolResult(success=False, error_message=str(error), payload={'blocked': error.code})
            try:
                return await super().step(action)
            finally:
                self._blocked_result = None
        try:
            result = await super().step(action)
        except BaseException as error:
            self.tools.finish(self.scope, call.id, status='uncertain', error_class=type(error).__name__)
            raise
        if action.tool == 'run_tests' and result.payload.get('status') == 'timeout':
            self.tools.finish(self.scope, call.id, status='uncertain', error_class='ToolTimeout')
            raise StoreError('reconcile_required', 'Pytest timeout does not prove descendant processes stopped')
        completed = self.tools.finish(self.scope, call.id, status='completed' if result.success else 'failed')
        if completed.late:
            raise StoreError('fence', 'Late tool result retained for audit only')
        return result

    def _run_tool(self, action: ToolAction) -> ToolResult:
        if self._blocked_result is not None:
            return self._blocked_result
        if action.tool == 'read_file' and (self.resolving or 'artifact_id' in action.arguments):
            if self.artifact_executor is None:
                return ToolResult(success=False, error_message='artifact_route')
            return self.artifact_executor(action)
        if action.tool == 'send_message':
            if self.message_executor is None:
                return ToolResult(success=False, error_message='message_route')
            return self.message_executor(action)
        return super()._run_tool(action)
