"""Scoped verification views and bounded, hash-bound report downloads."""

import hashlib
import re
from pathlib import Path

from ..domain.orchestration import AccessScope
from ..domain.verification import (
    AcceptanceContract,
    CaseResult,
    CheckSpec,
    EvidenceManifest,
    VerificationModel,
    VerificationVerdict,
)
from ..domain.verification_control import (
    RegisteredVerificationPlan,
    RegisteredVerificationProfile,
    VerificationBudget,
    VerificationExecution,
)
from ..orchestration.projects import Projects
from ..orchestration.store import StoreError
from .control import VerificationControl
from .engine import VerificationEngine
from .evidence import read_regular
from .oracle import OracleRegistry
from .registry import VerificationRegistry
from .runner import OUTPUT_LIMIT, REPORT_LIMIT
from .snapshot import capture, digest


class CheckView(VerificationModel):
    check_id: str
    number: int
    state: str
    late: bool
    duration: float | None
    error_class: str | None
    complete: bool
    cases: list[CaseResult]


class ExecutionView(VerificationModel):
    id: str
    number: int
    state: str
    started_at: float | None
    ended_at: float | None
    current: bool
    error: str | None
    checks: list[CheckView]
    verdict: VerificationVerdict | None


class ArtifactView(VerificationModel):
    id: str
    execution_id: str
    evidence_id: str
    name: str
    sha256: str


class PlanView(VerificationModel):
    id: str
    project_id: str
    workflow_id: str | None
    task_id: str | None
    acceptance: AcceptanceContract | None
    revision: int
    state: str
    title: str
    profile_id: str
    profile_version: int
    subject_ids: dict[str, str]
    subject_hash: str
    scope: str
    checks: list[CheckSpec]
    reruns: int
    budget: VerificationBudget
    spent: int
    reserved: int
    deadline_at: float | None
    current: bool
    verdict: VerificationVerdict | None
    blockers: list[str]
    attempts: list[ExecutionView]
    artifacts: list[ArtifactView]
    cursor: int


class PlanPage(VerificationModel):
    items: list[PlanView]
    next_cursor: int | None


class StopHandle(VerificationModel):
    plan_id: str
    revision: int
    state: str


class StopPage(VerificationModel):
    items: list[StopHandle]
    next_cursor: int | None


class CommandReceipt(VerificationModel):
    id: str
    plan_id: str
    state: str
    number: int | None = None


