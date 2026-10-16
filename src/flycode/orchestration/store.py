"""One transactional SQLite source for tasks, ownership, reservations and events.

No inference, subprocess or project filesystem operation runs in a transaction.
Caller scopes are supplied by the trusted host, not by a worker's task content.
"""

import hashlib
import json
import math
import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import (
    AccessScope,
    AgentAttempt,
    AgentInstance,
    AgentProfile,
    AgentProfileDraft,
    BudgetAmount,
    BudgetLedger,
    ClaimRequest,
    ClaimResult,
    ModelCall,
    QueueEvent,
    TaskCommand,
    TaskDraft,
    ToolCall,
    WorkerRecord,
    WorkflowBudget,
    WorkflowDraft,
    WorkflowSpec,
    WorkItem,
)
from ..domain.results import IntegrationRecord
from .scheduler import ordered

ACTIVE = ('assigned', 'running', 'stopping', 'reconciling')


def required_task(task: WorkItem) -> bool:
    return task.retired_by_result is None


def default_state_path() -> Path:
    return Path(os.environ.get('FLYCODE_STATE_DATABASE') or Path.home() / '.local/share/flycode/state.sqlite').expanduser().resolve()


class StoreError(ValueError):
    def __init__(self, code: str, message: str = '', current=None):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.current = current.model_dump(mode='json') if hasattr(current, 'model_dump') else current


def changed(record, **values):
    return type(record).model_validate({**record.model_dump(), **values})


def covers(scopes: tuple[str, ...], paths: tuple[str, ...]) -> bool:
    return all(any(scope == '.' or path == scope or path.startswith(scope + '/') for scope in scopes) for path in paths)


