"""Trusted host registration; callers resolve permitted UI selectors first."""
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from ..domain.orchestration import AccessScope, TaskCommand, WorkItem
from ..domain.project import (
    CanonicalProject,
    ProjectStopGrant,
    TaskStopPage,
    TaskStopReceipt,
)
from ..domain.source import SourceClaim
from .sources import Sources, _overlap
from .store import StoreError, changed


class Projects:
    def __init__(self, store):
        self.store = store

    def get(self, identity) -> CanonicalProject:
        """Host-only metadata/audit read; this does not issue project scope."""
        return self.store._load('canonical_projects', identity, CanonicalProject)

    def _find(self, root):
        row = self.store.db.execute('SELECT id FROM canonical_projects WHERE root_path=?', (root.path,)).fetchone()
        if row is None:
            row = self.store.db.execute('SELECT id FROM canonical_projects WHERE root_device=? AND root_inode=?', root.identity).fetchone()
        if row is None:
            return None
        project = self.get(row['id'])
        if project.root.path != root.path:
            raise StoreError('project_alias_unverified')
        if project.root != root:
            raise StoreError('project_changed')
        if project.state != 'active':
            raise StoreError('project_revoked')
        return project

    def _current(self, project):
        if Sources(self.store).prepare(Path(project.root.path)) != project.root:
            raise StoreError('project_changed')
        current = self.get(project.id)
        if current.state != 'active':
            raise StoreError('project_revoked')
        return current

    def register(self, root: Path, *, actor_id: str) -> CanonicalProject:
        """Internal host API: root must come from the allowed-folder resolver."""
        reference = Sources(self.store).prepare(root)
        with self.store.transaction():
            project = self._find(reference)
            if project is None:
                project = CanonicalProject(id=uuid4().hex, root=reference, created_by=actor_id,
                    created_at=self.store.clock())
                self.store.db.execute('INSERT INTO canonical_projects VALUES (?,?,?,?,?,?)',
                    (project.id, reference.path, *reference.identity, project.state, project.model_dump_json()))
        project = self._current(project)
        self._grant(project, actor_id)
        return project

    def resolve(self, root: Path, *, actor_id: str | None = None) -> CanonicalProject:
        reference = Sources(self.store).prepare(root)
        project = self._find(reference)
        if project is None:
            raise StoreError('project_not_registered')
        project = self._current(project)
        if actor_id is not None:
            self._grant(project, actor_id)
        return project

    def _grant(self, project, actor_id):
        """Persist the first successful physical resolution, never fresh authority."""
        grant = ProjectStopGrant(actor_id=actor_id, project_id=project.id,
            project_revision=project.revision, granted_at=self.store.clock())
        if self.store.db.execute('SELECT 1 FROM project_grants WHERE actor_id=? AND project_id=?',
                (actor_id, project.id)).fetchone():
            return
        with self.store.transaction():
            if self.get(project.id) != project:
                raise StoreError('project_changed')
            self.store.db.execute('INSERT INTO project_grants VALUES (?,?,?) ON CONFLICT DO NOTHING',
                (actor_id, project.id, grant.model_dump_json()))

    def stop_command(self, actor_id: str, task_id: str, command: TaskCommand):
        """Historical grants authorize only canonical pause/cancel, even after root loss."""
        command = TaskCommand.model_validate(command.model_dump())
        if command.operation not in {'pause', 'cancel'} or command.changes:
            raise StoreError('permission')
        try:
            task = self.store._task(task_id)
        except StoreError as error:
            if error.code == 'not_found':
                raise StoreError('scope') from error
            raise
        row = self.store.db.execute('SELECT data FROM project_grants WHERE actor_id=? AND project_id=?',
            (actor_id, task.project_id)).fetchone()
        if row is None:
            raise StoreError('scope')
        self._check_stop_grant(actor_id, task.project_id, row['data'])
        # Grants and task project IDs are immutable. Store.command rechecks scope,
        # revision, replay and fencing atomically; no filesystem check spans it.
        scope = AccessScope(actor_id=actor_id, project_ids=frozenset({task.project_id}), role='owner')
        return self.store.command(scope, task_id, command)

    def _check_stop_grant(self, actor_id, project_id, data):
        try:
            grant = ProjectStopGrant.model_validate_json(data)
        except ValidationError as error:
            raise StoreError('scope') from error
        if (grant.actor_id, grant.project_id) != (actor_id, project_id):
            raise StoreError('scope')

    def stop_page(self, actor_id: str, *, after=0, limit=50) -> TaskStopPage:
        """Operational Stop handles only; no fresh scope, content or result refresh."""
        if not 0 <= after <= 2**63 - 1 or not 1 <= limit <= 100:
            raise StoreError('cursor')
        rows = self.store.db.execute('''SELECT t.rowid AS cursor,t.id,t.data,w.project_id,g.data AS grant_data
            FROM tasks t JOIN workflows w ON w.id=t.workflow_id
            JOIN project_grants g ON g.project_id=w.project_id
            WHERE g.actor_id=? AND t.rowid>? AND (
                json_extract(t.data,'$.current_attempt_id') IS NOT NULL OR (
                    NOT json_extract(t.data,'$.archived') AND json_extract(t.data,'$.retired_by_result') IS NULL
                    AND t.state NOT IN ('succeeded','failed','cancelled')))
            ORDER BY t.rowid LIMIT ?''', (actor_id, after, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            task = WorkItem.model_validate_json(row['data'])
            if (task.id, task.project_id) != (row['id'], row['project_id']):
                raise StoreError('scope')
            self._check_stop_grant(actor_id, task.project_id, row['grant_data'])
            items.append(TaskStopReceipt(task_id=task.id, revision=task.revision, state=task.state, paused=task.paused))
        return TaskStopPage(items=items, next_cursor=rows[limit - 1]['cursor'] if len(rows) > limit else None)

    def _busy(self, project):
        db = self.store.db
        if db.execute("SELECT 1 FROM attempts JOIN workflows ON workflows.id=attempts.workflow_id "
                "WHERE project_id=? AND attempts.state IN ('assigned','running','stopping','reconciling')", (project.id,)).fetchone():
            return True
        for table in ('model_calls', 'tool_calls'):
            if db.execute(f"SELECT 1 FROM {table} calls JOIN attempts ON attempts.id=calls.attempt_id "
                    "JOIN workflows ON workflows.id=attempts.workflow_id WHERE project_id=? "
                    "AND calls.state IN ('running','uncertain')", (project.id,)).fetchone():
                return True
        return any(_overlap(project.root, SourceClaim.model_validate_json(row['data']).root)
            for row in db.execute("SELECT data FROM source_claims WHERE state!='released'"))

    def revoke(self, scope, project_id, *, expected_revision) -> CanonicalProject:
        """Quiescent, terminal internal revocation; user folders are untouched."""
        store = self.store
        store._role(scope, ('owner',))
        store._scope(scope, project_id)
        with store.transaction():
            project = self.get(project_id)
            if project.state == 'revoked' and expected_revision == project.revision - 1:
                return project
            if project.revision != expected_revision:
                raise StoreError('revision', 'Stale project', project)
            if project.state == 'revoked':
                return project
            if self._busy(project):
                raise StoreError('project_busy')
            project = changed(project, state='revoked', revision=project.revision + 1, revoked_at=store.clock())
            store.db.execute('UPDATE canonical_projects SET state=?,data=? WHERE id=?',
                (project.state, project.model_dump_json(), project.id))
            for workflow in store.workflows(scope):
                if workflow.project_id == project.id and workflow.active_slot:
                    # No writer/effect exists: relinquish scheduling capacity,
                    # without fabricating stop proof or changing task contracts.
                    workflow = changed(workflow, state='incomplete', active_slot=False, revision=workflow.revision + 1)
                    store._save_workflow(workflow)
                    store._event(scope, workflow, 'project_revoked', task=store.task(scope, workflow.root_task_id),
                        project_revision=project.revision)
            return project
