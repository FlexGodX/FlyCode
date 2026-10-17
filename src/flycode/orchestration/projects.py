"""Trusted host registration; callers resolve permitted UI selectors first."""
from pathlib import Path
from uuid import uuid4

from ..domain.project import CanonicalProject
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
        return self._current(project)

    def resolve(self, root: Path) -> CanonicalProject:
        reference = Sources(self.store).prepare(root)
        project = self._find(reference)
        if project is None:
            raise StoreError('project_not_registered')
        return self._current(project)

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
