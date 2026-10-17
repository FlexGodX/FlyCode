"""Charge registered verification subprocesses to their canonical review attempt."""

from pathlib import Path

from ..domain.verification import CheckAttempt, EvidenceManifest, VerificationVerdict
from ..verification.engine import VerificationEngine
from .sources import Sources
from .store import Store, StoreError, changed
from .tools import Tools


def accepted(engine: VerificationEngine, verdict: VerificationVerdict, root: Path, contract) -> bool:
    if (verdict.scope != 'acceptance-contract' or verdict.status != 'passed' or verdict.required_coverage != 1
            or not engine.current(verdict, root, contract=contract)):
        return False
    manifest = EvidenceManifest.model_validate_json((engine.data_dir / verdict.evidence_id / 'manifest.json').read_text())
    return manifest.complete


class CheckExecutor:
    def __init__(self, store: Store, scope, attempt_id, fence, *, source_claim_id=None):
        self.store, self.scope, self.attempt_id, self.fence = store, scope, attempt_id, fence
        self.pulse, self.uncertain = store.clock(), False
        self.source_claim_id = source_claim_id

    def should_stop(self):
        try:
            _, task, workflow = self.store._owned(self.scope, self.attempt_id, self.fence, running=True)
            if self.uncertain or task.paused or workflow.state != 'running' or self.store._counter('queue_paused'):
                return True
            if self.store.clock() - self.pulse >= 10:
                self.store.heartbeat(self.scope, self.attempt_id, self.fence)
                self.pulse = self.store.clock()
            return False
        except StoreError:
            return True

    def execute(self, check, number, invocation):
        tools = Tools(self.store)
        try:
            if self.should_stop():
                raise StoreError('cancelled')
            call = tools.begin_check(self.scope, self.attempt_id, self.fence, check, number)
        except StoreError as error:
            return CheckAttempt(number=number, argv=[], exit_code=None, duration=0, state='cancelled', cancelled=True,
                error_class='policy', reasons=[error.code])
        try:
            prepared = ({'on_prepare': lambda ref: Sources(self.store).bind_process(self.source_claim_id, ref)}
                if self.source_claim_id is not None else {})
            result = invocation(on_admit=lambda ref: tools.bind_process(self.scope, call.id, ref), **prepared)
        except BaseException as error:
            tools.finish(self.scope, call.id, status='uncertain', error_class=type(error).__name__)
            self.uncertain = True
            raise
        self.uncertain = (result.error_class in {'timeout', 'cleanup'}
            or (result.process_started and result.process_reason != 'completed'))
        tools.finish(self.scope, call.id, status='uncertain' if self.uncertain else
            'completed' if result.complete else 'failed', error_class=result.error_class)
        return result


def _succeed(store, scope, attempt, task, workflow, *, evidence_id):
    """Called inside the coordinating gate's transaction after validating its evidence."""
    if not store.db.in_transaction:
        raise RuntimeError('Completion state changes must be atomic')
    for table in ('model_calls', 'tool_calls'):
        if store.db.execute(f"SELECT 1 FROM {table} WHERE attempt_id=? AND state IN ('running','uncertain')",
                            (attempt.id,)).fetchone():
            raise StoreError('resource_busy')
    store._save_attempt(changed(attempt, state='succeeded', stop_confirmed=True))
    store._save_task(changed(task, state='succeeded', current_attempt_id=None, fence=task.fence + 1,
        revision=task.revision + 1, updated_at=store.clock()))
    instance = store._instance(attempt.instance_id)
    if instance.current_attempt_id == attempt.id:
        store._save_instance(changed(instance, state='idle', current_attempt_id=None, revision=instance.revision + 1))
    store.db.execute('UPDATE reservations SET model_reserved=0,tool_reserved=0,uncertain=0 WHERE attempt_id=?', (attempt.id,))
    store._event(scope, workflow, 'verified_task_completed', task=task, attempt=attempt, evidence_id=evidence_id)
