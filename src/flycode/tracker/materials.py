"""Scoped historical material bytes; downloading never asserts a current pass."""

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Literal

from ..domain.orchestration import (
    IntegrationBinding,
    QueueModel,
    ReviewBinding,
    WorkerBinding,
)
from ..domain.results import (
    IntegrationRecord,
    PatchArtifact,
    ReviewExecution,
    ReviewRecord,
)
from ..domain.verification import EvidenceManifest
from ..orchestration.sources import Sources
from ..orchestration.store import StoreError
from ..verification.runner import OUTPUT_LIMIT, REPORT_LIMIT
from ..verification.snapshot import digest


class MaterialView(QueueModel):
    id: str
    kind: Literal['patch', 'review', 'integration']
    record_id: str
    stage: str
    name: str
    sha256: str


class MaterialPage(QueueModel):
    task_id: str
    items: list[MaterialView]
    unavailable: tuple[str, ...] = ()


class ResultMaterials:
    def __init__(self, store):
        self.store = store

    def _facts(self, scope, identity, selected=None):
        store = self.store
        with store.transaction():
            task = store.task(scope, identity)
            patches, reviews = {}, []
            for row in store.db.execute('SELECT a.id,a.attempt_id,a.data FROM artifacts a JOIN attempts t ON t.id=a.attempt_id '
                    'WHERE t.task_id=? ORDER BY a.rowid', (identity,)):
                if selected is not None and row['id'] != selected:
                    continue
                artifact = PatchArtifact.model_validate_json(row['data'])
                if artifact.id != row['id'] or artifact.attempt_id != row['attempt_id']:
                    raise StoreError('material_changed')
                patches[artifact.id] = artifact
            own_patch = selected is not None and selected in patches
            for row in store.db.execute('SELECT r.id,r.artifact_id,r.attempt_id,r.data FROM reviews r JOIN attempts t ON t.id=r.attempt_id '
                    'JOIN artifacts a ON a.id=r.artifact_id WHERE t.task_id=? OR '
                    "json_extract(a.data,'$.task_id')=? ORDER BY r.rowid", (identity, identity)):
                if own_patch or (selected is not None and selected not in {row['id'], row['artifact_id']}):
                    continue
                review = ReviewRecord.model_validate_json(row['data'])
                execution = store._load('review_executions', review.reviewer_attempt_id, ReviewExecution)
                checker = store.task(scope, execution.task_id)
                artifact = store._load('artifacts', review.artifact_id, PatchArtifact)
                if (checker.workflow_id != task.workflow_id or checker.project_id != task.project_id
                        or execution.review_id != review.id or execution.artifact_id != artifact.id):
                    raise StoreError('scope')
                attempt = store._attempt(review.reviewer_attempt_id)
                if attempt.execution_binding_id is None:
                    raise StoreError('material_unavailable')
                binding = store._load('review_bindings', attempt.execution_binding_id, ReviewBinding)
                if (review.id != row['id'] or review.artifact_id != row['artifact_id']
                        or review.reviewer_attempt_id != row['attempt_id'] or artifact.id != review.artifact_id
                        or attempt.task_id != checker.id or attempt.workflow_id != task.workflow_id
                        or review.patch_hash != artifact.patch_hash or review.reviewer_fence != attempt.fence
                        or review.reviewer_contract_hash != attempt.contract_hash
                        or execution.attempt_id != attempt.id or execution.project_id != task.project_id
                        or execution.workflow_id != task.workflow_id or execution.state != 'completed'
                        or execution.patch_hash != review.patch_hash or execution.fence != review.reviewer_fence
                        or execution.approval_hash != review.approval_hash or execution.reruns != review.reruns
                        or binding.id != attempt.execution_binding_id or binding.runtime_root != execution.runtime_root
                        or binding.task_id != checker.id or binding.project_id != task.project_id
                        or binding.workflow_id != task.workflow_id or binding.contract_hash != attempt.contract_hash
                        or binding.semantic_revision != attempt.semantic_revision
                        or binding.workflow_semantic_revision != attempt.workflow_semantic_revision
                        or binding.artifact_id != artifact.id or binding.patch_hash != artifact.patch_hash
                        or binding.author_attempt_id != artifact.attempt_id or binding.author_fence != artifact.fence
                        or binding.approval_hash != review.approval_hash or binding.reruns != review.reruns):
                    raise StoreError('material_changed')
                reviews.append((review, execution, attempt, binding))
                patches[artifact.id] = artifact
            artifacts = []
            for artifact in patches.values():
                author = store.task(scope, artifact.task_id)
                attempt = store._attempt(artifact.attempt_id)
                if (artifact.project_id != task.project_id or artifact.workflow_id != task.workflow_id
                        or author.workflow_id != task.workflow_id or attempt.task_id != author.id):
                    raise StoreError('scope')
                if attempt.execution_binding_id is None:
                    raise StoreError('material_unavailable')
                binding = store._load('worker_bindings', attempt.execution_binding_id, WorkerBinding)
                if binding.task_id != author.id or binding.project_id != task.project_id or binding.workflow_id != task.workflow_id:
                    raise StoreError('scope')
                if (binding.id != attempt.execution_binding_id or attempt.workflow_id != artifact.workflow_id
                        or artifact.fence != attempt.fence or artifact.semantic_revision != attempt.semantic_revision
                        or artifact.contract_hash != attempt.contract_hash or binding.contract_hash != attempt.contract_hash
                        or binding.semantic_revision != attempt.semantic_revision
                        or binding.workflow_semantic_revision != attempt.workflow_semantic_revision):
                    raise StoreError('material_changed')
                authority = attempt.model_dump(include={'id', 'task_id', 'workflow_id', 'execution_binding_id',
                    'semantic_revision', 'workflow_semantic_revision', 'contract_hash', 'fence'}) if selected is not None else attempt
                artifacts.append((artifact, binding, authority))
            integrations = []
            for row in store.db.execute('SELECT id,attempt_id,data FROM integrations WHERE workflow_id=? ORDER BY rowid', (task.workflow_id,)):
                if selected is not None and row['id'] != selected:
                    continue
                record = IntegrationRecord.model_validate_json(row['data'])
                if record.id != row['id'] or record.attempt_id != row['attempt_id']:
                    raise StoreError('material_changed')
                if record.project_id != task.project_id or record.workflow_id != task.workflow_id:
                    raise StoreError('scope')
                attempt = store._attempt(record.attempt_id)
                owner = store.task(scope, attempt.task_id)
                if attempt.execution_binding_id is None or record.runtime_root is None:
                    raise StoreError('material_unavailable')
                binding = store._load('review_bindings', attempt.execution_binding_id, IntegrationBinding)
                if (attempt.workflow_id != task.workflow_id or owner.project_id != task.project_id
                        or record.fence != attempt.fence or binding.id != attempt.execution_binding_id
                        or binding.task_id != owner.id or binding.project_id != task.project_id
                        or binding.workflow_id != task.workflow_id or binding.contract_hash != attempt.contract_hash
                        or binding.semantic_revision != attempt.semantic_revision
                        or binding.workflow_semantic_revision != attempt.workflow_semantic_revision
                        or binding.runtime_root != record.runtime_root):
                    raise StoreError('material_changed')
                integrations.append((record, attempt, binding))
            return task, artifacts, reviews, integrations

    @contextmanager
    def _opened(self, root, parts):
        root = Path(root)
        expected = Sources(self.store).prepare(root)
        if str(root) != expected.path:
            raise StoreError('material_changed')
        descriptors, links = [], []
        try:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(descriptor)
            if (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) != expected.identity:
                raise StoreError('material_changed')
            for part in parts:
                if part in {'', '.', '..'} or '/' in part:
                    raise StoreError('material_changed')
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                descriptors.append(child)
                links.append((descriptor, part, child))
                descriptor = child
            yield descriptor
            for parent, name, child in links:
                before, after = os.fstat(child), os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (before.st_dev, before.st_ino, before.st_mode) != (after.st_dev, after.st_ino, after.st_mode):
                    raise StoreError('material_changed')
            if Sources(self.store).prepare(root) != expected:
                raise StoreError('material_changed')
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _read(self, root, name, limit):
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {'.', '..'} for part in path.parts):
            raise StoreError('material_changed')
        with self._opened(root, path.parts[:-1]) as descriptor:
            leaf = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=descriptor)
            with os.fdopen(leaf, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                    raise StoreError('material_changed')
                raw = stream.read(limit + 1)
                for after in (os.fstat(stream.fileno()), os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)):
                    if any(getattr(before, field) != getattr(after, field) for field in (
                            'st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns')):
                        raise StoreError('material_changed')
                if len(raw) > limit:
                    raise StoreError('material_changed')
                return raw

    def _collect(self, scope, identity, requested=None):
        if self.store.db.in_transaction:
            raise RuntimeError('Material inspection cannot hold a SQL transaction')
        selected = requested[:32] if requested is not None else None
        facts = self._facts(scope, identity, selected)
        task, artifacts, reviews, integrations = facts
        items, result, unavailable = [], None, []

        def add(kind, record, stage, name, sha, root, path, limit):
            nonlocal result
            item = MaterialView(id=record + digest({'task': identity, 'kind': kind, 'record': record,
                'stage': stage, 'name': name, 'sha256': sha})[:32], kind=kind, record_id=record,
                stage=stage, name=name, sha256=sha)
            items.append(item)
            if item.id == requested:
                raw = self._read(root, path, limit)
                if hashlib.sha256(raw).hexdigest() != sha:
                    raise StoreError('material_changed')
                result = item, raw

        def evidence(kind, record, stage, verdict, root):
            prefix = 'verification/' + verdict.evidence_id + '/'
            raw = self._read(root, prefix + 'manifest.json', REPORT_LIMIT)
            if hashlib.sha256(raw).hexdigest() != verdict.evidence_hash:
                raise StoreError('material_changed')
            manifest = EvidenceManifest.model_validate_json(raw)
            if (manifest.evidence_id != verdict.evidence_id or manifest.subject_hash != verdict.subject_hash
                    or manifest.plan_hash != verdict.plan_hash):
                raise StoreError('material_changed')
            for name, sha in manifest.artifacts.items():
                if re.fullmatch(r'attempt-(?:[0-9]+|[0-9a-f]{32})/(?:stdout\.txt|stderr\.txt|junit\.xml|cases\.jsonl)', name):
                    add(kind, record, stage, name, sha, root, prefix + name,
                        OUTPUT_LIMIT if name.endswith('.txt') else REPORT_LIMIT)
            if self._read(root, prefix + 'manifest.json', REPORT_LIMIT) != raw:
                raise StoreError('material_changed')

        bundles = [('patch', artifact.id, '', binding.runtime_root, artifact)
            for artifact, binding, _ in artifacts if selected is None or artifact.id == selected]
        bundles.extend(('review', review.id, review.status, execution.runtime_root, review.verdict)
            for review, execution, _, _ in reviews if selected is None or review.id == selected)
        for record, owner, _ in integrations:
            for stage, verdicts in [('combined', record.verdicts), ('applied', record.applied_verdicts)]:
                for author, verdict in verdicts.items():
                    if author == task.id or owner.task_id == task.id:
                        bundles.append(('integration', record.id, stage + ':' + author, record.runtime_root, verdict))
        for kind, record, stage, root, payload in bundles:
            beginning, previous = len(items), result
            try:
                if kind == 'patch':
                    artifact = payload
                    prefix = 'artifacts/' + artifact.id + '/'
                    raw = self._read(root, prefix + 'manifest.json', REPORT_LIMIT)
                    if (hashlib.sha256(raw).hexdigest() != artifact.patch_hash or json.loads(raw) != artifact.model_dump(
                            mode='json', exclude={'patch_hash'}, exclude_unset=True)):
                        raise StoreError('material_changed')
                    for change in artifact.changes:
                        for version_name, version in [('baseline', change.before), ('target', change.after)]:
                            if version:
                                add('patch', artifact.id, version_name, change.path, version.sha256, root,
                                    prefix + version_name + '/' + change.path, REPORT_LIMIT)
                    if self._read(root, prefix + 'manifest.json', REPORT_LIMIT) != raw:
                        raise StoreError('material_changed')
                else:
                    evidence(kind, record, stage, payload, root)
            except (OSError, ValueError) as error:
                if isinstance(error, StoreError) and error.code == 'scope':
                    raise
                del items[beginning:]
                result = previous
                unavailable.append(record)
        current = self._facts(scope, identity, selected)
        if requested is not None:
            # Historical reads depend on their exact provenance, not later task
            # status/title changes or another integration's live accounting.
            latest = current[0]
            stable = ((latest.id, latest.project_id, latest.workflow_id) == (task.id, task.project_id, task.workflow_id)
                and current[1:] == facts[1:])
        else:
            stable = current == facts
        if not stable:
            raise StoreError('revision')
        if requested is not None and result is None and unavailable:
            raise StoreError('material_changed')
        return MaterialPage(task_id=task.id, items=items, unavailable=tuple(dict.fromkeys(unavailable))), result

    def page(self, scope, identity):
        return self._collect(scope, identity)[0]

    def read(self, scope, identity, material_id):
        _, result = self._collect(scope, identity, material_id)
        if result is None:
            raise StoreError('not_found')
        return result
