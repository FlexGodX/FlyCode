"""Compose exact checked predecessor deltas into a private task input."""

import hashlib
import os
import stat
from pathlib import Path

from ..domain.results import FileChange, FileVersion
from ..verification.evidence import read_regular
from ..verification.snapshot import (
    MAX_BYTES,
    MAX_FILES,
    capture,
    digest,
    input_files,
    write_files,
)
from .artifacts import Artifacts
from .completions import Completions
from .sources import Sources
from .store import StoreError


def versions(snapshot):
    return {name: (value, snapshot.modes[name]) for name, value in snapshot.files.items()}


def version(value):
    return (value.sha256, value.mode) if value is not None else None


def apply_versions(before, artifacts):
    result = dict(before)
    for artifact in artifacts:
        baseline, target = versions(artifact.baseline), versions(artifact.target)
        expected = {name for name in baseline.keys() | target.keys() if baseline.get(name) != target.get(name)}
        if expected != {change.path for change in artifact.changes} or len(expected) != len(artifact.changes):
            raise StoreError('artifact_lineage')
        for change in artifact.changes:
            if baseline.get(change.path) != version(change.before) or target.get(change.path) != version(change.after):
                raise StoreError('artifact_lineage')
            current = result.get(change.path)
            if current != version(change.before) and current != version(change.after):
                raise StoreError('dependency_conflict', change.path)
            if change.after is None:
                result.pop(change.path, None)
            else:
                result[change.path] = version(change.after)
    return result


