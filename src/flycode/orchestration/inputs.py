"""Require observed conflict inputs, independently of an agent's success claim."""

import hashlib
import json
from pathlib import Path

from ..domain.orchestration import ToolCall, WorkspaceRecord
from ..domain.results import IntegrationRecord, PatchArtifact
from .store import Store, StoreError
from .workspaces import Workspaces


class Inputs:
    def __init__(self, store: Store):
        self.store = store

    def conflict(self, scope, task) -> IntegrationRecord | None:
        self.store.task(scope, task.id)
        records = [IntegrationRecord.model_validate_json(row['data']) for row in self.store.db.execute(
            "SELECT data FROM integrations WHERE workflow_id=? AND state='conflict'", (task.workflow_id,))]
        matches = [record for record in records if record.resolution_task_id == task.id]
        if len(matches) > 1:
            raise StoreError('resolution_scope')
        return matches[0] if matches else None

    def require(self, scope, attempt_id, fence, *, root=None, deep=True):
        attempt, task, _ = self.store._owned(scope, attempt_id, fence, running=True)
        self._check(scope, attempt, task, root=root, deep=deep)

    def require_artifact(self, scope, artifact: PatchArtifact):
        attempt = self.store._attempt(artifact.attempt_id)
        task = self.store.task(scope, artifact.task_id)
        if self.conflict(scope, task) is None:
            return
        if artifact.fence != attempt.fence or artifact.contract_hash != task.contract_hash:
            raise StoreError('contract_changed')
        self._check(scope, attempt, task, deep=True)

    def _check(self, scope, attempt, task, *, root=None, deep=False):
        conflict = self.conflict(scope, task)
        if conflict is None:
            return
        if task.input_artifacts != conflict.artifact_hashes or task.base_snapshot_hash != conflict.source_before.subject_hash:
            raise StoreError('resolution_scope')
        from .artifacts import Artifacts

        artifacts = Artifacts(self.store, Path(conflict.runtime_root))
        required = {}
        for identity, expected in conflict.artifact_hashes.items():
            artifact = artifacts.get(scope, identity) if deep else artifacts._record(scope, identity)
            if artifact.patch_hash != expected:
                raise StoreError('artifact_integrity')
            for path in conflict.conflict_paths:
                if path not in artifact.target.files:
                    raise StoreError('resolution_input_unsupported', 'Required pinned path has no target file')
                required[('artifact', identity, path)] = artifact.target.files[path]
        row = self.store.db.execute('SELECT data FROM workspaces WHERE attempt_id=?', (attempt.id,)).fetchone()
        if row is None:
            raise StoreError('resolution_inputs_required', 'Prepare the owned workspace before reading inputs')
        workspace = WorkspaceRecord.model_validate_json(row['data'])
        if workspace.fence != attempt.fence:
            raise StoreError('fence')
        workspaces = Workspaces(self.store, Path(conflict.runtime_root) / 'workspaces')
        if deep:
            workspaces.get(scope, attempt.id)
        if root is not None and Path(root).resolve() != workspaces.root / workspace.id / 'repository':
            raise StoreError('resolution_scope')
        for path in conflict.conflict_paths:
            required[('workspace', workspace.id, path)] = None
        observed = {}
        for row in self.store.db.execute('''SELECT data FROM events WHERE workflow_id=?
                AND json_extract(data,'$.attempt_id')=?
                AND json_extract(data,'$.kind') IN ('artifact_read','workspace_read') ORDER BY sequence''',
                (task.workflow_id, attempt.id)):
            event = json.loads(row['data'])
            data, pinned = event['data'], event['kind'] == 'artifact_read'
            identity = data.get('artifact_id') if pinned else data.get('workspace_id')
            key = ('artifact' if pinned else 'workspace', identity, data.get('path'))
            if key not in required or (pinned and data.get('patch_hash') != conflict.artifact_hashes[identity]):
                continue
            call_row = self.store.db.execute('SELECT data FROM tool_calls WHERE id=?', (data.get('call_id'),)).fetchone()
            if call_row is None:
                continue
            call = ToolCall.model_validate_json(call_row['data'])
            arguments = {'path': key[2], **({'artifact_id': identity} if pinned else {})}
            digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if (call.attempt_id != attempt.id or call.fence != attempt.fence or call.status != 'completed'
                    or call.late or call.tool != 'read_file' or call.argument_hash != digest
                    or call.execution_profile != ('artifact-read-v1' if pinned else None)):
                continue
            observed[key] = data.get('sha256')
        missing = []
        for key, expected in required.items():
            value = observed.get(key)
            if value is None or (expected is not None and value != expected):
                arguments = {'path': key[2], **({'artifact_id': key[1]} if key[0] == 'artifact' else {})}
                missing.append('read_file ' + json.dumps(arguments))
            elif key[0] == 'workspace' and root is not None:
                try:
                    with (Path(root) / key[2]).open('rb') as stream:
                        current = stream.read(3501)
                    if len(current) > 3500 or hashlib.sha256(current).hexdigest() != value:
                        missing.append('workspace:' + key[2] + ' (changed; read the current file)')
                except OSError:
                    missing.append('workspace:' + key[2] + ' (unavailable)')
        if missing:
            detail = ', '.join(missing[:8]) + (f'; {len(missing) - 8} more required inputs' if len(missing) > 8 else '')
            raise StoreError('resolution_inputs_required', 'Read required inputs before writing or publishing: ' + detail)
