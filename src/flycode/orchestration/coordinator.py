"""One physical database host; blocking stages never own its control loop."""

import argparse
import hashlib
import json
import signal
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from ..domain.orchestration import AccessScope, WorkerBinding
from ..llm.catalogue import ModelCatalogue
from ..verification.acceptance import input_identity
from ..verification.control import VerificationControl
from ..verification.evidence import read_regular
from ..verification.execution import VerificationExecutor
from .attach import AttachmentServer
from .dispatch import Dispatcher
from .host import Host
from .integration import Integration
from .projects import Projects
from .review import Reviews
from .runtime import Runtime
from .sources import Sources
from .store import StoreError, changed, default_state_path


class Coordinator:
    def __init__(self, host: Host):
        self.host = host

    def _connect(self):
        return self.host.connect(catalogue=ModelCatalogue(database=self.host.database))

    def _scope(self, store, *, current):
        projects = set()
        if current:
            registry = Projects(store)
            for row in store.db.execute("SELECT id FROM canonical_projects WHERE state='active'").fetchall():
                try:
                    project = registry.get(row['id'])
                    if registry.resolve(Path(project.root.path)).id == project.id:
                        projects.add(project.id)
                except (OSError, ValueError):
                    continue
        else:
            return self.host._scope(store)
        return AccessScope(actor_id='managed-host:' + self.host.session.id, project_ids=projects, role='runtime')

    def _inputs(self, store, scope):
        tables = ('workflows', 'tasks', 'profiles', 'instances', 'reservations', 'worker_bindings',
            'review_bindings', 'pipelines', 'pipeline_receipts', 'artifacts', 'reviews', 'integrations',
            'model_admissions', 'model_calls', 'tool_calls', 'canonical_projects', 'source_claims',
            'workers', 'review_executions', 'execution_lanes', 'verification_plans', 'verification_subjects',
            'verification_profiles', 'verification_profile_heads', 'verification_storage', 'verification_reservations')
        data = {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
            for table in tables}
        data['queue_paused'] = store._counter('queue_paused')
        data['scope'] = sorted(scope.project_ids)
        data['attempts'] = [json.loads(row[0]) for row in store.db.execute('SELECT data FROM attempts ORDER BY rowid')]
        for attempt in data['attempts']:
            attempt.pop('lease_until', None)
        data['verification_executions'] = [json.loads(row[0]) for row in store.db.execute(
            'SELECT data FROM verification_executions ORDER BY rowid')]
        for execution in data['verification_executions']:
            execution.pop('lease_until', None)
        roots, sources = {self.host.registration.runtime_root.path}, set()
        for table in ('worker_bindings', 'review_bindings', 'pipelines'):
            for row in data[table]:
                binding = json.loads(row[-1])
                if binding['project_id'] in scope.project_ids:
                    roots.add(binding['runtime_root'])
                    if 'source_root' in binding:
                        sources.add(binding['source_root'])
        paths = {Path(source) for source in sources}
        for root in roots:
            paths.update((Path(root) / 'verification/oracles', Path(root) / 'artifacts'))
        data['files'] = {}
        for path in sorted(paths):
            try:
                data['files'][str(path)] = input_identity(path)
            except (OSError, ValueError) as error:
                data['files'][str(path)] = type(error).__name__
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(), sorted(roots)

    def _select(self, previous):
        store = self._connect()
        try:
            scope = self._scope(store, current=True)
            signature, roots = self._inputs(store, scope)
            if signature != previous:
                for root in roots if store.workflows(scope) else []:
                    key = hashlib.sha256((signature + ':' + root).encode()).hexdigest()
                    result = Dispatcher(store, Path(root)).select(scope, key='host-select:' + key)
                    if result.attempt is not None:
                        return signature, (root, scope, result.attempt.id, 'agent')
                control = VerificationControl(store)
                for row in store.db.execute("SELECT e.id,p.project_id FROM verification_executions e "
                        "JOIN verification_plans p ON p.id=e.plan_id WHERE e.state='queued' ORDER BY e.rowid").fetchall():
                    if row['project_id'] not in scope.project_ids:
                        continue
                    try:
                        execution = control.claim(scope, row['id'])
                    except (OSError, ValueError) as error:
                        if isinstance(error, StoreError) and error.code.startswith('host_'):
                            raise
                        reason = getattr(error, 'code', type(error).__name__)
                        if reason not in {'instance_limit', 'paused', 'revision', 'verification_state'}:
                            control.block_queued(scope, row['id'], reason)
                        continue
                    return signature, (None, scope, execution.id, 'verification')
            return signature, None
        finally:
            store.close()

    def _execute(self, root, scope, attempt_id, kind='agent'):
        store = self._connect()
        if kind == 'verification':
            try:
                execution = VerificationControl(store)._execution(attempt_id)
                VerificationExecutor(store, attempt_id, execution.fence).run()
            except (OSError, ValueError):
                # The executor retains its failed preparation/custody record;
                # reconciliation, rather than a fresh run, owns the next step.
                pass
            finally:
                store.close()
            return
        dispatcher = Dispatcher(store, Path(root))
        try:
            dispatcher.launch(scope, attempt_id)
            for handle in dispatcher.handles.values():
                handle.finished.wait()
        finally:
            for handle in dispatcher.handles.values():
                handle.close()
            store.close()

    def _pulse(self, store, scope):
        with store.transaction():
            self.host.validate_sql(store)
            session = changed(self.host.session, heartbeat_at=store.clock())
            store.db.execute('UPDATE host_sessions SET data=? WHERE id=1', (session.model_dump_json(),))
            self.host.session = session
        for workflow in store.workflows(scope):
            for attempt in store.attempts(scope, workflow.id):
                if attempt.state in {'assigned', 'running'}:
                    try:
                        store.heartbeat(scope, attempt.id, attempt.fence)
                    except StoreError as error:
                        if error.code not in {'fence', 'wall_deadline'}:
                            raise
        control = VerificationControl(store)
        for row in store.db.execute("SELECT id FROM verification_executions WHERE state IN "
                "('preparing','running','evaluating')").fetchall():
            execution = control._execution(row['id'])
            try:
                control.heartbeat(scope, execution.id, execution.fence)
            except StoreError as error:
                if error.code.startswith('host_'):
                    raise

    def _recovery_state(self, store, identity, kind='agent'):
        data, paths = {}, set()
        def references(value):
            if isinstance(value, dict):
                if value.get('version') in {'pytest-custody-v1', 'worker-custody-v1'}:
                    paths.update(Path(value['directory']) / name for name in ('process.json', 'process.lock'))
                for item in value.values():
                    references(item)
            elif isinstance(value, list):
                for item in value:
                    references(item)
        tables = (('verification_executions', 'id'), ('verification_reservations', 'execution_id')) if kind == 'verification' else (
                ('attempts', 'id'), ('reservations', 'attempt_id'), ('workers', 'attempt_id'),
                ('review_executions', 'id'), ('integrations', 'attempt_id'), ('workspaces', 'attempt_id'),
                ('model_calls', 'attempt_id'), ('tool_calls', 'attempt_id'), ('execution_lanes', 'attempt_id'))
        for table, column in tables:
            rows = [dict(row) for row in store.db.execute('SELECT * FROM ' + table + ' WHERE ' + column + '=?', (identity,))]
            data[table] = rows
            for row in rows:
                if 'data' in row:
                    record = json.loads(row['data'])
                    if table == 'workers':
                        record.pop('stopped_at', None)
                        row['data'] = json.dumps(record, sort_keys=True)
                    references(record)
                    if table == 'review_executions':
                        paths.add(Path(record['runtime_root']) / 'reviews' / identity / 'coordinator.lock')
                    elif table == 'integrations' and record.get('runtime_root'):
                        paths.add(Path(record['runtime_root']) / 'integrations' / record['id'] / 'coordinator.lock')
                    elif table == 'verification_executions' and record.get('coordinator_root'):
                        root = Path(record['coordinator_root']['path'])
                        paths.update((root, root / 'coordinator.lock'))
                        paths.update(root.parents)
        if kind == 'agent':
            attempt = store._attempt(identity)
            data['claims'] = [tuple(row) for row in store.db.execute(
                "SELECT * FROM source_claims WHERE json_extract(data,'$.attempt_id')=?", (identity,))]
            data['task'] = store._task(attempt.task_id).model_dump(mode='json')
        data['files'] = {}
        for path in sorted(paths):
            try:
                info = path.lstat()
                data['files'][str(path)] = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    hashlib.sha256(read_regular(path, limit=64 * 1024)).hexdigest()
                    if path.name == 'process.json' and stat.S_ISREG(info.st_mode) else None)
            except (OSError, ValueError) as error:
                data['files'][str(path)] = type(error).__name__
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def _recover(self, identity, kind='agent'):
        store = self._connect()
        try:
            observed = self._recovery_state(store, identity, kind)
            if kind == 'verification':
                execution = VerificationControl(store)._execution(identity)
                try:
                    VerificationExecutor(store, identity, execution.fence).recover()
                    return observed, False
                except StoreError as error:
                    if error.code.startswith('host_'):
                        raise
                    VerificationControl(store).recovery_blocked(self._scope(store, current=False),
                        identity, execution.fence, error.code)
                    return observed, error.code in {'coordinator_running', 'process_unconfirmed', 'revision'}
            retry = False
            scope = self._scope(store, current=False)
            attempt = store._attempt(identity)
            try:
                integration = store.db.execute('SELECT id,data FROM integrations WHERE attempt_id=?', (identity,)).fetchone()
                review = store.db.execute('SELECT data FROM review_executions WHERE id=?', (identity,)).fetchone()
                if integration is not None:
                    root = json.loads(integration['data']).get('runtime_root')
                    if not root:
                        raise StoreError('ownership_unknown')
                    result = Integration(store, Path(root)).reconcile(scope, integration['id'])
                    retry = not store._attempt(identity).stop_confirmed and result.error != 'effects_unknown'
                    if store._attempt(identity).stop_confirmed and result.source_claim_id is not None:
                        Sources(store).settle(result.source_claim_id)
                elif review is not None:
                    result = Reviews(store, Path(json.loads(review['data'])['runtime_root'])).reconcile(scope, identity)
                    retry = not store._attempt(identity).stop_confirmed and result.error != 'effects_unknown'
                elif store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (identity,)).fetchone():
                    if attempt.execution_binding_id is None:
                        raise StoreError('ownership_unknown')
                    binding = store._load('worker_bindings', attempt.execution_binding_id, WorkerBinding)
                    Runtime(store, Path(binding.runtime_root)).reconcile(scope, identity)
                elif store.db.execute('SELECT 1 FROM execution_lanes WHERE attempt_id=?', (identity,)).fetchone():
                    raise StoreError('ownership_unknown')
                else:
                    task = store.task(scope, attempt.task_id)
                    workflow = store.workflow(scope, attempt.workflow_id)
                    store.confirm_stopped(scope, identity, attempt.fence,
                        outcome='retry_wait' if task.paused or workflow.state == 'pausing' else 'failed',
                        expenditure_confirmed=True)
            except StoreError as error:
                if error.code.startswith('host_'):
                    raise
                retry = error.code not in {'resource_busy', 'uncertain'}
                with store.transaction():
                    task = store.task(scope, attempt.task_id)
                    request = {'attempt': identity, 'observed': observed, 'reason': error.code}
                    key = 'host-recovery:' + hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
                    found, _, digest = store._replay(scope, key, request)
                    if not found:
                        store._event(scope, store.workflow(scope, attempt.workflow_id), 'reconciliation_blocked',
                            task=task, attempt=attempt, reason=error.code)
                        store._remember(scope, key, task.project_id, digest, request)
            return observed, retry
        finally:
            store.close()

    def run(self, should_stop=lambda: False):
        store = self._connect()
        admission_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='admission')
        stage_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='stage')
        stages, recoveries, reconciled, admission, signature = {}, {}, {}, None, None
        pulse, checked = 0., 0.
        try:
            while not should_stop():
                self.host.check()
                scope = self._scope(store, current=False)
                store.expire(scope)
                VerificationControl(store).expire(scope)
                now = time.monotonic()
                if now - pulse >= 10:
                    self._pulse(store, scope)
                    pulse = now
                for identity, future in list(stages.items()):
                    if future.done():
                        future.result()
                        del stages[identity]
                for identity, future in list(recoveries.items()):
                    if future.done():
                        observed, retry = future.result()
                        reconciled[identity] = observed, now + 1 if retry else None
                        del recoveries[identity]
                if admission is not None and admission.done() and len(stages) + len(recoveries) < 4:
                    signature, selected = admission.result()
                    admission = None
                    if selected is not None:
                        root, assigned_scope, identity, kind = selected
                        stages[(kind, identity)] = stage_pool.submit(self._execute, root, assigned_scope, identity, kind)
                targets = {('agent', row[0]) for row in store.db.execute("SELECT id FROM attempts WHERE state IN ('stopping','reconciling')")}
                targets.update(('agent', row[0]) for row in store.db.execute("SELECT json_extract(data,'$.attempt_id') FROM source_claims "
                    "WHERE owner_kind='integration' AND state!='released'"))
                targets.update(('verification', row[0]) for row in store.db.execute(
                    "SELECT id FROM verification_executions WHERE state IN ('cancelling','reconciling')"))
                for job in targets - stages.keys() - recoveries.keys():
                    if len(stages) + len(recoveries) >= 4:
                        break
                    kind, identity = job
                    fingerprint = self._recovery_state(store, identity, kind)
                    observed, probe_at = reconciled.get(job, (None, None))
                    if observed != fingerprint or (probe_at is not None and now >= probe_at):
                        recoveries[job] = stage_pool.submit(self._recover, identity, kind)
                reconciled = {identity: value for identity, value in reconciled.items() if identity in targets}
                if admission is None and len(stages) + len(recoveries) < 4 and now - checked >= .5:
                    admission = admission_pool.submit(self._select, signature)
                    checked = now
                time.sleep(.05)
        finally:
            try:
                self.host.drain()
            finally:
                try:
                    admission_pool.shutdown(wait=True, cancel_futures=True)
                finally:
                    try:
                        stage_pool.shutdown(wait=True, cancel_futures=True)
                    finally:
                        store.close()
                        self.host.close()


def main():
    parser = argparse.ArgumentParser(description='Run the managed canonical database host.')
    parser.add_argument('--database', type=Path, default=default_state_path())
    parser.add_argument('--state-root', type=Path)
    args = parser.parse_args()
    stop = Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.set())
    try:
        with Host(args.database, args.state_root) as host, AttachmentServer(host):
            print(json.dumps({'session': host.session.model_dump(mode='json')}), flush=True)
            Coordinator(host).run(stop.is_set)
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({'error': getattr(error, 'code', type(error).__name__)}), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