class Compositions:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        self.completions = Completions(store, root)

    def metadata(self, scope, attempt):
        task = self.store.task(scope, attempt.task_id)
        expected = self.completions._inputs(scope, task, {}, frozenset())
        if expected != attempt.prerequisites:
            raise StoreError('dependency_changed')
        return expected

    def records(self, scope, prerequisites):
        ordered, seen, visiting = [], set(), set()
        def visit(identity, reference):
            if self.store.task(scope, identity).kind == 'review':
                return
            if reference.id in visiting or len(seen) + len(visiting) >= 64:
                raise StoreError('dependency_cycle')
            if reference.id in seen:
                return
            record = self.completions.metadata(scope, reference.id)
            if record.task_id != identity or digest(record.model_dump(mode='json')) != reference.sha256:
                raise StoreError('dependency_changed')
            visiting.add(record.id)
            for task_id, item in sorted(record.prerequisites.items()):
                visit(task_id, item)
            visiting.remove(record.id)
            seen.add(record.id)
            ordered.append(record)
        for identity, reference in sorted(prerequisites.items()):
            visit(identity, reference)
        return ordered

    def evidence(self, scope, attempt):
        prerequisites = self.metadata(scope, attempt)
        if not prerequisites:
            return None
        records = self.records(scope, prerequisites)
        review_id = records[0].review_id if records else next(iter(prerequisites.values())).id
        pins = self.completions._evidence_pins(scope, review_id, prerequisites)
        self.completions._input_evidence(scope, prerequisites, {})
        result = review_id, prerequisites, pins
        self.stable(scope, attempt, result)
        return result

    def stable(self, scope, attempt, evidence):
        if evidence is not None:
            review_id, prerequisites, pins = evidence
            sources = {}
            for record in self.records(scope, prerequisites):
                expected = (record.source_root, record.source_hash)
                if record.source_root.path in sources and sources[record.source_root.path] != expected:
                    raise StoreError('dependency_source')
                sources[record.source_root.path] = expected
            for path, (root, expected_hash) in sources.items():
                if Sources(self.store).prepare(Path(path)) != root or capture(Path(path)).subject_hash != expected_hash:
                    raise StoreError('dependency_changed')
            if (self.metadata(scope, attempt) != prerequisites
                    or self.completions._evidence_pins(scope, review_id, prerequisites) != pins):
                raise StoreError('dependency_changed')

    def merge(self, scope, artifacts, snapshot):
        """Validate every ancestor and distinguish sequential edits from sibling conflicts."""
        selected = {artifact.task_id: artifact for artifact in artifacts}
        ordered, ancestry, visiting = [], {}, set()
        def visit(artifact):
            if artifact.id in visiting:
                raise StoreError('dependency_cycle')
            if artifact.id in ancestry:
                return
            visiting.add(artifact.id)
            attempt = self.store._attempt(artifact.attempt_id)
            if (artifact.base.subject_hash != snapshot.subject_hash or artifact.prerequisites != attempt.prerequisites
                    or self.metadata(scope, attempt) != artifact.prerequisites):
                raise StoreError('artifact_lineage')
            ancestors = set()
            for proof in self.records(scope, artifact.prerequisites):
                predecessor = selected.get(proof.task_id)
                if predecessor is None or predecessor.id != proof.artifact_id:
                    raise StoreError('artifact_lineage')
                visit(predecessor)
                ancestors.update({predecessor.id, *ancestry[predecessor.id]})
            expected = apply_versions(versions(snapshot), [item for item in ordered if item.id in ancestors])
            if versions(artifact.baseline) != expected:
                raise StoreError('artifact_lineage')
            apply_versions(expected, [artifact])
            visiting.remove(artifact.id)
            ancestry[artifact.id] = ancestors
            ordered.append(artifact)
        for artifact in sorted(artifacts, key=lambda item: item.task_id):
            visit(artifact)
        conflicts, changed = set(), {}
        for artifact in ordered:
            for change in artifact.changes:
                for other, previous in changed.get(change.path, ()):
                    if other not in ancestry[artifact.id] and previous != change:
                        conflicts.add(change.path)
                changed.setdefault(change.path, []).append((artifact.id, change))
        if conflicts:
            return ordered, (), tuple(sorted(conflicts))
        before, after = versions(snapshot), apply_versions(versions(snapshot), ordered)
        def file_version(value):
            return FileVersion(sha256=value[0], mode=value[1]) if value is not None else None
        changes = tuple(FileChange(path=name, before=file_version(before.get(name)), after=file_version(after.get(name)))
            for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name))
        return ordered, changes, ()

    def write(self, scope, attempt, source, snapshot, baseline, destination):
        if self.store.db.in_transaction:
            raise RuntimeError('Dependency composition cannot run in a transaction')
        prerequisites = self.metadata(scope, attempt)
        records = self.records(scope, prerequisites)
        source_root = Sources(self.store).prepare(source)
        for record in records:
            if record.source_root != source_root or record.source_hash != snapshot.subject_hash:
                raise StoreError('dependency_source')
        evidence = self.evidence(scope, attempt)
        artifacts = Artifacts(self.store, self.root)
        outputs = {record.id: artifacts.get(scope, record.artifact_id) for record in records}
        original, excluded = input_files(baseline)
        original_versions = {name: (hashlib.sha256(data).hexdigest(), stat.S_IMODE((baseline / name).stat().st_mode))
            for name, data in original.items()}
        if excluded or original_versions != versions(snapshot):
            raise StoreError('workspace_integrity')
        ordered, _, conflicts = self.merge(scope, list(outputs.values()), snapshot)
        if conflicts:
            raise StoreError('dependency_conflict', ', '.join(conflicts))
        combined = apply_versions(original_versions, ordered)
        if len(combined) > MAX_FILES:
            raise StoreError('dependency_limit')
        data = dict(original)
        total = sum(len(raw) for raw in data.values())
        for artifact in ordered:
            for change in artifact.changes:
                previous_size = len(data.get(change.path, b''))
                if change.after is None:
                    data.pop(change.path, None)
                    total -= previous_size
                else:
                    try:
                        raw = read_regular(artifacts.directory / artifact.id / 'target' / change.path,
                            limit=MAX_BYTES - total + previous_size)
                    except (OSError, ValueError) as error:
                        raise StoreError('dependency_evidence') from error
                    if hashlib.sha256(raw).hexdigest() != change.after.sha256:
                        raise StoreError('artifact_integrity')
                    data[change.path] = raw
                    total += len(raw) - previous_size
        if {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()} != {name: pair[0] for name, pair in combined.items()}:
            raise StoreError('artifact_lineage')
        destination.mkdir(mode=0o700)
        descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            write_files(descriptor, data, {name: pair[1] for name, pair in combined.items()})
        finally:
            os.close(descriptor)
        result = capture(destination)
        self.stable(scope, attempt, evidence)
        if (self.metadata(scope, attempt) != prerequisites or capture(source).subject_hash != snapshot.subject_hash
                or Sources(self.store).prepare(source) != source_root):
            raise StoreError('dependency_changed')
        return result, evidence