class Store:
    def __init__(self, path: Path, *, catalogue=None, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.catalogue, self.clock = catalogue, clock
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            self.db.close()
            raise StoreError('schema_version', str(version))
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, slot INTEGER NOT NULL, data TEXT NOT NULL,
                root_task_id TEXT NOT NULL REFERENCES tasks(id) DEFERRABLE INITIALLY DEFERRED);
            CREATE UNIQUE INDEX IF NOT EXISTS active_root ON workflows(slot) WHERE slot=1;
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
                parent_id TEXT REFERENCES tasks(id), state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS instances (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
                task_id TEXT NOT NULL REFERENCES tasks(id), instance_id TEXT NOT NULL REFERENCES instances(id),
                state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_task ON attempts(task_id)
                WHERE state IN ('assigned','running','stopping','reconciling');
            CREATE UNIQUE INDEX IF NOT EXISTS active_instance ON attempts(instance_id)
                WHERE state IN ('assigned','running','stopping','reconciling');
            CREATE TABLE IF NOT EXISTS reservations (
                attempt_id TEXT PRIMARY KEY REFERENCES attempts(id),
                model_reserved INTEGER NOT NULL CHECK(model_reserved>=0),
                tool_reserved INTEGER NOT NULL CHECK(tool_reserved>=0),
                model_spent INTEGER NOT NULL CHECK(model_spent>=0),
                tool_spent INTEGER NOT NULL CHECK(tool_spent>=0),
                verification INTEGER NOT NULL, uncertain INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS commands (
                actor_id TEXT NOT NULL, key TEXT NOT NULL, project_id TEXT, request_hash TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(actor_id,key));
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL REFERENCES workflows(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                sequence INTEGER PRIMARY KEY REFERENCES events(sequence), delivered_at REAL);
            CREATE TABLE IF NOT EXISTS model_calls (
                id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts(id),
                endpoint_ref TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_endpoint ON model_calls(endpoint_ref)
                WHERE state IN ('running','uncertain');
            CREATE TABLE IF NOT EXISTS tool_calls (
                id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
                state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_tool ON tool_calls(attempt_id)
                WHERE state IN ('running','uncertain');
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
            INSERT OR IGNORE INTO counters VALUES ('task_number',0),('root_starts',0),('queue_paused',0);
            CREATE TABLE IF NOT EXISTS messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                workflow_id TEXT NOT NULL REFERENCES workflows(id),
                recipient_attempt_id TEXT NOT NULL REFERENCES attempts(id), data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS workflow_messages ON messages(workflow_id,sequence);
            CREATE TABLE IF NOT EXISTS message_batches (
                attempt_id TEXT NOT NULL REFERENCES attempts(id), key TEXT NOT NULL,
                request_hash TEXT NOT NULL, message_ids TEXT NOT NULL, PRIMARY KEY(attempt_id,key));
            CREATE TABLE IF NOT EXISTS message_outbox (
                message_id TEXT PRIMARY KEY REFERENCES messages(id), delivered_at REAL);
            CREATE TABLE IF NOT EXISTS message_inbox (
                message_id TEXT PRIMARY KEY REFERENCES messages(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS message_questions (
                workflow_id TEXT NOT NULL REFERENCES workflows(id), correlation_id TEXT NOT NULL,
                batch_id TEXT NOT NULL, expires_at REAL NOT NULL, answer_id TEXT REFERENCES messages(id),
                PRIMARY KEY(workflow_id,correlation_id));
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS integrations (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
                attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id), state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_integration ON integrations(workflow_id)
                WHERE state IN ('preparing','verifying','applying','reconciling');
            CREATE TABLE IF NOT EXISTS execution_lanes (
                attempt_id TEXT PRIMARY KEY REFERENCES attempts(id),
                mode TEXT NOT NULL CHECK(mode IN ('worker','review','integration')));
            CREATE UNIQUE INDEX IF NOT EXISTS current_integration ON integrations(workflow_id) WHERE state='completed';
        ''')
        if version < 10:
            try:
                with self.transaction():
                    if any(row['name'] == 'attempt_id' and row['notnull'] for row in self.db.execute('PRAGMA table_info(model_calls)')):
                        self.db.execute('ALTER TABLE model_calls RENAME TO model_calls_previous')
                        self.db.execute('DROP INDEX active_endpoint')
                        self.db.execute('''CREATE TABLE model_calls (
                            id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts(id),
                            endpoint_ref TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)''')
                        self.db.execute('''INSERT INTO model_calls(rowid,id,attempt_id,endpoint_ref,state,data)
                            SELECT rowid,id,attempt_id,endpoint_ref,state,data FROM model_calls_previous''')
                        self.db.execute('DROP TABLE model_calls_previous')
                        self.db.execute("CREATE UNIQUE INDEX active_endpoint ON model_calls(endpoint_ref) WHERE state IN ('running','uncertain')")
                    self.db.execute('PRAGMA user_version=10')
            except BaseException:
                self.db.close()
                raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _scope(self, scope, project, roles=()):
        if project not in scope.project_ids or (roles and scope.role not in roles):
            raise StoreError('scope', 'Caller cannot access or mutate this project')

    def _role(self, scope, roles):
        if scope.role not in roles:
            raise StoreError('scope', 'Host authority required')

    def _counter(self, name, increment=0):
        if increment:
            self.db.execute('UPDATE counters SET value=value+? WHERE name=?', (increment, name))
        return self.db.execute('SELECT value FROM counters WHERE name=?', (name,)).fetchone()[0]

    def _load(self, table, identity, cls):
        row = self.db.execute(f'SELECT data FROM {table} WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('not_found', table)
        return cls.model_validate_json(row['data'])

    def _workflow(self, identity):
        return self._load('workflows', identity, WorkflowSpec)

    def _task(self, identity):
        return self._load('tasks', identity, WorkItem)

    def _instance(self, identity):
        return self._load('instances', identity, AgentInstance)

    def _profile(self, identity):
        return self._load('profiles', identity, AgentProfile)

    def _attempt(self, identity):
        row = self.db.execute('SELECT data FROM attempts WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'attempt')
        ledger = self.db.execute('SELECT * FROM reservations WHERE attempt_id=?', (identity,)).fetchone()
        return AgentAttempt.model_validate({**json.loads(row['data']),
            'reserved': {'model_calls': ledger['model_reserved'], 'tool_calls': ledger['tool_reserved']},
            'spent': {'model_calls': ledger['model_spent'], 'tool_calls': ledger['tool_spent']}})

    def _save_workflow(self, workflow):
        self.db.execute('INSERT INTO workflows VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET slot=excluded.slot,data=excluded.data',
            (workflow.id, workflow.project_id, int(workflow.active_slot), workflow.model_dump_json(), workflow.root_task_id))

    def _save_task(self, task):
        self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,data=excluded.data',
            (task.id, task.workflow_id, task.parent_id, task.state, task.model_dump_json()))

    def _save_instance(self, instance):
        self.db.execute('INSERT INTO instances VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                        (instance.id, instance.profile_id, instance.model_dump_json()))

    def _save_attempt(self, attempt):
        self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,data=excluded.data',
            (attempt.id, attempt.workflow_id, attempt.task_id, attempt.instance_id, attempt.state,
             attempt.model_dump_json(exclude={'reserved', 'spent'})))

    def _replay(self, scope, key, request):
        if not key or len(key) > 200:
            raise StoreError('idempotency', 'Key must contain 1..200 characters')
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest()
        row = self.db.execute('SELECT * FROM commands WHERE actor_id=? AND key=?', (scope.actor_id, key)).fetchone()
        if row is None:
            return False, None, digest
        if row['project_id'] is not None:
            self._scope(scope, row['project_id'])
        if row['request_hash'] != digest:
            raise StoreError('idempotency', 'Key already belongs to a different request')
        return True, json.loads(row['result']), digest

    def _remember(self, scope, key, project, digest, result):
        data = result.model_dump(mode='json') if hasattr(result, 'model_dump') else result
        self.db.execute('INSERT INTO commands VALUES (?,?,?,?,?)',
                        (scope.actor_id, key, project, digest, json.dumps(data, allow_nan=False)))

    def _event(self, scope, workflow, kind, *, task=None, attempt=None, **data):
        values = {'project_id': workflow.project_id, 'workflow_id': workflow.id, 'actor_id': scope.actor_id, 'kind': kind,
                  'occurred_at': self.clock(), 'task_id': task.id if task else None,
                  'attempt_id': attempt.id if attempt else None, 'data': data}
        cursor = self.db.execute('INSERT INTO events(project_id,workflow_id,data) VALUES (?,?,?)',
                                 (workflow.project_id, workflow.id, json.dumps(values, allow_nan=False)))
        self.db.execute('INSERT INTO outbox(sequence) VALUES (?)', (cursor.lastrowid,))

    def workflow(self, scope, identity):
        result = self._workflow(identity)
        self._scope(scope, result.project_id)
        return result

    def task(self, scope, identity):
        result = self._task(identity)
        self._scope(scope, result.project_id)
        return result

    def workflows(self, scope):
        return [WorkflowSpec.model_validate_json(row['data']) for row in self.db.execute('SELECT data FROM workflows')
                if json.loads(row['data'])['project_id'] in scope.project_ids]

    def tasks(self, scope, workflow_id):
        self.workflow(scope, workflow_id)
        return [WorkItem.model_validate_json(row['data']) for row in self.db.execute(
            'SELECT data FROM tasks WHERE workflow_id=? ORDER BY rowid', (workflow_id,))]

    def _invalidate_result(self, scope, identity, reason, *, expected=None):
        if not self.db.in_transaction:
            raise RuntimeError('Result invalidation must be atomic')
        record = self._load('integrations', identity, IntegrationRecord)
        workflow = self.workflow(scope, record.workflow_id)
        if record.state != 'completed' or (expected is not None and record != expected):
            return record
        record = changed(record, state='invalidated', error=reason)
        self.db.execute('UPDATE integrations SET state=?,data=? WHERE id=?',
            (record.state, record.model_dump_json(), record.id))
        for task in self.tasks(scope, workflow.id):
            if (task.state == 'succeeded' and (task.result_id == record.id
                    or (task.result_id is None and task.id in record.contracts))):
                task = changed(task, state='blocked', result_id=record.id, blocked_reason='result_invalidated',
                    revision=task.revision + 1, updated_at=self.clock())
                self._save_task(task)
                self._event(scope, workflow, 'result_invalidated', task=task, integration_id=record.id, reason=reason)
        active = any(attempt.state in ACTIVE for attempt in self.attempts(scope, workflow.id))
        state = workflow.state if workflow.state in {'cancelled', 'cancelling', 'failed'} else 'incomplete'
        self._save_workflow(changed(workflow, state=state, active_slot=workflow.active_slot if active else False,
            revision=workflow.revision + 1))
        return record

    def refresh_results(self, scope, workflow_id=None):
        """Refresh external facts before admission/projection, never under a SQL lock."""
        if self.db.in_transaction:
            raise RuntimeError('Result inspection cannot run in a SQLite transaction')
        if workflow_id is not None:
            self.workflow(scope, workflow_id)
        from .integration import Integration

        records = [IntegrationRecord.model_validate_json(row['data']) for row in self.db.execute(
            "SELECT data FROM integrations WHERE state='completed'")]
        results = []
        for record in records:
            if record.project_id not in scope.project_ids or (workflow_id is not None and record.workflow_id != workflow_id):
                continue
            if record.runtime_root is None or not (Path(record.runtime_root) / 'verification').is_dir():
                with self.transaction():
                    results.append(self._invalidate_result(scope, record.id, 'result_location_missing', expected=record))
            else:
                results.append(Integration(self, Path(record.runtime_root)).refresh(scope, record.id))
        return results

    def attempts(self, scope, workflow_id):
        self.workflow(scope, workflow_id)
        return [self._attempt(row['id']) for row in self.db.execute('SELECT id FROM attempts WHERE workflow_id=? ORDER BY rowid', (workflow_id,))]

    def events(self, scope, workflow_id, after=0, limit=500):
        self.workflow(scope, workflow_id)
        if after < 0 or not 1 <= limit <= 1000:
            raise StoreError('cursor', 'Invalid event window')
        return [QueueEvent(sequence=row['sequence'], **json.loads(row['data'])) for row in self.db.execute(
            'SELECT * FROM events WHERE workflow_id=? AND sequence>? ORDER BY sequence LIMIT ?', (workflow_id, after, limit))]

    def outbox_count(self):
        return self.db.execute('SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL').fetchone()[0]

    def last_event(self, scope, workflow_id, task_id=None):
        self.workflow(scope, workflow_id)
        for row in self.db.execute('SELECT sequence,data FROM events WHERE workflow_id=? ORDER BY sequence DESC', (workflow_id,)):
            data = json.loads(row['data'])
            if task_id is None or data['task_id'] == task_id:
                return QueueEvent(sequence=row['sequence'], **data)
        return None

    def instances(self, scope):
        return [self._instance(row['id']) for row in self.db.execute('SELECT id FROM instances')
                if set(self._profile(self._instance(row['id']).profile_id).project_ids) <= scope.project_ids]

    def _new_task(self, workflow, draft):
        return WorkItem(**draft.model_dump(), id=uuid4().hex, short_id=f'FC-{self._counter("task_number", 1)}',
            project_id=workflow.project_id, workflow_id=workflow.id, created_at=self.clock(), updated_at=self.clock())

    def create_workflow(self, scope: AccessScope, draft: WorkflowDraft, *, key: str) -> WorkflowSpec:
        draft = WorkflowDraft.model_validate(draft.model_dump())
        self._scope(scope, draft.project_id, ('owner',))
        if draft.task.parent_id or draft.task.dependencies:
            raise StoreError('dependency', 'A new root cannot reference other workflows')
        with self.transaction():
            found, result, digest = self._replay(scope, key, {'create_workflow': draft.model_dump(mode='json')})
            if found:
                return WorkflowSpec.model_validate(result)
            identity, root_id = uuid4().hex, uuid4().hex
            workflow = WorkflowSpec(id=identity, project_id=draft.project_id, root_task_id=root_id,
                                    budget=draft.budget, created_at=self.clock())
            task = changed(self._new_task(workflow, draft.task), id=root_id)
            self._save_workflow(workflow)
            self._save_task(task)
            self._event(scope, workflow, 'workflow_created', task=task, revision=workflow.revision)
            self._remember(scope, key, workflow.project_id, digest, workflow)
            return workflow

    def _validate_graph(self, scope, workflow, task):
        tasks = {item.id: item for item in self.tasks(scope, workflow.id)}
        tasks[task.id] = task
        limits = workflow.budget or WorkflowBudget()
        if len(tasks) > limits.max_tasks:
            raise StoreError('task_limit')
        if task.parent_id is not None:
            parent = tasks.get(task.parent_id)
            if parent is None or parent.id == task.id:
                raise StoreError('parent', 'Unknown or cyclic parent')
            if sum(item.parent_id == parent.id for item in tasks.values()) > limits.max_children:
                raise StoreError('children', 'Parent child limit reached')
            ancestors, current = {task.id}, parent
            while current is not None:
                if current.id in ancestors:
                    raise StoreError('cycle', 'Parent cycle')
                ancestors.add(current.id)
                current = tasks.get(current.parent_id)
            if len(ancestors) - 1 > limits.max_depth:
                raise StoreError('depth')
        elif task.id != workflow.root_task_id:
            raise StoreError('parent', 'Only the root has no parent')
        visiting, visited = set(), set()
        def visit(identity):
            if identity in visiting:
                raise StoreError('cycle', 'Dependency cycle')
            if identity in visited:
                return
            if identity not in tasks:
                raise StoreError('dependency', 'Foreign or missing dependency')
            visiting.add(identity)
            children = tuple(item.id for item in tasks.values() if item.parent_id == identity)
            for dependency in (*tasks[identity].dependencies, *children):
                visit(dependency)
            visiting.remove(identity)
            visited.add(identity)
        for identity in tasks:
            visit(identity)

    def create_task(self, scope, workflow_id, draft: TaskDraft, *, key):
        draft = TaskDraft.model_validate(draft.model_dump())
        with self.transaction():
            workflow = self.workflow(scope, workflow_id)
            self._scope(scope, workflow.project_id, ('owner',))
            found, result, digest = self._replay(scope, key, {'create_task': workflow_id, 'draft': draft.model_dump(mode='json')})
            if found:
                return WorkItem.model_validate(result)
            if workflow.state in {'succeeded', 'cancelled', 'failed'}:
                raise StoreError('state', 'Cannot extend a terminal workflow')
            task = self._new_task(workflow, draft)
            self._validate_graph(scope, workflow, task)
            self._save_task(task)
            workflow = changed(workflow, revision=workflow.revision + 1, semantic_revision=workflow.semantic_revision + 1)
            self._save_workflow(workflow)
            self._event(scope, workflow, 'task_created', task=task, revision=task.revision)
            self._remember(scope, key, workflow.project_id, digest, task)
            return task

    def _ready(self, task, workflow):
        if (not task.objective.strip() or task.acceptance is None or task.permissions is None
                or not task.permissions.approved or task.budget is None or workflow.budget is None
                or not task.budget.model_calls + task.budget.tool_calls or not task.permissions.tools):
            raise StoreError('incomplete', 'Objective, criteria, approved permissions and budget are required')
        if (task.budget.model_calls > workflow.budget.max_model_calls
                or task.budget.tool_calls > workflow.budget.max_tool_calls):
            raise StoreError('budget', 'Task budget exceeds workflow limits')

    def _stop(self, task, *, outcome='retry_wait'):
        if task.current_attempt_id is None:
            return task
        attempt = self._attempt(task.current_attempt_id)
        if attempt.state == 'awaiting_review':
            if not attempt.stop_confirmed:
                raise StoreError('stop_unconfirmed')
            self._save_attempt(changed(attempt, state='cancelled' if outcome == 'cancelled' else 'interrupted'))
            # Publication released this instance and reservation already. The
            # instance may now belong to a completely different assignment.
            return changed(task, state=outcome, current_attempt_id=None, fence=task.fence + 1)
        if attempt.state in ACTIVE:
            self._save_attempt(changed(attempt, state='stopping'))
            instance = self._instance(attempt.instance_id)
            self._save_instance(changed(instance, state='reconciling', revision=instance.revision + 1))
        return changed(task, fence=task.fence + 1)

    def _reopen(self, scope, task, workflow):
        if task.state not in {'succeeded', 'blocked', 'failed', 'cancelled', 'retry_wait'}:
            raise StoreError('state', 'Only completed or stopped work can be reopened')
        if any(item.current_attempt_id for item in self.tasks(scope, workflow.id)):
            raise StoreError('ownership', 'Stop all assignments before reopening their result')
        record = self._load('integrations', task.result_id, IntegrationRecord) if task.result_id else next((
            IntegrationRecord.model_validate_json(row['data']) for row in self.db.execute(
                "SELECT data FROM integrations WHERE workflow_id=? AND state IN ('completed','invalidated') ORDER BY rowid DESC",
                (workflow.id,)) if task.id in json.loads(row['data'])['contracts']), None)
        if record is not None:
            self._invalidate_result(scope, record.id, 'owner_reopened')
            auxiliary = {self._attempt(record.attempt_id).task_id}
            for identity in record.review_ids:
                row = self.db.execute('SELECT attempt_id FROM reviews WHERE id=?', (identity,)).fetchone()
                if row:
                    auxiliary.add(self._attempt(row['attempt_id']).task_id)
            for identity in auxiliary - set(record.author_attempts) - {workflow.root_task_id, task.id}:
                old = self._task(identity)
                if (old.kind == 'review' and old.current_attempt_id is None
                        and record.contracts.get(old.id) == old.contract_hash
                        and record.semantic_revisions.get(old.id) == old.semantic_revision
                        and (old.result_id is None or old.result_id == record.id)):
                    old = changed(old, retired_by_result=record.id, archived=True, blocked_reason='historical_result',
                        revision=old.revision + 1, updated_at=self.clock())
                    self._save_task(old)
                    self._event(scope, workflow, 'review_retired', task=old, integration_id=record.id)
            task, workflow = self._task(task.id), self._workflow(workflow.id)
        if task.id != workflow.root_task_id:
            root = self._task(workflow.root_task_id)
            self._save_task(changed(root, state='planned', result_id=None, blocked_reason=None,
                semantic_revision=root.semantic_revision + 1, revision=root.revision + 1, updated_at=self.clock()))
        return (changed(task, state='planned', result_id=None, blocked_reason=None),
                changed(workflow, state='draft', active_slot=False, semantic_revision=workflow.semantic_revision + 1))

    def command(self, scope, task_id, command: TaskCommand):
        command = TaskCommand.model_validate(command.model_dump())
        with self.transaction():
            task = self.task(scope, task_id)
            workflow = self._workflow(task.workflow_id)
            self._scope(scope, task.project_id, ('owner',))
            found, result, digest = self._replay(scope, command.idempotency_key,
                {'task': task_id, 'command': command.model_dump(mode='json')})
            if found:
                return WorkItem.model_validate(result)
            if task.revision != command.expected_revision:
                raise StoreError('revision', 'Stale task revision', task)
            op, values = command.operation, command.changes
            original_hash = task.contract_hash
            if not required_task(task) and op not in {'set_metadata', 'set_priority', 'archive'}:
                raise StoreError('historical_result', 'This review belongs to a previous result')
            if op == 'enqueue':
                if values or task.state != 'planned':
                    raise StoreError('state', 'Only planned tasks can be enqueued')
                self._ready(task, workflow)
                self._validate_graph(scope, workflow, task)
                task = changed(task, state='ready', queued_at=self.clock())
            elif op in {'set_metadata', 'set_priority', 'assign_profile', 'revise'}:
                allowed = {'title', 'tags', 'due_at'} if op == 'set_metadata' else {'priority'} if op == 'set_priority' else (
                    {'allowed_profile_ids'} if op == 'assign_profile' else set(TaskDraft.model_fields) - {'title', 'tags', 'due_at', 'priority'})
                if not values or set(values) - allowed or 'parent_id' in values:
                    raise StoreError('command', 'Unsupported change fields')
                if op in {'revise', 'assign_profile'} and task.current_attempt_id:
                    raise StoreError('ownership', 'Confirm worker stop before changing its contract')
                task = changed(task, **values)
                self._validate_graph(scope, workflow, task)
                if task.state != 'planned':
                    self._ready(task, workflow)
            elif op in {'pause', 'resume'}:
                if values:
                    raise StoreError('command', 'Unexpected change fields')
                if op == 'resume' and task.current_attempt_id:
                    raise StoreError('ownership', 'Stop is not yet confirmed')
                task = changed(self._stop(task) if op == 'pause' else task, paused=op == 'pause')
                if task.id == workflow.root_task_id and workflow.active_slot:
                    if op == 'pause':
                        for child in self.tasks(scope, workflow.id):
                            if child.id != task.id and child.current_attempt_id:
                                self._save_task(changed(self._stop(child), revision=child.revision + 1))
                        active = any(a.state in ACTIVE for a in self.attempts(scope, workflow.id))
                        workflow = changed(workflow, state='pausing' if active else 'paused')
                    else:
                        if any(a.state in ACTIVE for a in self.attempts(scope, workflow.id)):
                            raise StoreError('ownership', 'Workflow stop is not confirmed')
                        expired = workflow.deadline_at is not None and self.clock() >= workflow.deadline_at
                        workflow = changed(workflow, state='incomplete' if expired else 'running', active_slot=not expired)
            elif op == 'release_assignment':
                if values or not task.current_attempt_id:
                    raise StoreError('ownership', 'No current assignment')
                task = self._stop(task)
            elif op == 'retry':
                if values or task.state not in {'retry_wait', 'failed'} or task.current_attempt_id:
                    raise StoreError('state', 'Task cannot retry')
                self._ready(task, workflow)
                count = self.db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?', (task.id,)).fetchone()[0]
                if count >= workflow.budget.max_attempts:
                    raise StoreError('attempt_limit')
                task = changed(task, state='ready', queued_at=self.clock())
            elif op == 'archive':
                if (task.current_attempt_id or set(values) - {'archived'}
                        or (task.id == workflow.root_task_id and workflow.active_slot)):
                    raise StoreError('ownership', 'Stop before archiving')
                task = changed(task, archived=values.get('archived', True))
            elif op == 'cancel':
                if values:
                    raise StoreError('command', 'Unexpected change fields')
                targets = self.tasks(scope, workflow.id) if task.id == workflow.root_task_id else [task]
                for target in targets:
                    before_hash = target.contract_hash
                    target = self._stop(target, outcome='cancelled')
                    target = changed(target, state=target.state if target.current_attempt_id else 'cancelled',
                        permissions=changed(target.permissions, approved=False) if target.permissions else None,
                        revision=target.revision + int(target.id != task.id), updated_at=self.clock())
                    if target.id != task.id and target.contract_hash != before_hash:
                        target = changed(target, semantic_revision=target.semantic_revision + 1)
                    self._save_task(target)
                    if target.id == task.id:
                        task = target
                if task.id == workflow.root_task_id:
                    active = any(a.state in ACTIVE for a in self.attempts(scope, workflow.id))
                    workflow = changed(workflow, state='cancelling' if active else 'cancelled', active_slot=active)
            elif op == 'reopen':
                if values:
                    raise StoreError('command', 'Unexpected change fields')
                task, workflow = self._reopen(scope, task, workflow)
            else:
                raise StoreError('command')
            task = changed(task, revision=task.revision + 1, updated_at=self.clock(),
                           semantic_revision=task.semantic_revision + int(task.contract_hash != original_hash or op == 'reopen'))
            workflow = changed(workflow, revision=workflow.revision + 1)
            self._save_task(task)
            self._save_workflow(workflow)
            if task.contract_hash != original_hash:
                for row in self.db.execute("SELECT id,data FROM integrations WHERE workflow_id=? AND state='completed'", (workflow.id,)).fetchall():
                    record = IntegrationRecord.model_validate_json(row['data'])
                    if task.id in record.contracts:
                        self._invalidate_result(scope, record.id, 'contract_changed')
                task, workflow = self._task(task.id), self._workflow(workflow.id)
            self._event(scope, workflow, 'task_' + op, task=task, revision=task.revision,
                        semantic_revision=task.semantic_revision, changes=values)
            self._remember(scope, command.idempotency_key, task.project_id, digest, task)
            return task

    def register_profile(self, scope, draft: AgentProfileDraft):
        self._role(scope, ('owner',))
        draft = AgentProfileDraft.model_validate(draft.model_dump())
        if not set(draft.project_ids) <= scope.project_ids:
            raise StoreError('scope', 'Profile includes an unauthorized project')
        role = 'scorer' if draft.role == 'decider' else 'primary'
        model = self.catalogue.resolve(draft.model_ref, role, draft.capability_version) if self.catalogue else None
        reason = ('model_unavailable' if model is None else 'context_unavailable' if model.context_limit is None
                  else 'context_limit' if draft.context_limit > model.context_limit else None)
        profile = AgentProfile(**draft.model_dump(), id=uuid4().hex,
            instruction_hash=hashlib.sha256(draft.instructions.encode()).hexdigest(), available=reason is None,
            model_id=model.model_id if model else None, endpoint_ref=model.endpoint_ref if model else None,
            model_revision=model.revision if model else None, loaded_instance_id=model.loaded_instance_id if model else None,
            context_capacity=model.context_limit if model else None, load_fingerprint=model.load_fingerprint if model else None,
            unavailable_reason=reason)
        with self.transaction():
            self.db.execute('INSERT INTO profiles VALUES (?,?)', (profile.id, profile.model_dump_json()))
        return profile

    def register_instance(self, scope, profile_id):
        self._role(scope, ('runtime',))
        profile = self._profile(profile_id)
        if not set(profile.project_ids) <= scope.project_ids:
            raise StoreError('scope', 'Instance includes an unauthorized project')
        instance = AgentInstance(id=uuid4().hex, profile_id=profile.id, profile_version=profile.version)
        with self.transaction():
            self._save_instance(instance)
        return instance

    def instance(self, scope, identity):
        instance = self._instance(identity)
        if not set(self._profile(instance.profile_id).project_ids) <= scope.project_ids:
            raise StoreError('scope', 'Foreign instance')
        return instance

    def _binding_current(self, profile):
        if not self.catalogue or not profile.available:
            return False
        role = 'scorer' if profile.role == 'decider' else 'primary'
        model = self.catalogue.resolve(profile.model_ref, role, profile.capability_version)
        return (model is not None and model.revision == profile.model_revision
                and model.endpoint_ref == profile.endpoint_ref and model.model_id == profile.model_id
                and model.loaded_instance_id == profile.loaded_instance_id and model.context_limit == profile.context_capacity
                and model.load_fingerprint == profile.load_fingerprint and profile.context_capacity is not None
                and profile.context_limit <= profile.context_capacity)

    def budget(self, scope, workflow_id):
        workflow = self.workflow(scope, workflow_id)
        if workflow.budget is None:
            raise StoreError('incomplete', 'Workflow has no approved budget')
        row = self.db.execute('''SELECT COALESCE(SUM(model_reserved),0) AS mr, COALESCE(SUM(tool_reserved),0) AS tr,
            COALESCE(SUM(model_spent),0) AS ms, COALESCE(SUM(tool_spent),0) AS ts,
            COALESCE(MAX(uncertain),0) AS uncertain FROM reservations JOIN attempts ON attempts.id=attempt_id
            WHERE workflow_id=?''', (workflow_id,)).fetchone()
        limits = workflow.budget
        return BudgetLedger(limit=BudgetAmount(model_calls=limits.max_model_calls, tool_calls=limits.max_tool_calls),
            spent=BudgetAmount(model_calls=row['ms'], tool_calls=row['ts']),
            reserved=BudgetAmount(model_calls=row['mr'], tool_calls=row['tr']),
            free=BudgetAmount(model_calls=limits.max_model_calls-row['ms']-row['mr'],
                              tool_calls=limits.max_tool_calls-row['ts']-row['tr']), uncertain=bool(row['uncertain']))

    def pause_queue(self, scope, paused, *, key):
        self._role(scope, ('owner',))
        if not scope.global_queue_control:
            raise StoreError('scope', 'Global queue authority required')
        if not isinstance(paused, bool):
            raise StoreError('command', 'Pause must be boolean')
        with self.transaction():
            found, _, digest = self._replay(scope, key, {'queue_paused': paused, 'scope': sorted(scope.project_ids)})
            if found:
                return
            self.db.execute('UPDATE counters SET value=? WHERE name=?', (int(paused), 'queue_paused'))
            for workflow in self.workflows(scope):
                self._event(scope, workflow, 'queue_paused' if paused else 'queue_resumed')
            self._remember(scope, key, None, digest, paused)

    def start_next(self, scope, *, key):
        self._role(scope, ('owner', 'runtime'))
        with self.transaction():
            found, result, digest = self._replay(scope, key, {'start_next': sorted(scope.project_ids)})
            if found:
                return WorkflowSpec.model_validate(result) if result else None
            selected = None
            if not self._counter('queue_paused') and not self.db.execute('SELECT 1 FROM workflows WHERE slot=1').fetchone():
                candidates = []
                for workflow in self.workflows(scope):
                    task = self._task(workflow.root_task_id)
                    if workflow.state == 'draft' and task.state == 'ready' and not task.paused and not task.archived:
                        self._ready(task, workflow)
                        reason = None
                        if workflow.deadline_at is not None and (self.clock() >= workflow.deadline_at or self.clock() < workflow.started_at):
                            reason = 'wall_deadline'
                        ledger = self.budget(scope, workflow.id)
                        if not ledger.free.model_calls + ledger.free.tool_calls:
                            reason = reason or 'budget_exhausted'
                        children = [item for item in self.tasks(scope, workflow.id) if item.parent_id == task.id and required_task(item)]
                        if not children:
                            previous = [attempt for attempt in self.attempts(scope, workflow.id) if attempt.task_id == task.id]
                            if len(previous) >= workflow.budget.max_attempts:
                                reason = reason or 'attempt_limit'
                            models = task.budget.model_calls - sum(attempt.spent.model_calls + attempt.reserved.model_calls for attempt in previous)
                            tools = task.budget.tool_calls - sum(attempt.spent.tool_calls + attempt.reserved.tool_calls for attempt in previous)
                            if models < 0 or tools < 0 or models + tools == 0:
                                reason = reason or 'task_budget'
                        if reason:
                            task = changed(task, state='blocked', blocked_reason=reason, revision=task.revision + 1, updated_at=self.clock())
                            self._save_task(task)
                            self._save_workflow(changed(workflow, state='incomplete', active_slot=False, revision=workflow.revision + 1))
                            self._event(scope, workflow, 'workflow_blocked', task=task, reason=reason)
                            continue
                        candidates.append(task)
                ranked = ordered(candidates, self.clock(), self._counter('root_starts'), root=True)
                if ranked:
                    selected = self._workflow(ranked[0].workflow_id)
                    selected = changed(selected, active_slot=True, state='running', revision=selected.revision + 1)
                    self._save_workflow(selected)
                    self._event(scope, selected, 'workflow_selected', task=ranked[0], revision=selected.revision)
            self._remember(scope, key, selected.project_id if selected else None, digest, selected)
            return selected

    def _eligibility(self, scope, workflow, task, instance, profile, reserve):
        reasons = []
        if self._counter('queue_paused'):
            reasons.append('queue_paused')
        if task.paused or workflow.state in {'paused', 'pausing'}:
            reasons.append('paused')
        if not workflow.active_slot or workflow.state != 'running':
            reasons.append('root_not_active')
        if not required_task(task):
            reasons.append('historical_result')
        if task.state != 'ready' or task.archived or task.current_attempt_id:
            reasons.append('task_not_ready')
        if instance.state != 'idle' or instance.current_attempt_id:
            reasons.append('instance_busy')
        if task.permissions is None or not task.permissions.approved:
            reasons.append('permission')
        if task.project_id not in profile.project_ids or (task.allowed_profile_ids and profile.id not in task.allowed_profile_ids):
            reasons.append('profile_scope')
        required_roles = {'implementation': {'developer'}, 'research': {'researcher', 'planner'}, 'review': {'reviewer'}}
        if profile.role not in required_roles[task.kind] or not set(task.capabilities) <= set(profile.capabilities):
            reasons.append('capabilities')
        if task.permissions is not None and (not set(task.permissions.tools) <= set(profile.permissions.tools)
                    or not covers(profile.permissions.read_scopes, task.permissions.read_scopes)
                    or not covers(profile.permissions.write_scopes, task.permissions.write_scopes)
                or not profile.permissions.approved):
            reasons.append('profile_permission')
        if not self._binding_current(profile):
            reasons.append('model_unavailable')
        tasks = self.tasks(scope, workflow.id)
        if any(item.parent_id == task.id and required_task(item) for item in tasks):
            reasons.append('aggregate_parent')
        by_id = {item.id: item for item in tasks}
        if any(by_id[identity].state != 'succeeded' or not required_task(by_id[identity]) for identity in task.dependencies):
            reasons.append('dependency')
        attempts = self.attempts(scope, workflow.id)
        active = [attempt for attempt in attempts if attempt.state in ACTIVE]
        limits = workflow.budget
        if limits is None or task.budget is None:
            return [*reasons, 'incomplete']
        if len(active) >= limits.max_instances:
            reasons.append('instance_limit')
        previous = [attempt for attempt in attempts if attempt.task_id == task.id]
        if len(previous) >= limits.max_attempts:
            reasons.append('attempt_limit')
        if workflow.deadline_at is not None and (self.clock() >= workflow.deadline_at or self.clock() < workflow.started_at):
            reasons.append('wall_deadline')
        if task.permissions and task.permissions.write_scopes:
            for attempt in active:
                other = self._task(attempt.task_id)
                if other.permissions and any(covers((a,), (b,)) or covers((b,), (a,))
                        for a in task.permissions.write_scopes for b in other.permissions.write_scopes):
                    reasons.append('write_scope_conflict')
                    break
        ledger = self.budget(scope, workflow.id)
        if reserve.model_calls > ledger.free.model_calls or reserve.tool_calls > ledger.free.tool_calls:
            reasons.append('budget_exhausted')
        if (sum(a.spent.model_calls + a.reserved.model_calls for a in previous) + reserve.model_calls > task.budget.model_calls
                or sum(a.spent.tool_calls + a.reserved.tool_calls for a in previous) + reserve.tool_calls > task.budget.tool_calls):
            reasons.append('task_budget')
        if task.kind != 'review':
            dev = [attempt for attempt in attempts if not attempt.verification]
            if (sum(a.spent.model_calls + a.reserved.model_calls for a in dev) + reserve.model_calls
                    > math.floor(limits.max_model_calls * (1 - limits.verification_fraction))
                    or sum(a.spent.tool_calls + a.reserved.tool_calls for a in dev) + reserve.tool_calls
                    > math.floor(limits.max_tool_calls * (1 - limits.verification_fraction))):
                reasons.append('verification_reserve')
        return reasons

    def claim(self, scope, request: ClaimRequest) -> ClaimResult:
        self._role(scope, ('runtime',))
        request = ClaimRequest.model_validate(request.model_dump())
        self.refresh_results(scope, self.task(scope, request.task_id).workflow_id if request.task_id else None)
        with self.transaction():
            instance = self.instance(scope, request.instance_id)
            profile = self._profile(instance.profile_id)
            preferred = self.task(scope, request.task_id) if request.task_id else None
            found, result, digest = self._replay(scope, request.idempotency_key, {'claim': request.model_dump(mode='json')})
            if found:
                return ClaimResult.model_validate(result)
            if instance.revision != request.expected_instance_revision:
                raise StoreError('revision', 'Stale instance', instance)
            if preferred and preferred.revision != request.expected_task_revision:
                raise StoreError('revision', 'Stale task', preferred)
            workflows = [self._workflow(preferred.workflow_id)] if preferred else [w for w in self.workflows(scope) if w.active_slot]
            eligible, rejected = [], []
            for workflow in workflows:
                tasks = [preferred] if preferred else self.tasks(scope, workflow.id)
                for task in tasks:
                    reasons = self._eligibility(scope, workflow, task, instance, profile, request.reserve)
                    if reasons:
                        rejected.extend(reasons)
                    else:
                        eligible.append(task)
            attempt = None
            workflow = self._workflow(eligible[0].workflow_id) if eligible else None
            if eligible:
                assert workflow is not None
                ledger = self.budget(scope, workflow.id)
                cost = max(request.reserve.model_calls / max(ledger.free.model_calls, 1),
                           request.reserve.tool_calls / max(ledger.free.tool_calls, 1))
                task = ordered(eligible, self.clock(), workflow.dispatch_count, cost=cost)[0]
                identity, fence = uuid4().hex, task.fence + 1
                attempt = AgentAttempt(id=identity, workflow_id=workflow.id, task_id=task.id,
                    workflow_revision=workflow.revision, workflow_semantic_revision=workflow.semantic_revision,
                    task_revision=task.revision, semantic_revision=task.semantic_revision, contract_hash=task.contract_hash,
                    profile_id=profile.id, profile_version=profile.version, profile_snapshot=profile, instance_id=instance.id,
                    fence=fence, initial_budget=request.reserve, reserved=request.reserve, verification=task.kind == 'review',
                    created_at=self.clock(), lease_until=self.clock() + 30.)
                self._save_attempt(attempt)
                self.db.execute('INSERT INTO reservations VALUES (?,?,?,?,?,?,?)',
                    (identity, request.reserve.model_calls, request.reserve.tool_calls, 0, 0, int(attempt.verification), 0))
                task = changed(task, current_attempt_id=identity, fence=fence, state='assigned',
                               revision=task.revision + 1, updated_at=self.clock())
                self._save_task(task)
                self._save_instance(changed(instance, current_attempt_id=identity, state='assigned', revision=instance.revision + 1))
                self._event(scope, workflow, 'attempt_assigned', task=task, attempt=attempt,
                            revision=task.revision, fence=fence, reservation=request.reserve.model_dump())
            result = ClaimResult(attempt=attempt, reasons=() if attempt else tuple(sorted(set(rejected))) or ('no_eligible_task',))
            self._remember(scope, request.idempotency_key, workflow.project_id if workflow else (preferred.project_id if preferred else None), digest, result)
            return result

    def _owned(self, scope, attempt_id, fence, *, running=False):
        self._role(scope, ('runtime',))
        attempt = self._attempt(attempt_id)
        task = self.task(scope, attempt.task_id)
        workflow = self._workflow(attempt.workflow_id)
        if (attempt.fence != fence or task.fence != fence or task.current_attempt_id != attempt.id
                or attempt.state not in (('running',) if running else ('assigned', 'running'))
                or attempt.lease_until <= self.clock() or task.semantic_revision != attempt.semantic_revision
                or task.contract_hash != attempt.contract_hash):
            raise StoreError('fence', 'Ownership is stale, expired or revoked')
        if workflow.deadline_at is not None and (self.clock() >= workflow.deadline_at or self.clock() < workflow.started_at):
            raise StoreError('wall_deadline')
        return attempt, task, workflow

    def _execution_available(self, scope, attempt_id):
        self.task(scope, self._attempt(attempt_id).task_id)
        if self.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt_id,)).fetchone():
            raise StoreError('worker_exists', 'A recorded worker session cannot be shared or replayed')
        if self.db.execute('SELECT 1 FROM execution_lanes WHERE attempt_id=?', (attempt_id,)).fetchone():
            raise StoreError('execution_busy', 'This attempt already has a durable execution admission')

    def _claim_execution(self, scope, attempt_id, fence, mode):
        if not self.db.in_transaction:
            raise RuntimeError('Execution admission must be atomic')
        attempt, task, workflow = self._owned(scope, attempt_id, fence, running=mode != 'worker')
        self._execution_available(scope, attempt_id)
        self.db.execute('INSERT INTO execution_lanes VALUES (?,?)', (attempt_id, mode))
        self._event(scope, workflow, 'execution_admitted', task=task, attempt=attempt, mode=mode)

    def confirm_started(self, scope, attempt_id, fence):
        with self.transaction():
            attempt, task, workflow = self._owned(scope, attempt_id, fence)
            if attempt.started_at is not None:
                return attempt
            if workflow.state != 'running' or self._counter('queue_paused') or task.paused:
                raise StoreError('paused')
            attempt = changed(attempt, state='running', started_at=self.clock(), lease_until=self.clock() + 30.)
            self._save_attempt(attempt)
            instance = self._instance(attempt.instance_id)
            self._save_instance(changed(instance, state='running', revision=instance.revision + 1))
            task = changed(task, state='running', revision=task.revision + 1, updated_at=self.clock())
            self._save_task(task)
            if workflow.started_at is None:
                workflow = changed(workflow, started_at=self.clock(), deadline_at=self.clock() + workflow.budget.wall_seconds)
                self._counter('root_starts', 1)
            workflow = changed(workflow, dispatch_count=workflow.dispatch_count + 1, revision=workflow.revision + 1)
            self._save_workflow(workflow)
            self._event(scope, workflow, 'attempt_started', task=task, attempt=attempt, fence=fence)
            return attempt

    def spend(self, scope, attempt_id, fence, amount: BudgetAmount, *, key):
        self._role(scope, ('runtime',))
        amount = BudgetAmount.model_validate(amount.model_dump())
        with self.transaction():
            task = self.task(scope, self._attempt(attempt_id).task_id)
            found, result, digest = self._replay(scope, key, {'spend': attempt_id, 'fence': fence,
                                                             'amount': amount.model_dump(mode='json')})
            if found:
                return AgentAttempt.model_validate(result)
            attempt, task, workflow = self._owned(scope, attempt_id, fence, running=True)
            self._debit(scope, attempt, task, workflow, amount)
            result = self._attempt(attempt_id)
            self._remember(scope, key, task.project_id, digest, result)
            return result

    def _debit(self, scope, attempt, task, workflow, amount):
        if amount.model_calls > attempt.reserved.model_calls or amount.tool_calls > attempt.reserved.tool_calls:
            raise StoreError('budget_exhausted', 'Spend exceeds owned reservation')
        self.db.execute('''UPDATE reservations SET model_reserved=model_reserved-?, tool_reserved=tool_reserved-?,
            model_spent=model_spent+?, tool_spent=tool_spent+? WHERE attempt_id=?''',
            (amount.model_calls, amount.tool_calls, amount.model_calls, amount.tool_calls, attempt.id))
        self._event(scope, workflow, 'budget_spent', task=task, attempt=attempt, amount=amount.model_dump())

    def recover(self, scope):
        self._role(scope, ('runtime',))
        with self.transaction():
            for workflow in self.workflows(scope):
                for attempt in self.attempts(scope, workflow.id):
                    if attempt.state not in {'assigned', 'running', 'stopping'}:
                        continue
                    self._reconcile(scope, workflow, attempt, 'host_restart')

    def _reconcile(self, scope, workflow, attempt, reason):
        task = self._task(attempt.task_id)
        self._save_task(changed(task, fence=task.fence + 1, revision=task.revision + 1))
        attempt = changed(attempt, state='reconciling')
        self._save_attempt(attempt)
        self.db.execute('UPDATE reservations SET uncertain=1 WHERE attempt_id=?', (attempt.id,))
        worker = self.db.execute('SELECT data FROM workers WHERE attempt_id=?', (attempt.id,)).fetchone()
        if worker is not None:
            record = WorkerRecord.model_validate_json(worker['data'])
            if not record.stop_confirmed:
                record = changed(record, state='uncertain')
                self.db.execute('UPDATE workers SET data=? WHERE id=?', (record.model_dump_json(), record.id))
                self._event(scope, workflow, 'worker_reconciling', task=task, attempt=attempt, worker_id=record.id, reason=reason)
        for row in self.db.execute("SELECT data FROM model_calls WHERE attempt_id=? AND state='running'", (attempt.id,)):
            call = changed(ModelCall.model_validate_json(row['data']), status='uncertain', late=True)
            self.db.execute('UPDATE model_calls SET state=?,data=? WHERE id=?', ('uncertain', call.model_dump_json(), call.id))
            self._event(scope, workflow, 'model_uncertain', task=task, attempt=attempt, call=call.model_dump(mode='json'), reason=reason)
        for row in self.db.execute("SELECT data FROM tool_calls WHERE attempt_id=? AND state='running'", (attempt.id,)):
            call = changed(ToolCall.model_validate_json(row['data']), status='uncertain', late=True)
            self.db.execute('UPDATE tool_calls SET state=?,data=? WHERE id=?', ('uncertain', call.model_dump_json(), call.id))
            self._event(scope, workflow, 'tool_uncertain', task=task, attempt=attempt, call=call.model_dump(mode='json'), reason=reason)
        instance = self._instance(attempt.instance_id)
        self._save_instance(changed(instance, state='reconciling', revision=instance.revision + 1))
        self._event(scope, workflow, 'attempt_reconciling', task=task, attempt=attempt, reason=reason)
        return self._attempt(attempt.id)

    def heartbeat(self, scope, attempt_id, fence):
        with self.transaction():
            attempt, task, workflow = self._owned(scope, attempt_id, fence)
            attempt = changed(attempt, lease_until=self.clock() + 30.)
            self._save_attempt(attempt)
            self._event(scope, workflow, 'attempt_heartbeat', task=task, attempt=attempt, fence=fence)
            return attempt

    def expire(self, scope):
        """Revoke expired ownership without asserting that its process has stopped."""
        self._role(scope, ('runtime',))
        expired = []
        with self.transaction():
            now = self.clock()
            for workflow in self.workflows(scope):
                if not workflow.active_slot:
                    continue
                deadline = workflow.deadline_at is not None and (now >= workflow.deadline_at or now < workflow.started_at)
                for attempt in self.attempts(scope, workflow.id):
                    if attempt.state in {'assigned', 'running', 'stopping'} and (deadline or now >= attempt.lease_until):
                        expired.append(self._reconcile(scope, workflow, attempt, 'wall_deadline' if deadline else 'lease_expired'))
                if deadline and workflow.state not in {'incomplete', 'cancelling'}:
                    active = any(a.state in ACTIVE for a in self.attempts(scope, workflow.id))
                    workflow = changed(workflow, state='incomplete', active_slot=active, revision=workflow.revision + 1)
                    self._save_workflow(workflow)
                    self._event(scope, workflow, 'workflow_deadline', task=self._task(workflow.root_task_id))
        return expired

    def confirm_stopped(self, scope, attempt_id, fence, *, outcome, expenditure_confirmed=False):
        with self.transaction():
            return self._confirm_stopped(scope, attempt_id, fence, outcome=outcome,
                expenditure_confirmed=expenditure_confirmed)

    def _confirm_stopped(self, scope, attempt_id, fence, *, outcome, expenditure_confirmed=False):
        """Settle a known stop inside the caller's atomic host operation."""
        if not self.db.in_transaction:
            raise RuntimeError('Stop settlement must be atomic')
        self._role(scope, ('runtime',))
        if outcome not in {'retry_wait', 'failed', 'cancelled'}:
            raise StoreError('completion_gate', 'A result needs immutable artifacts and review')
        attempt = self._attempt(attempt_id)
        task = self.task(scope, attempt.task_id)
        workflow = self._workflow(attempt.workflow_id)
        key = 'stop:' + attempt_id
        found, result, digest = self._replay(scope, key, {'stop': attempt_id, 'fence': fence,
            'outcome': outcome, 'expenditure_confirmed': expenditure_confirmed})
        if found:
            return AgentAttempt.model_validate(result)
        if attempt.fence != fence or task.current_attempt_id != attempt_id or attempt.state not in ACTIVE:
            raise StoreError('fence', 'Stop acknowledgement does not own the current attempt')
        if self.db.execute("SELECT 1 FROM integrations WHERE attempt_id=? AND state IN ('preparing','verifying','applying','reconciling')",
                           (attempt_id,)).fetchone():
            raise StoreError('reconcile_required', 'Resolve the integration journal before releasing its assignment')
        if self.db.execute("SELECT 1 FROM model_calls WHERE attempt_id=? AND state IN ('running','uncertain')",
                           (attempt_id,)).fetchone():
            raise StoreError('resource_busy', 'Confirm the model transport outcome before releasing ownership')
        if self.db.execute("SELECT 1 FROM tool_calls WHERE attempt_id=? AND state IN ('running','uncertain')",
                           (attempt_id,)).fetchone():
            raise StoreError('resource_busy', 'Confirm the tool outcome before releasing ownership')
        uncertain = self.db.execute('SELECT uncertain FROM reservations WHERE attempt_id=?', (attempt_id,)).fetchone()[0]
        if uncertain and not expenditure_confirmed:
            raise StoreError('uncertain', 'Reconcile unknown spending before releasing the reservation')
        terminal = 'cancelled' if workflow.state == 'cancelling' or (task.permissions and not task.permissions.approved) else outcome
        attempt_state = 'interrupted' if terminal == 'retry_wait' else terminal
        attempt = changed(attempt, state=attempt_state, stop_confirmed=True)
        self._save_attempt(attempt)
        self.db.execute('UPDATE reservations SET model_reserved=0,tool_reserved=0,uncertain=0 WHERE attempt_id=?', (attempt_id,))
        self._save_task(changed(task, state=terminal, current_attempt_id=None, fence=task.fence + 1,
                                revision=task.revision + 1, updated_at=self.clock()))
        instance = self._instance(attempt.instance_id)
        self._save_instance(changed(instance, state='idle', current_attempt_id=None, revision=instance.revision + 1))
        active = any(a.state in ACTIVE for a in self.attempts(scope, workflow.id))
        if not active and workflow.state in {'cancelling', 'pausing', 'incomplete'}:
            state = {'cancelling': 'cancelled', 'pausing': 'paused', 'incomplete': 'incomplete'}[workflow.state]
            workflow = changed(workflow, state=state, active_slot=state == 'paused', revision=workflow.revision + 1)
            self._save_workflow(workflow)
        self._event(scope, workflow, 'attempt_stopped', task=task, attempt=attempt, outcome=terminal)
        result = self._attempt(attempt_id)
        self._remember(scope, key, task.project_id, digest, result)
        return result