class VerificationViews:
    def __init__(self, store):
        self.store = store
        self.control, self.registry = VerificationControl(store), VerificationRegistry(store)

    def executions(self, record):
        return [VerificationExecution.model_validate_json(row['data']) for row in self.store.db.execute(
            'SELECT data FROM verification_executions WHERE plan_id=? ORDER BY number', (record.id,))]

    def stops(self, actor, *, after=0, limit=50):
        if not 0 <= after <= 2**63 - 1 or not 1 <= limit <= 100:
            raise StoreError('cursor')
        rows = self.store.db.execute('''SELECT p.rowid AS cursor,p.data,g.data AS grant_data
            FROM verification_plans p JOIN project_grants g ON g.project_id=p.project_id
            WHERE g.actor_id=? AND p.rowid>? AND json_extract(p.data,'$.state') IN
            ('queued','preparing','running','evaluating','cancelling','reconciling')
            ORDER BY p.rowid LIMIT ?''', (actor, after, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            record = RegisteredVerificationPlan.model_validate_json(row['data'])
            Projects(self.store)._check_stop_grant(actor, record.project_id, row['grant_data'])
            items.append(StopHandle(plan_id=record.id, revision=record.revision, state=record.state))
        return StopPage(items=items, next_cursor=rows[limit - 1]['cursor'] if len(rows) > limit else None)

    def _bundle(self, scope, record, execution):
        project = self.registry._project(scope, record.project_id)
        storage = self.registry._storage(project)
        if execution.coordinator_root is None or execution.verdict is None:
            raise StoreError('not_found')
        root = Path(execution.coordinator_root.path)
        if not root.is_relative_to(Path(storage.path) / 'executions'):
            raise StoreError('verification_storage_changed')
        self.registry._stage_current(root, execution.coordinator_root)
        directory = self.registry._directory(execution.coordinator_root, 'evidence', execution.verdict.evidence_id)
        content = read_regular(directory / 'manifest.json')
        if hashlib.sha256(content).hexdigest() != execution.verdict.evidence_hash:
            raise StoreError('evidence_changed')
        return directory, EvidenceManifest.model_validate_json(content)

    def _artifacts(self, execution, manifest):
        return [ArtifactView(id=digest({'evidence': execution.verdict.evidence_id, 'name': name}),
            execution_id=execution.id, evidence_id=execution.verdict.evidence_id, name=name, sha256=sha)
            for name, sha in manifest.artifacts.items() if re.fullmatch(
                r'attempt-(?:[0-9]+|[0-9a-f]{32})/(?:stdout\.txt|stderr\.txt|junit\.xml|cases\.jsonl)', name)]

    def plan(self, scope, identity):
        self.control.get(scope, identity)
        # Keep metadata and its event cursor at one SQL revision. Filesystem
        # freshness checks run only after this short transaction has ended.
        with self.store.transaction():
            record = self.control._record(scope, identity)
            profile = self.store._load('verification_profiles', record.profile_id, RegisteredVerificationProfile)
            executions = self.executions(record)
            ledger = self.store.db.execute('''SELECT COALESCE(SUM(tool_spent),0),COALESCE(SUM(tool_reserved),0)
                FROM verification_reservations JOIN verification_executions ON id=execution_id WHERE plan_id=?''',
                (record.id,)).fetchone()
            cursor = self.store.db.execute('SELECT COALESCE(MAX(sequence),0) FROM verification_events WHERE plan_id=?',
                (record.id,)).fetchone()[0]
        current, blockers, artifacts = False, [], []
        latest = next((item for item in executions if item.id == record.current_execution_id), None)
        for execution in executions:
            if execution.verdict is None:
                continue
            try:
                _, manifest = self._bundle(scope, record, execution)
                artifacts.extend(self._artifacts(execution, manifest))
            except (OSError, ValueError):
                if execution == latest:
                    blockers.append('evidence_changed')
        if latest is not None and latest.current and latest.verdict is not None:
            try:
                project, trusted_profile = self.control._preflight(scope, record, owner=False)
                directory, _ = self._bundle(scope, record, latest)
                engine = VerificationEngine(directory.parent)
                if trusted_profile.approval_hash is not None:
                    storage = self.registry._storage(project)
                    engine.oracles = OracleRegistry(self.registry._directory(storage, 'oracles', project.id, profile.id))
                stage = 'integration' if 'integration' in record.subject_ids else 'target'
                subject = self.registry.subject(scope, record.subject_ids[stage])
                root = self.registry.subject_root(scope, subject.id)
                current = engine.current(latest.verdict, root, contract=record.plan.acceptance, captured=True)
                if not current:
                    blockers.append('evidence_changed')
                source = capture(Path(project.root.path))
                snapshot = subject.snapshot
                base = subject.origin.source_base if subject.origin is not None else snapshot.base
                if (source.files != snapshot.files or source.modes != snapshot.modes or source.base != base
                        or digest(source.environment) != digest(snapshot.environment)):
                    current = False
                    blockers.append('subject_changed')
            except (OSError, ValueError) as error:
                current = False
                blockers.append(getattr(error, 'code', 'inputs_unavailable'))
        if latest is not None and latest.error:
            blockers.append(latest.error)
        return PlanView(id=record.id, project_id=record.project_id, revision=record.revision, state=record.state,
            workflow_id=record.workflow_id, task_id=record.task_id, acceptance=record.plan.acceptance,
            title=profile.draft.title, profile_id=record.profile_id, profile_version=record.profile_version,
            subject_ids=record.subject_ids, subject_hash=record.plan.subject_hash, scope=record.plan.scope,
            checks=record.plan.checks, reruns=record.plan.reruns, budget=record.budget,
            spent=ledger[0], reserved=ledger[1], deadline_at=record.deadline_at, current=current,
            verdict=latest.verdict if latest else None, blockers=sorted(set(blockers)), artifacts=artifacts, cursor=cursor,
            attempts=[ExecutionView(id=item.id, number=item.number, state=item.state,
                started_at=item.started_at, ended_at=item.ended_at, current=current if item == latest else False,
                error=item.error, verdict=item.verdict, checks=[CheckView(check_id=call.check_id, number=call.number,
                    state=call.state, late=call.late, duration=call.result.duration if call.result else None,
                    error_class=call.result.error_class if call.result else None, complete=bool(call.result and call.result.complete),
                    cases=call.result.cases if call.result else []) for call in item.invocations]) for item in executions])

    def page(self, scope, *, after=0, limit=50):
        if not 0 <= after <= 2**63 - 1 or not 1 <= limit <= 100:
            raise StoreError('cursor')
        projects = sorted(scope.project_ids)
        if not projects:
            return PlanPage(items=[], next_cursor=None)
        marks = ','.join('?' for _ in projects)
        rows = self.store.db.execute(f'SELECT rowid,id FROM verification_plans WHERE rowid>? AND '
            f'project_id IN ({marks}) ORDER BY rowid LIMIT ?', (after, *projects, limit + 1)).fetchall()
        return PlanPage(items=[self.plan(scope, row['id']) for row in rows[:limit]],
            next_cursor=rows[limit - 1]['rowid'] if len(rows) > limit else None)

    def artifact(self, scope, identity, artifact_id):
        record = self.control.get(scope, identity)
        unavailable = False
        for execution in self.executions(record):
            if execution.verdict is None:
                continue
            try:
                directory, manifest = self._bundle(scope, record, execution)
            except (OSError, ValueError):
                unavailable = True
                continue
            for artifact in self._artifacts(execution, manifest):
                if artifact.id != artifact_id:
                    continue
                parent, name = artifact.name.split('/')
                self.registry._directory(execution.coordinator_root, 'evidence', artifact.evidence_id, parent)
                limit = OUTPUT_LIMIT if name.endswith('.txt') else REPORT_LIMIT
                content = read_regular(directory / parent / name, limit=limit)
                if hashlib.sha256(content).hexdigest() != artifact.sha256:
                    raise StoreError('evidence_changed')
                self.registry._stage_current(Path(execution.coordinator_root.path), execution.coordinator_root)
                return artifact, content
        raise StoreError('evidence_changed' if unavailable else 'not_found')

    def reproduction(self, scope, evidence_id):
        projects = sorted(scope.project_ids)
        if not projects:
            raise StoreError('scope')
        marks = ','.join('?' for _ in projects)
        row = self.store.db.execute(f'''SELECT e.data FROM verification_executions e
            JOIN verification_plans p ON p.id=e.plan_id WHERE p.project_id IN ({marks})
            AND json_extract(e.data,'$.verdict.evidence_id')=?''', (*projects, evidence_id)).fetchone()
        if row is None:
            raise StoreError('not_found')
        execution = VerificationExecution.model_validate_json(row['data'])
        record = self.control.get(scope, execution.plan_id)
        project, profile = self.control._preflight(scope, record, owner=False)
        try:
            directory, _ = self._bundle(scope, record, execution)
            engine = VerificationEngine(directory.parent)
            if profile.approval_hash is not None:
                storage = self.registry._storage(project)
                engine.oracles = OracleRegistry(self.registry._directory(storage, 'oracles', project.id, profile.id))
            stage = 'integration' if 'integration' in record.subject_ids else 'target'
            root = self.registry.subject_root(scope, record.subject_ids[stage])
            if not engine.current(execution.verdict, root, contract=record.plan.acceptance, captured=True):
                raise StoreError('evidence_changed')
        except (OSError, ValueError) as error:
            raise StoreError('evidence_changed') from error
        return record.id

    def cancel(self, actor, identity, command):
        if command.operation != 'cancel':
            raise StoreError('permission')
        record = self.store._load('verification_plans', identity, RegisteredVerificationPlan)
        row = self.store.db.execute('SELECT data FROM project_grants WHERE actor_id=? AND project_id=?',
            (actor, record.project_id)).fetchone()
        if row is None:
            raise StoreError('scope')
        Projects(self.store)._check_stop_grant(actor, record.project_id, row['data'])
        scope = AccessScope(actor_id=actor, project_ids={record.project_id}, role='owner')
        return self.control.command(scope, identity, command)


def command_receipt(result):
    return CommandReceipt(id=result.id, plan_id=getattr(result, 'plan_id', result.id),
        state=result.state, number=getattr(result, 'number', None))
