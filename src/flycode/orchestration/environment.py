"""Canonical local execution adapter; host code supplies its private workspace."""

from pathlib import Path

from ..domain import TaskSpec, ToolAction
from ..environment import SoftwareEnvironment
from .store import Store, StoreError
from .tools import Tools


class AttemptEnvironment(SoftwareEnvironment):
    def __init__(self, root: Path, store: Store, scope, attempt_id: str, fence: int):
        self.store, self.scope, self.attempt_id, self.fence = store, scope, attempt_id, fence
        _, task, _ = store._owned(scope, attempt_id, fence, running=True)
        if task.permissions is None or task.permissions.read_scopes != ('.',):
            raise StoreError('permission', 'Scoped observations are not implemented; full read permission is required')
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
        call, action = self.tools.begin(self.scope, self.attempt_id, self.fence, root=self.repo_root, action=action)
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
