"""Host-owned attempt checkouts from immutable, byte-preserving input copies."""

import hashlib
import os
import shutil
import stat
import subprocess
from itertools import chain
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import WorkspaceRecord
from ..verification.evidence import atomic_json, read_regular
from ..verification.snapshot import MAX_FILES, capture, input_files
from .store import Store, StoreError


def _git(root: Path, *arguments: str) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
        GIT_AUTHOR_NAME='FlyCode workspace', GIT_AUTHOR_EMAIL='workspace@flycode.invalid',
        GIT_COMMITTER_NAME='FlyCode workspace', GIT_COMMITTER_EMAIL='workspace@flycode.invalid')
    result = subprocess.run(['git', '-c', f'core.hooksPath={os.devnull}', '-c', f'core.attributesFile={os.devnull}',
        '-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false', '-C', str(root), *arguments],
        env=environment, capture_output=True, text=True, timeout=30, check=True)
    return result.stdout.strip()


def _checkout(root: Path) -> str:
    _git(root, 'init', '--quiet', '--template=')
    # Project/global attributes must not normalize the captured bytes or invoke filters.
    (root / '.git/info').mkdir(exist_ok=True)
    (root / '.git/info/attributes').write_text('* -text -filter -ident -working-tree-encoding\n')
    _git(root, 'add', '--all', '--force', '--', '.')
    _git(root, 'commit', '--quiet', '--allow-empty', '--no-gpg-sign', '-m', 'Workspace baseline')
    revision = _git(root, 'rev-parse', 'HEAD')
    _git(root, 'checkout', '--quiet', '--detach', revision)
    return revision


def _input_pins(directory):
    pins = {}
    for name in ('baseline', 'original'):
        root = directory / name
        if name == 'original' and not root.exists():
            continue
        for number, path in enumerate(chain((root,), root.rglob('*'))):
            info = path.lstat()
            if number >= MAX_FILES * 8 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise StoreError('workspace_integrity')
            pins[str(path.relative_to(directory))] = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return pins


