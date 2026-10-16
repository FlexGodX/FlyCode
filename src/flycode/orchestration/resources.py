"""Durable endpoint admission and per-request accounting, outside inference."""

import time
from collections.abc import Callable
from typing import TypeVar
from uuid import uuid4

from openai import APIStatusError
from pydantic import ValidationError

from ..domain.orchestration import (
    AccessScope,
    BudgetAmount,
    ModelAdmission,
    ModelCall,
    ModelUsage,
)
from .store import Store, StoreError, changed

T = TypeVar('T')


class Resources:
    def __init__(self, store: Store):
        self.store = store

    def get(self, scope: AccessScope, identity: str) -> ModelCall:
        row = self.store.db.execute('SELECT attempt_id FROM model_calls WHERE id=?', (identity,)).fetchone()
        if row is not None and row['attempt_id'] is None:
            raise StoreError('scope', 'Host request is not a workflow model call')
        call = self.store._load('model_calls', identity, ModelCall)
        self.store.workflow(scope, call.workflow_id)
        return call

    def calls(self, scope: AccessScope, workflow_id: str) -> list[ModelCall]:
        self.store.workflow(scope, workflow_id)
        return [self.get(scope, row['id']) for row in self.store.db.execute('''SELECT model_calls.id FROM model_calls
            JOIN attempts ON attempts.id=attempt_id WHERE workflow_id=? ORDER BY model_calls.rowid''', (workflow_id,))]

    def begin(self, scope: AccessScope, attempt_id: str, fence: int, *, key: str) -> ModelAdmission | None:
        store = self.store
        store._role(scope, ('runtime',))
        with store.transaction():
            task = store.task(scope, store._attempt(attempt_id).task_id)
            found, result, digest = store._replay(scope, key, {'model_call': attempt_id, 'fence': fence})
            if found:
                return ModelAdmission(call=self.get(scope, result['id']), fresh=False)
            attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
            if task.paused or workflow.state != 'running' or store._counter('queue_paused'):
                raise StoreError('paused')
            profile = attempt.profile_snapshot
            if not store._binding_current(profile):
                raise StoreError('model_unavailable', 'The approved model binding changed')
            if store.db.execute("SELECT 1 FROM model_calls WHERE endpoint_ref=? AND state IN ('running','uncertain')",
                                (profile.endpoint_ref,)).fetchone():
                return None
            call = ModelCall(id=uuid4().hex, workflow_id=workflow.id, task_id=task.id, attempt_id=attempt.id,
                instance_id=attempt.instance_id, fence=fence, endpoint_ref=profile.endpoint_ref,
                model_id=profile.loaded_instance_id or profile.model_id,
                role='scorer' if profile.role == 'decider' else 'primary', started_at=store.clock())
            store._debit(scope, attempt, task, workflow, BudgetAmount(model_calls=1))
            store.db.execute('INSERT INTO model_calls VALUES (?,?,?,?,?)',
                             (call.id, attempt.id, call.endpoint_ref, call.status, call.model_dump_json()))
            store._event(scope, workflow, 'model_start', task=task, attempt=attempt, call=call.model_dump(mode='json'))
            store._remember(scope, key, task.project_id, digest, call)
            return ModelAdmission(call=call, fresh=True)

    def finish(self, scope: AccessScope, identity: str, *, status: str, error_class: str | None = None,
               usage: ModelUsage | None = None) -> ModelCall:
        """Host transport acknowledgement, never an agent-supplied completion flag."""
        store = self.store
        store._role(scope, ('runtime',))
        if status not in {'completed', 'failed', 'uncertain'}:
            raise StoreError('model_status')
        with store.transaction():
            call = self.get(scope, identity)
            if call.status != 'running':
                if (call.status, call.error_class, call.usage) == (status, error_class, usage):
                    return call
                if call.status != 'uncertain' or status not in {'completed', 'failed'}:
                    raise StoreError('reconcile_required', 'A terminal transport outcome cannot be overwritten')
            try:
                attempt, task, workflow = store._owned(scope, call.attempt_id, call.fence, running=True)
                late = False
            except StoreError as error:
                if error.code not in {'fence', 'wall_deadline'}:
                    raise
                attempt = store._attempt(call.attempt_id)
                task, workflow = store.task(scope, call.task_id), store.workflow(scope, call.workflow_id)
                late = True
            call = changed(call, status=status, error_class=error_class, usage=usage, late=late,
                           ended_at=store.clock() if status != 'uncertain' else None)
            store.db.execute('UPDATE model_calls SET state=?,data=? WHERE id=?', (status, call.model_dump_json(), identity))
            if status == 'uncertain':
                store.db.execute('UPDATE reservations SET uncertain=1 WHERE attempt_id=?', (call.attempt_id,))
            store._event(scope, workflow, 'model_uncertain' if status == 'uncertain' else 'model_end',
                         task=task, attempt=attempt, call=call.model_dump(mode='json'))
            return call

    def execute(self, scope: AccessScope, attempt_id: str, fence: int, *, key: str, request: Callable[[], T],
                should_stop: Callable[[], bool] = lambda: False) -> T:
        """Blocking transport adapter; the host runs it outside its serving loop."""
        waited = False
        while True:
            if should_stop():
                raise InterruptedError('Model request cancelled before admission')
            admission = self.begin(scope, attempt_id, fence, key=key)
            if admission is not None:
                break
            if not waited:
                with self.store.transaction():
                    attempt, task, workflow = self.store._owned(scope, attempt_id, fence, running=True)
                    self.store._event(scope, workflow, 'model_wait', task=task, attempt=attempt)
                waited = True
            time.sleep(.05)
        if not admission.fresh:
            raise StoreError('request_replay', 'The recorded request must not be sent again')
        try:
            result = request()
        except APIStatusError as error:
            self.finish(scope, admission.call.id, status='failed', error_class=type(error).__name__)
            raise
        except BaseException as error:
            self.finish(scope, admission.call.id, status='uncertain', error_class=type(error).__name__)
            raise
        raw_usage = getattr(result, 'usage', None)
        try:
            usage = ModelUsage(**{name: getattr(raw_usage, name, None) for name in ModelUsage.model_fields}) if raw_usage else None
        except ValidationError:
            self.finish(scope, admission.call.id, status='failed', error_class='UsageValidationError')
            raise
        call = self.finish(scope, admission.call.id, status='completed', usage=usage)
        if call.late:
            raise StoreError('fence', 'Late provider response retained for audit only')
        return result
