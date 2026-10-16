"""Publish exact stopped-worker bytes without trusting an agent's success claim."""

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import WorkerRecord
from ..domain.results import FileChange, FileVersion, PatchArtifact
from ..domain.verification import SubjectRef
from ..verification.acceptance import input_identity
from ..verification.evidence import atomic_json
from ..verification.snapshot import capture, input_files
from .store import Store, StoreError, changed, covers
from .tools import _path
from .workspaces import Workspaces


class Artifacts:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.workspaces = Workspaces(store, self.root / 'workspaces')
        self.directory = self.root / 'artifacts'

    def _record(self, scope, identity) -> PatchArtifact:
        record = self.store._load('artifacts', identity, PatchArtifact)
        self.store.task(scope, record.task_id)
        return record

    def for_attempt(self, scope, attempt_id) -> PatchArtifact:
        self.store.task(scope, self.store._attempt(attempt_id).task_id)
        row = self.store.db.execute('SELECT id FROM artifacts WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'artifact')
        return self.get(scope, row['id'])

    def get(self, scope, identity) -> PatchArtifact:
        if self.store.db.in_transaction:
            raise RuntimeError('Artifact inspection cannot run in a SQLite transaction')
        record = self._record(scope, identity)
        directory = self.directory / record.id
        try:
            if directory.is_symlink() or {path.name for path in directory.iterdir()} != {
                    'manifest.json', 'baseline', 'target', 'run-result.json'}:
                raise ValueError('Artifact inventory changed')
            for name in ('manifest.json', 'baseline', 'target', 'run-result.json'):
                if (directory / name).is_symlink():
                    raise ValueError('Artifact link is unsupported')
            raw = (directory / 'manifest.json').read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.patch_hash or json.loads(raw) != record.model_dump(
                    mode='json', exclude={'patch_hash'}):
                raise ValueError('Artifact manifest changed')
            if hashlib.sha256((directory / 'run-result.json').read_bytes()).hexdigest() != record.result_hash:
                raise ValueError('Worker result changed')
            for name, snapshot in (('baseline', record.baseline), ('target', record.target)):
                _, excluded = input_files(directory / name)
                if excluded or input_identity(directory / name) != (snapshot.files, snapshot.modes):
                    raise ValueError('Artifact bytes changed')
        except (OSError, ValueError) as error:
            raise StoreError('artifact_integrity', 'The immutable result changed') from error
        return record

    def publish(self, scope, attempt_id, fence) -> PatchArtifact:
        store = self.store
        store._role(scope, ('runtime',))
        if store.db.in_transaction:
            raise RuntimeError('Artifact publication cannot run in a SQLite transaction')
        row = store.db.execute('SELECT id FROM artifacts WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row:
            record = self.get(scope, row['id'])
            if record.fence != fence:
                raise StoreError('fence')
            return record
        attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
        workspace = self.workspaces.get(scope, attempt_id)
        worker_row = store.db.execute('SELECT data FROM workers WHERE attempt_id=?', (attempt_id,)).fetchone()
        worker = WorkerRecord.model_validate_json(worker_row['data']) if worker_row else None
        if worker is None or not worker.stop_confirmed or worker.error or not worker.result_hash or worker.fence != fence:
            raise StoreError('stop_unconfirmed')
        if task.paused or workflow.state != 'running' or store._counter('queue_paused'):
            raise StoreError('paused')
        source = self.workspaces.root / workspace.id
        result_bytes = (source / 'run-result.json').read_bytes()
        if hashlib.sha256(result_bytes).hexdigest() != worker.result_hash:
            raise StoreError('artifact_integrity', 'Worker result changed')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = uuid4().hex
        directory = self.directory / identity
        directory.mkdir(mode=0o700)
        try:
            ref = SubjectRef(project_id=task.project_id, workflow_id=workflow.id, task_id=task.id, attempt_id=attempt.id)
            baseline = capture(source / 'baseline', directory / 'baseline', ref=ref)
            target = capture(source / 'repository', directory / 'target', ref=ref)
            changes = []
            def version(snapshot, name):
                return FileVersion(sha256=snapshot.files[name], mode=snapshot.modes[name]) if name in snapshot.files else None
            for name in sorted(set(baseline.files) | set(target.files)):
                before, after = version(baseline, name), version(target, name)
                if before != after:
                    _path(source / 'repository', name)
                    if not covers(task.permissions.write_scopes, (name,)) or (after is not None and after.mode & 0o7000):
                        raise StoreError('patch_scope', 'Changed bytes or modes exceed the approved write scope')
                    changes.append(FileChange(path=name, before=before, after=after))
            record = PatchArtifact(id=identity, project_id=task.project_id, workflow_id=workflow.id, task_id=task.id,
                attempt_id=attempt.id, fence=fence, semantic_revision=attempt.semantic_revision,
                contract_hash=attempt.contract_hash, workspace_id=workspace.id, result_hash=worker.result_hash,
                base=workspace.snapshot, baseline=baseline, target=target, changes=tuple(changes), created_at=store.clock(), patch_hash='')
            raw = record.model_dump_json(exclude={'patch_hash'})
            record = changed(record, patch_hash=hashlib.sha256(raw.encode()).hexdigest())
            atomic_json(directory / 'manifest.json', raw)
            (directory / 'run-result.json').write_bytes(result_bytes)
            with store.transaction():
                winner = store.db.execute('SELECT id FROM artifacts WHERE attempt_id=?', (attempt_id,)).fetchone()
                if winner is None:
                    attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
                    current_worker = WorkerRecord.model_validate_json(store.db.execute(
                        'SELECT data FROM workers WHERE attempt_id=?', (attempt_id,)).fetchone()['data'])
                    if not current_worker.stop_confirmed or current_worker.error or current_worker.result_hash != worker.result_hash:
                        raise StoreError('stop_unconfirmed')
                    if task.paused or workflow.state != 'running' or store._counter('queue_paused'):
                        raise StoreError('paused')
                    for table in ('model_calls', 'tool_calls'):
                        if store.db.execute(f"SELECT 1 FROM {table} WHERE attempt_id=? AND state IN ('running','uncertain')",
                                            (attempt_id,)).fetchone():
                            raise StoreError('resource_busy')
                    store.db.execute('INSERT INTO artifacts VALUES (?,?,?)', (identity, attempt_id, record.model_dump_json()))
                    store._save_attempt(changed(attempt, state='awaiting_review', stop_confirmed=True))
                    store._save_task(changed(task, state='awaiting_review', fence=task.fence + 1, revision=task.revision + 1))
                    instance = store._instance(attempt.instance_id)
                    store._save_instance(changed(instance, state='idle', current_attempt_id=None, revision=instance.revision + 1))
                    store.db.execute('UPDATE reservations SET model_reserved=0,tool_reserved=0,uncertain=0 WHERE attempt_id=?', (attempt_id,))
                    store._event(scope, workflow, 'artifact_published', task=task, attempt=attempt,
                        artifact_id=identity, patch_hash=record.patch_hash)
            if winner is not None:
                shutil.rmtree(directory)
                return self.get(scope, winner['id'])
            return self.get(scope, identity)
        except BaseException:
            if not store.db.execute('SELECT 1 FROM artifacts WHERE id=?', (identity,)).fetchone():
                shutil.rmtree(directory)
            raise
