"""Tracker access to the same canonical objects used by the dispatcher."""

import json

from ..domain.orchestration import AccessScope, QueueEvent, WorkflowDraft
from ..orchestration.store import Store, StoreError
from .commands import CommentCommand, comment, execute
from .projection import AgentCard, TaskCard, TaskEventPage, TaskPage, task_card


class TrackerService:
    def __init__(self, store: Store):
        self.store = store

    def create(self, scope: AccessScope, draft: WorkflowDraft, *, key: str):
        return self.store.create_workflow(scope, draft, key=key)

    def command(self, scope: AccessScope, task_id: str, data: dict):
        return execute(self.store, scope, task_id, data)

    def task(self, scope: AccessScope, task_id: str) -> TaskCard:
        self.store.refresh_results(scope, self.store.task(scope, task_id).workflow_id)
        return self._card(scope, task_id)

    def _card(self, scope: AccessScope, task_id: str) -> TaskCard:
        task = self.store.task(scope, task_id)
        workflow = self.store.workflow(scope, task.workflow_id)
        attempts = [attempt for attempt in self.store.attempts(scope, workflow.id) if attempt.task_id == task.id]
        current = next((a for a in attempts if a.id == task.current_attempt_id), None)
        return task_card(task, workflow, self.store.tasks(scope, workflow.id),
                         current or (attempts[-1] if attempts else None), self.store.last_event(scope, workflow.id, task.id))

    def board(self, scope: AccessScope, workflow_id: str, *, archived: bool = False) -> list[TaskCard]:
        self.store.refresh_results(scope, workflow_id)
        return [self._card(scope, task.id) for task in self.store.tasks(scope, workflow_id) if archived or not task.archived]

    def agents(self, scope: AccessScope) -> list[AgentCard]:
        self.store.refresh_results(scope)
        result = []
        for instance in self.store.instances(scope):
            attempt = self.store._attempt(instance.current_attempt_id) if instance.current_attempt_id else None
            task = self.store.task(scope, attempt.task_id) if attempt else None
            result.append(AgentCard(instance=instance, task=task, attempt=attempt))
        return result


    def comment(self, scope: AccessScope, task_id: str, data: CommentCommand) -> QueueEvent:
        return comment(self.store, scope, task_id, data)

    def page(self, scope: AccessScope, *, after=0, limit=50, project_id=None, state=None, q='', archived=False) -> TaskPage:
        if not 0 <= after <= 2**63 - 1 or not 1 <= limit <= 100 or len(q) > 200:
            raise StoreError('cursor')
        if project_id is not None:
            self.store._scope(scope, project_id)
        projects = json.dumps([project_id] if project_id else sorted(scope.project_ids))
        self.store.refresh_results(scope)
        self.store.db.create_function('flycode_casefold', 1, lambda value: (value or '').casefold(), deterministic=True)
        where = "t.rowid>? AND w.project_id IN (SELECT value FROM json_each(?))"
        values = [after, projects]
        if not archived:
            where += " AND NOT json_extract(t.data,'$.archived')"
        if state is not None:
            where += ' AND t.state=?'
            values.append(state)
        if q:
            where += " AND instr(flycode_casefold(json_extract(t.data,'$.short_id')||char(10)||json_extract(t.data,'$.title')||char(10)||json_extract(t.data,'$.objective')),?)>0"
            values.append(q.casefold())
        with self.store.transaction():
            rows = self.store.db.execute('SELECT t.rowid AS cursor,t.id FROM tasks t JOIN workflows w ON w.id=t.workflow_id '
                'WHERE ' + where + ' ORDER BY t.rowid LIMIT ?', (*values, limit + 1)).fetchall()
            return TaskPage(items=[self._card(scope, row['id']) for row in rows[:limit]],
                next_cursor=rows[limit - 1]['cursor'] if len(rows) > limit else None,
                cursor=self.store.db.execute('SELECT COALESCE(MAX(sequence),0) FROM events').fetchone()[0])

    def events(self, scope: AccessScope, *, after=0, limit=500) -> tuple[list[QueueEvent], int]:
        if not 0 <= after <= 2**63 - 1 or not 1 <= limit <= 1000:
            raise StoreError('cursor')
        with self.store.transaction():
            rows = self.store.db.execute('SELECT sequence,data FROM events WHERE sequence>? '
                'AND project_id IN (SELECT value FROM json_each(?)) ORDER BY sequence LIMIT ?',
                (after, json.dumps(sorted(scope.project_ids)), limit)).fetchall()
            frontier = self.store.db.execute('SELECT COALESCE(MAX(sequence),0) FROM events').fetchone()[0]
            events = [QueueEvent(sequence=row['sequence'], **json.loads(row['data'])) for row in rows]
            return events, events[-1].sequence if len(events) == limit else frontier

    def task_events(self, scope: AccessScope, task_id: str, *, after=0, limit=50) -> TaskEventPage:
        if not 0 <= after <= 2**63 - 1 or not 1 <= limit <= 200:
            raise StoreError('cursor')
        task = self.store.task(scope, task_id)
        with self.store.transaction():
            rows = self.store.db.execute('SELECT sequence,data FROM events WHERE workflow_id=? AND sequence>? '
                "AND json_extract(data,'$.task_id')=? ORDER BY sequence LIMIT ?",
                (task.workflow_id, after, task.id, limit + 1)).fetchall()
            return TaskEventPage(items=[QueueEvent(sequence=row['sequence'], **json.loads(row['data'])) for row in rows[:limit]],
                next_cursor=rows[limit - 1]['sequence'] if len(rows) > limit else None,
                cursor=self.store.db.execute('SELECT COALESCE(MAX(sequence),0) FROM events').fetchone()[0])
