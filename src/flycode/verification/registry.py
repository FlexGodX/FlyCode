"""Trusted catalogue registration; remote callers consume scoped opaque IDs only."""
import hashlib
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..domain.source import SourceRoot
from ..domain.verification_control import (
    RegisteredSubject,
    RegisteredVerificationProfile,
    VerificationProfileDraft,
)
from ..orchestration.projects import Projects
from ..orchestration.sources import Sources
from ..orchestration.store import Store, StoreError
from .oracle import OracleRegistry
from .plan import contract_hash, registered_checks, validate_target
from .snapshot import capture, digest, input_files


class VerificationRegistry:
    def __init__(self, store: Store, directory: Path | None = None):
        self.store = store
        self.directory = Path(directory).expanduser().resolve() if directory is not None else None

    def _project(self, scope, project_id, *, owner=False):
        self.store._scope(scope, project_id, ('owner',) if owner else ())
        project = Projects(self.store).get(project_id)
        return Projects(self.store)._current(project)

    def _storage(self, project, *, create=False) -> SourceRoot:
        if self.store.db.in_transaction:
            raise RuntimeError('Verification storage inspection cannot run in a SQLite transaction')
        row = self.store.db.execute('SELECT data FROM verification_storage WHERE id=1').fetchone()
        expected = SourceRoot.model_validate_json(row['data']) if row else None
        path = (Path(expected.path) if expected else self.directory
            or self.store.path.absolute().with_name(self.store.path.name + '.verification'))
        if self.directory is not None and path != self.directory:
            raise StoreError('verification_storage_changed')
        source = Path(project.root.path)
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise StoreError('verification_storage_location')
        if expected is None:
            if not create:
                raise StoreError('not_found', 'verification storage')
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            current = Sources(self.store).prepare(path)
            info = path.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise StoreError('verification_storage_permissions')
        except OSError as error:
            raise StoreError('verification_storage_changed') from error
        if expected is not None:
            if current != expected:
                raise StoreError('verification_storage_changed')
            return current
        with self.store.transaction():
            row = self.store.db.execute('SELECT data FROM verification_storage WHERE id=1').fetchone()
            if row is not None:
                if SourceRoot.model_validate_json(row['data']) != current:
                    raise StoreError('verification_storage_changed')
            else:
                self.store.db.execute('INSERT INTO verification_storage VALUES (1,?)', (current.model_dump_json(),))
        return current

    def _unchanged(self, project, storage):
        if self._storage(project) != storage:
            raise StoreError('verification_storage_changed')
        if Projects(self.store)._current(project) != project:
            raise StoreError('project_changed')

    def _directory(self, storage, *parts, create=False):
        path = Path(storage.path)
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise StoreError('verification_storage_changed')
            if create:
                path.mkdir(exist_ok=True, mode=0o700)
            if not path.is_dir() or path.resolve() != path:
                raise StoreError('verification_storage_changed')
        return path

    def _stage_current(self, directory, expected):
        try:
            if (expected.path != str(directory) or directory.is_symlink()
                    or Sources(self.store).prepare(directory) != expected):
                raise StoreError('verification_storage_changed')
        except OSError as error:
            raise StoreError('verification_storage_changed') from error

    @contextmanager
    def _opened(self, directory, expected):
        self._stage_current(directory, expected)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != expected.identity:
                raise StoreError('verification_storage_changed')
            yield descriptor
        finally:
            os.close(descriptor)

    def _project_sql(self, project):
        if Projects(self.store).get(project.id) != project:
            raise StoreError('project_changed')

    def _copy_matches(self, root, snapshot):
        files, excluded = input_files(root)
        return (not excluded and {name: hashlib.sha256(data).hexdigest() for name, data in files.items()} == snapshot.files
            and {name: stat.S_IMODE((root / name).stat().st_mode) for name in files} == snapshot.modes)

    def register_subject(self, scope, project_id: str) -> RegisteredSubject:
        project = self._project(scope, project_id, owner=True)
        storage = self._storage(project, create=True)
        parent = self._directory(storage, 'subjects', create=True)
        directory = parent / uuid4().hex
        directory.mkdir(mode=0o700)
        owned = Sources(self.store).prepare(directory)
        published = False
        try:
            root = directory / 'files'
            with self._opened(directory, owned) as descriptor:
                os.mkdir('files', mode=0o700, dir_fd=descriptor)
                target = os.open('files', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    snapshot = capture(Path(project.root.path), root, destination_fd=target)
                    self._stage_current(directory, owned)
                    info = os.fstat(target)
                    captured_root = Sources(self.store).prepare(root)
                    if captured_root.path != str(root) or captured_root.identity != (info.st_dev, info.st_ino):
                        raise StoreError('verification_subject_changed')
                finally:
                    os.close(target)
            if (capture(Path(project.root.path)).subject_hash != snapshot.subject_hash
                    or not self._copy_matches(root, snapshot)):
                raise StoreError('verification_source_changed')
            self._stage_current(directory, owned)
            self._stage_current(root, captured_root)
            record = RegisteredSubject(project_id=project.id, project_revision=project.revision,
                source_root=project.root, root=captured_root, snapshot=snapshot,
                created_by=scope.actor_id, created_at=self.store.clock())
            self._unchanged(project, storage)
            with self.store.transaction():
                self._project_sql(project)
                self.store.db.execute('INSERT INTO verification_subjects VALUES (?,?,?)',
                    (record.id, project.id, record.model_dump_json()))
            published = True
            return record
        finally:
            if not published and directory.exists() and Sources(self.store).prepare(directory) == owned:
                shutil.rmtree(directory)

    def subject(self, scope, identity: str) -> RegisteredSubject:
        record = self.store._load('verification_subjects', identity, RegisteredSubject)
        project = self._project(scope, record.project_id)
        if project.revision != record.project_revision or project.root != record.source_root:
            raise StoreError('project_changed')
        return record

    def subject_root(self, scope, identity: str) -> Path:
        record = self.subject(scope, identity)
        project = Projects(self.store).get(record.project_id)
        storage = self._storage(project)
        root = Path(record.root.path)
        try:
            if (not root.is_relative_to(Path(storage.path) / 'subjects')
                    or Sources(self.store).prepare(root) != record.root
                    or not self._copy_matches(root, record.snapshot)):
                raise StoreError('verification_subject_changed')
        except (OSError, ValueError) as error:
            raise StoreError('verification_subject_changed') from error
        self._unchanged(project, storage)
        return root

    def register_profile(self, scope, project_id: str, draft: VerificationProfileDraft, *,
                         oracle_root: Path | None = None) -> RegisteredVerificationProfile:
        project = self._project(scope, project_id, owner=True)
        draft = VerificationProfileDraft.model_validate(draft.model_dump())
        for check in draft.checks:
            if (not check.id or len(check.id) > 200 or check.version != 'check-v1'
                    or check.oracle_version != 'pytest-observer+junit-v1' or check.subject_hash is not None):
                raise ValueError('Unsupported or already bound check profile')
            for target in check.targets:
                validate_target(target)
        if draft.acceptance is None:
            if oracle_root is not None or len(draft.checks) != 1:
                raise ValueError('Registered suite requires one pytest suite and no independent oracle')
            check = draft.checks[0]
            if not check.required or check.stage != 'target' or check.dependencies or check.expected_outcome != 'passed':
                raise ValueError('Registered suite requires a mandatory target suite')
        else:
            registered_checks(draft.checks, draft.acceptance)
            if oracle_root is None:
                raise ValueError('Acceptance profile requires independent owner oracle bytes')
        storage = self._storage(project, create=True)
        identity, approval, directory, owned = uuid4().hex, None, None, None
        published = False
        try:
            if draft.acceptance is not None:
                assert oracle_root is not None
                directory = self._directory(storage, 'oracles', project.id, identity, create=True)
                owned = Sources(self.store).prepare(directory)
                with self._opened(directory, owned) as descriptor:
                    approval = OracleRegistry(directory, directory_fd=descriptor).approve(draft.acceptance, draft.checks, oracle_root)
                    self._stage_current(directory, owned)
            record = RegisteredVerificationProfile(id=identity, project_id=project.id, project_revision=project.revision,
                draft=draft, approval_hash=approval, created_by=scope.actor_id, created_at=self.store.clock())
            self._unchanged(project, storage)
            with self.store.transaction():
                self._project_sql(project)
                self._activate_profile(record)
                self.store.db.execute('INSERT INTO verification_profiles VALUES (?,?,?)',
                    (record.id, project.id, record.model_dump_json()))
            published = True
            return record
        finally:
            if (not published and directory is not None and directory.exists()
                    and Sources(self.store).prepare(directory) == owned):
                shutil.rmtree(directory)

    def _activate_profile(self, record):
        contract = record.draft.acceptance
        if contract is None:
            return
        semantic = contract_hash(contract)
        head = self.store.db.execute('SELECT * FROM verification_profile_heads WHERE project_id=? AND contract_id=?',
            (record.project_id, contract.id)).fetchone()
        if head is not None and (head['version'] > contract.version
                or (head['version'] == contract.version and head['contract_hash'] != semantic)):
            raise ValueError('Semantic contract changes require a newer owner version')
        self.store.db.execute('''INSERT INTO verification_profile_heads VALUES (?,?,?,?,?)
            ON CONFLICT(project_id,contract_id) DO UPDATE SET version=excluded.version,
                contract_hash=excluded.contract_hash,approval_hash=excluded.approval_hash''',
            (record.project_id, contract.id, contract.version, semantic, record.approval_hash))

    def profile(self, scope, identity: str) -> RegisteredVerificationProfile:
        record = self.store._load('verification_profiles', identity, RegisteredVerificationProfile)
        project = self._project(scope, record.project_id)
        if record.project_revision != project.revision:
            raise StoreError('project_changed')
        storage = self._storage(project)
        if record.draft.acceptance is not None:
            try:
                head = self.store.db.execute('SELECT approval_hash FROM verification_profile_heads WHERE project_id=? AND contract_id=?',
                    (project.id, record.draft.acceptance.id)).fetchone()
                if head is None or head['approval_hash'] != record.approval_hash:
                    raise ValueError('A newer owner approval superseded this profile')
                directory = self._directory(storage, 'oracles', project.id, record.id)
                approved = OracleRegistry(directory).resolve(
                    record.approval_hash, record.draft.acceptance)
                if digest([item.model_dump() for item in approved.checks]) != digest(
                        [item.model_dump() for item in record.draft.checks]):
                    raise ValueError('Registered profile checks changed')
            except (OSError, ValueError) as error:
                raise StoreError('verification_profile_changed') from error
        return record