class Workspaces:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()

    def _record(self, scope, attempt_id) -> WorkspaceRecord | None:
        self.store.task(scope, self.store._attempt(attempt_id).task_id)
        row = self.store.db.execute('SELECT data FROM workspaces WHERE attempt_id=?', (attempt_id,)).fetchone()
        return WorkspaceRecord.model_validate_json(row['data']) if row else None

    def get(self, scope, attempt_id) -> WorkspaceRecord:
        if self.store.db.in_transaction:
            raise RuntimeError('Workspace inspection cannot run in a SQLite transaction')
        record = self._record(scope, attempt_id)
        if record is None:
            raise StoreError('not_found', 'workspace')
        return self._verify(scope, record, self.root / record.id)

    def _verify(self, scope, record, directory):
        attempt_id = record.attempt_id
        try:
            pins = _input_pins(directory)
            baseline, repository = directory / 'baseline', directory / 'repository'
            manifest = directory / 'workspace.json'
            if (directory.is_symlink() or baseline.is_symlink() or repository.is_symlink() or manifest.is_symlink()
                    or not baseline.is_dir() or not repository.is_dir()):
                raise ValueError('Invalid workspace directories')
            expected = record.model_dump_json(exclude={'input_snapshot', 'prerequisites'} - record.model_fields_set).encode()
            if read_regular(manifest, limit=len(expected)) != expected:
                raise ValueError('Workspace manifest changed')
            files, excluded = input_files(baseline)
            hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
            modes = {name: stat.S_IMODE((baseline / name).stat().st_mode) for name in files}
            snapshot = record.input_snapshot or record.snapshot
            if excluded or hashes != snapshot.files or modes != snapshot.modes:
                raise ValueError('Workspace baseline changed')
            if record.input_snapshot is not None:
                original = directory / 'original'
                if original.is_symlink() or not original.is_dir():
                    raise ValueError('Workspace source changed')
                files, excluded = input_files(original)
                if (excluded or {name: hashlib.sha256(data).hexdigest() for name, data in files.items()} != record.snapshot.files
                        or {name: stat.S_IMODE((original / name).stat().st_mode) for name in files} != record.snapshot.modes):
                    raise ValueError('Workspace source changed')
            if record.prerequisites != self.store._attempt(attempt_id).prerequisites:
                raise ValueError('Workspace prerequisites changed')
            if _git(repository, 'rev-parse', 'HEAD') != record.checkout_revision:
                raise ValueError('Workspace checkout revision changed')
            if _input_pins(directory) != pins:
                raise ValueError('Workspace inputs changed during inspection')
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise StoreError('workspace_integrity', 'The registered baseline or checkout changed') from error
        return record

    def repository(self, scope, attempt_id) -> Path:
        return self.root / self.get(scope, attempt_id).id / 'repository'

    def prepare(self, scope, attempt_id: str, fence: int, source: Path, *, expected_source_hash=None) -> WorkspaceRecord:
        store = self.store
        if store.db.in_transaction:
            raise RuntimeError('Workspace preparation cannot run in a SQLite transaction')
        attempt, task, _ = store._owned(scope, attempt_id, fence)
        if task.dependencies or attempt.prerequisites:
            from .composition import Compositions

            Compositions(store, self.root.parent).metadata(scope, attempt)
        source = Path(source).resolve(strict=True)
        if self.root.is_relative_to(source):
            raise StoreError('workspace_location', 'Workspace storage must be outside the project')
        previous = self._record(scope, attempt_id)
        if previous:
            if (previous.source_root != str(source) or previous.fence != fence
                    or (expected_source_hash is not None and previous.snapshot.subject_hash != expected_source_hash)):
                raise StoreError('idempotency', 'Attempt already has a different workspace')
            if task.dependencies or attempt.prerequisites:
                Compositions(store, self.root.parent).evidence(scope, attempt)
            return self.get(scope, attempt_id)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = uuid4().hex
        staging, directory = self.root / ('.' + identity + '.tmp'), self.root / identity
        staging.mkdir(mode=0o700)
        try:
            snapshot = capture(source, staging / 'baseline')
            if ((task.base_snapshot_hash is not None and task.base_snapshot_hash != snapshot.subject_hash)
                    or (expected_source_hash is not None and expected_source_hash != snapshot.subject_hash)):
                raise StoreError('snapshot_changed', 'The approved input snapshot changed')
            input_snapshot, evidence = None, None
            if attempt.prerequisites:
                from .composition import Compositions

                input_snapshot, evidence = Compositions(store, self.root.parent).write(scope, attempt, source, snapshot,
                    staging / 'baseline', staging / 'input')
                (staging / 'baseline').rename(staging / 'original')
                (staging / 'input').rename(staging / 'baseline')
            repository = staging / 'repository'
            shutil.copytree(staging / 'baseline', repository)
            pins = _input_pins(staging)
            info = staging.stat()
            identity_pin = (info.st_dev, info.st_ino)
            from .continuations import Continuations
            from .retries import Retries

            revision = _checkout(repository)
            if not Retries(store, self.root.parent).restore(scope, attempt, repository):
                Continuations(store, self.root.parent).restore(scope, attempt, repository)
            record = WorkspaceRecord(id=identity, attempt_id=attempt_id, fence=fence, contract_hash=attempt.contract_hash,
                source_root=str(source), snapshot=snapshot, input_snapshot=input_snapshot, prerequisites=attempt.prerequisites,
                checkout_revision=revision, created_at=store.clock())
            atomic_json(staging / 'workspace.json', record.model_dump_json())
            staging.rename(directory)
            self._verify(scope, record, directory)
            if evidence is not None:
                Compositions(store, self.root.parent).stable(scope, attempt, evidence)
            info = directory.stat()
            if (info.st_dev, info.st_ino) != identity_pin or _input_pins(directory) != pins:
                raise StoreError('workspace_integrity')
            with store.transaction():
                attempt, task, workflow = store._owned(scope, attempt_id, fence)
                if attempt.prerequisites:
                    Compositions(store, self.root.parent).metadata(scope, attempt)
                winner = self._record(scope, attempt_id)
                if winner is None:
                    store.db.execute('INSERT INTO workspaces VALUES (?,?,?)', (identity, attempt_id, record.model_dump_json()))
                    store._event(scope, workflow, 'workspace_created', task=task, attempt=attempt,
                        workspace_id=identity, subject_hash=snapshot.subject_hash, checkout_revision=record.checkout_revision)
            if winner is not None:
                shutil.rmtree(directory)
                if winner.source_root != str(source) or winner.fence != fence:
                    raise StoreError('idempotency', 'Concurrent preparation used a different workspace')
            return self.get(scope, attempt_id)
        except BaseException:
            # Only this invocation's server-generated staging/published directory is eligible.
            if self._record(scope, attempt_id) is None:
                shutil.rmtree(directory, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
            raise
