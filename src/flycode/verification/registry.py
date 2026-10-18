"""Trusted catalogue registration; remote callers consume scoped opaque IDs only."""
import hashlib
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import WorkerBinding, WorkspaceRecord
from ..domain.results import IntegrationRecord, PatchArtifact
from ..domain.source import SourceRoot
from ..domain.verification import SubjectRef, SubjectSnapshot, VerificationPlan
from ..domain.verification_control import (
    RegisteredSubject,
    RegisteredVerificationPlan,
    RegisteredVerificationProfile,
    SubjectOrigin,
    VerificationPlanDraft,
    VerificationProfileDraft,
    VerificationStage,
)
from ..orchestration.projects import Projects
from ..orchestration.sources import Sources
from ..orchestration.store import Store, StoreError
from .oracle import OracleRegistry
from .plan import PlanBuilder, contract_hash, registered_checks, validate_target
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
        return self._register_capture(scope, project, Path(project.root.path))

    def _register_capture(self, scope, project, source, *, ref=None, provenance=None, origin=None):
        source_identity = Sources(self.store).prepare(source)
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
                    snapshot = capture(source, root, destination_fd=target, ref=ref, provenance=provenance)
                    self._stage_current(directory, owned)
                    info = os.fstat(target)
                    captured_root = Sources(self.store).prepare(root)
                    if captured_root.path != str(root) or captured_root.identity != (info.st_dev, info.st_ino):
                        raise StoreError('verification_subject_changed')
                finally:
                    os.close(target)
            if (capture(source, ref=ref, provenance=provenance).subject_hash != snapshot.subject_hash
                    or not self._copy_matches(root, snapshot)):
                raise StoreError('verification_source_changed')
            if origin is not None:
                self._origin_input(scope, origin)
            if Sources(self.store).prepare(source) != source_identity:
                raise StoreError('verification_source_changed')
            self._stage_current(directory, owned)
            self._stage_current(root, captured_root)
            record = RegisteredSubject(project_id=project.id, project_revision=project.revision,
                source_root=project.root, root=captured_root, snapshot=snapshot, origin=origin,
                created_by=scope.actor_id, created_at=self.store.clock())
            self._unchanged(project, storage)
            with self.store.transaction():
                self._project_sql(project)
                if origin is not None:
                    self._origin_sql(scope, origin)
                self.store.db.execute('INSERT INTO verification_subjects VALUES (?,?,?)',
                    (record.id, project.id, record.model_dump_json()))
            published = True
            return record
        finally:
            if not published and directory.exists() and Sources(self.store).prepare(directory) == owned:
                shutil.rmtree(directory)

    def _origin_access(self, scope, project_id):
        self.store._scope(scope, project_id, ('owner', 'runtime'))
        if scope.role == 'runtime':
            if self.store.host is None:
                raise StoreError('host_required')
            self.store._host_sql()

    def _assignment_sql(self, scope, attempt_id):
        attempt = self.store._attempt(attempt_id)
        task = self.store.task(scope, attempt.task_id)
        workflow = self.store.workflow(scope, attempt.workflow_id)
        self._origin_access(scope, task.project_id)
        project = Projects(self.store).get(task.project_id)
        if project.state != 'active':
            raise StoreError('project_revoked')
        if attempt.execution_binding_id is None:
            raise StoreError('verification_provenance_unavailable')
        binding = self.store._load('worker_bindings', attempt.execution_binding_id, WorkerBinding)
        if (binding.task_id != task.id or binding.workflow_id != workflow.id or binding.project_id != project.id
                or binding.semantic_revision != attempt.semantic_revision
                or binding.workflow_semantic_revision != attempt.workflow_semantic_revision
                or binding.contract_hash != attempt.contract_hash or binding.id != attempt.execution_binding_id
                or task.workflow_id != workflow.id or workflow.project_id != project.id
                or attempt.contract_hash != task.contract_hash or attempt.semantic_revision > task.semantic_revision
                or attempt.workflow_semantic_revision > workflow.semantic_revision
                or binding.source_root != project.root.path):
            raise StoreError('verification_subject_scope')
        row = self.store.db.execute('SELECT data FROM workspaces WHERE attempt_id=?', (attempt.id,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'workspace')
        workspace = WorkspaceRecord.model_validate_json(row['data'])
        if (workspace.source_root != binding.source_root or workspace.snapshot.subject_hash != binding.source_hash
                or workspace.fence != attempt.fence or workspace.contract_hash != attempt.contract_hash):
            raise StoreError('verification_subject_scope')
        ref = SubjectRef(project_id=project.id, workflow_id=workflow.id, task_id=task.id, attempt_id=attempt.id)
        return project, attempt, workspace, binding, ref

    def _assignment(self, scope, attempt_id):
        from ..orchestration.workspaces import Workspaces

        result = self._assignment_sql(scope, attempt_id)
        project, attempt, workspace, binding, _ = result
        self._project(scope, project.id)
        if Workspaces(self.store, Path(binding.runtime_root) / 'workspaces').get(scope, attempt.id) != workspace:
            raise StoreError('verification_subject_scope')
        return result

    def _origin_sql(self, scope, origin):
        if origin.kind in {'artifact-baseline', 'artifact-target'}:
            return self._artifact_sql(scope, origin)
        if origin.kind == 'integration-stage':
            return self._integration_sql(scope, origin)
        if origin.kind != 'workspace-baseline':
            raise StoreError('verification_provenance_unavailable')
        saved = self.store._load('workspaces', origin.record_id, WorkspaceRecord)
        project, _, workspace, binding, ref = self._assignment_sql(scope, saved.attempt_id)
        if (workspace.id != origin.record_id or digest(workspace.model_dump(mode='json', exclude={'input_snapshot', 'prerequisites'} - workspace.model_fields_set)) != origin.record_hash
                or origin.source_subject_hash != workspace.snapshot.subject_hash or origin.source_base != workspace.snapshot.base):
            raise StoreError('verification_subject_scope')
        return project, Path(binding.runtime_root) / 'workspaces' / workspace.id / 'baseline', workspace.input_snapshot or workspace.snapshot, ref

    def _artifact_sql(self, scope, origin):
        record = self.store._load('artifacts', origin.record_id, PatchArtifact)
        project, attempt, workspace, binding, ref = self._assignment_sql(scope, record.attempt_id)
        owner = self.store.db.execute('SELECT attempt_id FROM artifacts WHERE id=?', (origin.record_id,)).fetchone()
        if (record.id != origin.record_id or record.patch_hash != origin.record_hash
                or owner['attempt_id'] != record.attempt_id or hashlib.sha256(
                    record.model_dump_json(exclude={'patch_hash'} | ({'prerequisites'} - record.model_fields_set)).encode()).hexdigest() != record.patch_hash
                or record.project_id != project.id or record.workflow_id != ref.workflow_id
                or record.task_id != ref.task_id or record.attempt_id != attempt.id or record.fence != attempt.fence
                or record.contract_hash != attempt.contract_hash or record.semantic_revision != attempt.semantic_revision
                or record.workspace_id != workspace.id or digest(record.base.model_dump(mode='json')) != digest(
                    workspace.snapshot.model_dump(mode='json'))
                or record.baseline.ref != ref or record.target.ref != ref
                or origin.source_subject_hash != record.base.subject_hash or origin.source_base != record.base.base):
            raise StoreError('verification_subject_scope')
        stage = 'baseline' if origin.kind == 'artifact-baseline' else 'target'
        snapshot = record.baseline if stage == 'baseline' else record.target
        return project, Path(binding.runtime_root) / 'artifacts' / record.id / stage, snapshot, ref

    def _integration_sql(self, scope, origin):
        record = self.store._load('integrations', origin.record_id, IntegrationRecord)
        workflow = self.store.workflow(scope, record.workflow_id)
        self._origin_access(scope, record.project_id)
        project = Projects(self.store).get(record.project_id)
        if origin.checked_task_id is None:
            raise StoreError('verification_subject_scope')
        task = self.store.task(scope, origin.checked_task_id)
        integrator = self.store._attempt(record.attempt_id)
        owning_task = self.store.task(scope, integrator.task_id)
        owner = self.store.db.execute('SELECT workflow_id,attempt_id,state FROM integrations WHERE id=?', (origin.record_id,)).fetchone()
        lane = self.store.db.execute('SELECT mode FROM execution_lanes WHERE attempt_id=?', (integrator.id,)).fetchone()
        author_id = record.author_attempts.get(task.id, record.attempt_id)
        attempt = self.store._attempt(author_id)
        if (record.runtime_root is None or not Path(record.runtime_root).is_absolute()
                or record.candidate is None or record.state not in {'completed', 'failed', 'invalidated'}
                or digest(record.model_dump(mode='json', exclude={'author_completions'} - record.model_fields_set)) != origin.record_hash
                or workflow.project_id != project.id or task.project_id != project.id or task.workflow_id != workflow.id
                or attempt.workflow_id != workflow.id
                or integrator.workflow_id != workflow.id or owning_task.workflow_id != workflow.id
                or owner['workflow_id'] != record.workflow_id or owner['attempt_id'] != record.attempt_id
                or owner['state'] != record.state or lane is None or lane['mode'] != 'integration'
                or owning_task.project_id != project.id or integrator.fence != record.fence
                or owning_task.kind != 'review' or integrator.profile_snapshot.role != 'reviewer'
                or integrator.contract_hash != owning_task.contract_hash
                or owning_task.id not in record.approval_hashes
                or record.source_root != project.root.path or project.state != 'active'
                or record.contracts.get(task.id) != task.contract_hash or record.semantic_revisions.get(task.id) != task.semantic_revision
                or (task.id in record.author_attempts and (attempt.task_id != task.id or attempt.contract_hash != task.contract_hash))
                or attempt.workflow_semantic_revision > workflow.semantic_revision or task.id not in record.approval_hashes
                or origin.source_subject_hash != record.source_before.subject_hash or origin.source_base != record.source_before.base
                or origin.stage is None or not record.artifact_hashes):
            raise StoreError('verification_subject_scope')
        selected, first, authors = None, None, {}
        for identity, expected in record.artifact_hashes.items():
            artifact = self.store._load('artifacts', identity, PatchArtifact)
            pinned = SubjectOrigin(kind='artifact-target', record_id=identity, record_hash=expected,
                source_subject_hash=artifact.base.subject_hash, source_base=artifact.base.base)
            _, source, _, ref = self._artifact_sql(scope, pinned)
            if (ref.workflow_id != workflow.id or ref.project_id != project.id
                    or source.parents[2] != Path(record.runtime_root) or record.author_attempts.get(ref.task_id) != ref.attempt_id):
                raise StoreError('verification_subject_scope')
            authors[artifact.task_id] = artifact.attempt_id
            first = first or artifact
            if artifact.task_id == task.id:
                selected = artifact
        if authors != record.author_attempts:
            raise StoreError('verification_subject_scope')
        ref = SubjectRef(project_id=project.id, workflow_id=workflow.id, task_id=task.id, attempt_id=attempt.id)
        artifact = selected or first
        assert artifact is not None
        if origin.stage == 'baseline' and selected is None:
            original, seen = record, set()
            while original.previous_result_id is not None:
                if original.id in seen or len(seen) >= 64:
                    raise StoreError('verification_subject_scope')
                seen.add(original.id)
                original = self.store._load('integrations', original.previous_result_id, IntegrationRecord)
                if original.workflow_id != record.workflow_id or original.source_root != record.source_root:
                    raise StoreError('verification_subject_scope')
            return project, Path(record.runtime_root) / 'integrations' / original.id / 'baseline', original.source_before, ref
        if origin.stage == 'baseline' or (origin.stage == 'target' and selected is not None):
            stage = 'baseline' if origin.stage == 'baseline' else 'target'
            snapshot = artifact.baseline if stage == 'baseline' else artifact.target
            return project, Path(record.runtime_root) / 'artifacts' / artifact.id / stage, snapshot, ref
        return project, Path(record.runtime_root) / 'integrations' / record.id / 'candidate', record.candidate, ref

    def _origin_input(self, scope, origin):
        project, source, snapshot, ref = self._origin_sql(scope, origin)
        self._project(scope, project.id)
        if origin.kind == 'workspace-baseline':
            self._assignment(scope, ref.attempt_id)
        elif origin.kind in {'artifact-baseline', 'artifact-target'}:
            from ..orchestration.artifacts import Artifacts
            from ..orchestration.inputs import Inputs

            self._assignment(scope, ref.attempt_id)
            record = Artifacts(self.store, source.parents[2]).get(scope, origin.record_id)
            Inputs(self.store).require_artifact(scope, record)
        else:
            record = self.store._load('integrations', origin.record_id, IntegrationRecord)
            for identity, expected in record.artifact_hashes.items():
                artifact = self.store._load('artifacts', identity, PatchArtifact)
                self._origin_input(scope, SubjectOrigin(kind='artifact-target', record_id=identity, record_hash=expected,
                    source_subject_hash=artifact.base.subject_hash, source_base=artifact.base.base))
            if not self._copy_matches(source, snapshot):
                raise StoreError('verification_subject_changed')
        return project, source, snapshot, ref

    def register_workspace_subject(self, scope, attempt_id: str) -> RegisteredSubject:
        self.store._role(scope, ('owner',))
        _, _, workspace, _, _ = self._assignment(scope, attempt_id)
        origin = SubjectOrigin(kind='workspace-baseline', record_id=workspace.id,
            record_hash=digest(workspace.model_dump(mode='json', exclude={'input_snapshot', 'prerequisites'} - workspace.model_fields_set)), source_subject_hash=workspace.snapshot.subject_hash,
            source_base=workspace.snapshot.base)
        project, source, provenance, ref = self._origin_input(scope, origin)
        return self._register_capture(scope, project, source, ref=ref, provenance=provenance, origin=origin)

    def register_artifact_subject(self, scope, artifact_id: str, *, stage: str) -> RegisteredSubject:
        self.store._role(scope, ('owner',))
        if stage not in {'baseline', 'target'}:
            raise StoreError('verification_subject_stages')
        record = self.store._load('artifacts', artifact_id, PatchArtifact)
        origin = SubjectOrigin(kind='artifact-baseline' if stage == 'baseline' else 'artifact-target',
            record_id=record.id, record_hash=record.patch_hash, source_subject_hash=record.base.subject_hash,
            source_base=record.base.base)
        project, source, provenance, ref = self._origin_input(scope, origin)
        return self._register_capture(scope, project, source, ref=ref, provenance=provenance, origin=origin)

    def register_integration_subject(self, scope, integration_id: str, *, task_id: str,
                                     stage: VerificationStage = 'integration') -> RegisteredSubject:
        self.store._role(scope, ('owner',))
        if stage not in {'baseline', 'target', 'regression', 'integration'}:
            raise StoreError('verification_subject_stages')
        record = self.store._load('integrations', integration_id, IntegrationRecord)
        origin = SubjectOrigin(kind='integration-stage', record_id=record.id, stage=stage,
            record_hash=digest(record.model_dump(mode='json', exclude={'author_completions'} - record.model_fields_set)), checked_task_id=task_id,
            source_subject_hash=record.source_before.subject_hash, source_base=record.source_before.base)
        project, source, provenance, ref = self._origin_input(scope, origin)
        return self._register_capture(scope, project, source, ref=ref, provenance=provenance, origin=origin)

    def _subject_origin(self, scope, subject):
        if subject.origin is None:
            if subject.snapshot.ref is not None:
                raise StoreError('verification_provenance_unavailable')
            return
        project, _, provenance, ref = self._origin_input(scope, subject.origin)
        if project.id != subject.project_id or ref != subject.snapshot.ref:
            raise StoreError('verification_subject_scope')
        current = capture(self.subject_root(scope, subject.id), ref=ref, provenance=provenance)
        if current.model_dump(mode='json') != subject.snapshot.model_dump(mode='json'):
            raise StoreError('verification_subject_changed')

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

    def create_plan(self, scope, draft: VerificationPlanDraft) -> RegisteredVerificationPlan:
        draft = VerificationPlanDraft.model_validate(draft.model_dump())
        self.store._role(scope, ('owner',))
        request = {'verification_plan': draft.model_dump(mode='json')}
        found, result, _ = self.store._replay(scope, draft.idempotency_key, request)
        if found:
            return RegisteredVerificationPlan.model_validate(result)
        profile = self.profile(scope, draft.profile_id)
        project = self._project(scope, profile.project_id, owner=True)
        if profile.version != draft.profile_version:
            raise StoreError('verification_profile_version')
        subjects: dict[str, SubjectSnapshot] = {}
        for stage, identity in draft.subject_ids.items():
            subject = self.subject(scope, identity)
            if subject.project_id != project.id:
                raise StoreError('verification_subject_scope')
            self.subject_root(scope, identity)
            self._subject_origin(scope, subject)
            subjects[stage] = subject.snapshot
        if profile.draft.acceptance is None:
            if set(subjects) != {'target'}:
                raise StoreError('verification_subject_stages')
            snapshot = subjects['target']
            checks = [check.model_copy(update={'subject_hash': snapshot.subject_hash}) for check in profile.draft.checks]
            plan = VerificationPlan(subject_hash=snapshot.subject_hash,
                contract_hash=digest({'profile_id': profile.id, 'version': profile.version,
                    'checks': [check.model_dump(mode='json') for check in profile.draft.checks]}),
                checks=checks, subjects=subjects, trust=profile.draft.trust, reruns=profile.draft.reruns)
        else:
            if 'integration' not in subjects or profile.approval_hash is None:
                raise StoreError('verification_subject_stages')
            plan = PlanBuilder().build(subjects['integration'], profile.draft.acceptance, profile.draft.checks,
                subjects=subjects, approval_hash=profile.approval_hash, reruns=profile.draft.reruns)
            plan = plan.model_copy(update={'trust': profile.draft.trust})
        ref = next(iter(subjects.values())).ref
        workflow, task = None, None
        if ref is not None:
            if any(subject.ref is None or subject.ref.model_dump(exclude={'attempt_id'}) != ref.model_dump(
                    exclude={'attempt_id'}) for subject in subjects.values()):
                raise StoreError('verification_subject_scope')
            workflow, task = self.store.workflow(scope, ref.workflow_id), self.store.task(scope, ref.task_id)
            if ref.project_id != project.id or task.project_id != project.id or task.workflow_id != workflow.id:
                raise StoreError('verification_subject_scope')
            for snapshot in subjects.values():
                assert snapshot.ref is not None
                attempt = self.store._attempt(snapshot.ref.attempt_id)
                if attempt.workflow_id != workflow.id or attempt.workflow_semantic_revision > workflow.semantic_revision:
                    raise StoreError('verification_subject_scope')
            if profile.draft.acceptance is not None and (task.acceptance is None
                    or contract_hash(task.acceptance) != plan.contract_hash):
                raise StoreError('contract_changed')
        record = RegisteredVerificationPlan(id=plan.id, project_id=project.id, project_revision=project.revision,
            workflow_id=workflow.id if workflow else None, task_id=task.id if task else None,
            task_contract_hash=task.contract_hash if task else None,
            workflow_semantic_revision=workflow.semantic_revision if workflow else None,
            profile_id=profile.id, profile_version=profile.version, subject_ids=draft.subject_ids,
            budget=draft.budget, plan=plan, plan_hash=digest(plan.model_dump(mode='json')),
            created_by=scope.actor_id, created_at=self.store.clock())
        # All filesystem and oracle checks precede the transaction. Heads and
        # canonical metadata are checked again under the publication lock.
        self.profile(scope, profile.id)
        with self.store.transaction():
            found, result, request_hash = self.store._replay(scope, draft.idempotency_key, request)
            if found:
                return RegisteredVerificationPlan.model_validate(result)
            self._project_sql(project)
            self._profile_sql(profile)
            for identity in draft.subject_ids.values():
                subject = self.store._load('verification_subjects', identity, RegisteredSubject)
                if subject.origin is not None:
                    self._origin_sql(scope, subject.origin)
            if task is not None:
                assert workflow is not None
                if self.store.task(scope, task.id) != task or self.store.workflow(scope, workflow.id) != workflow:
                    raise StoreError('verification_subject_scope')
            self.store.db.execute('INSERT INTO verification_plans VALUES (?,?,?,?)',
                (record.id, project.id, record.workflow_id, record.model_dump_json()))
            from .control import VerificationControl

            VerificationControl(self.store)._event(scope, record, 'plan_created')
            self.store._remember(scope, draft.idempotency_key, project.id, request_hash, record)
        return record

    def _profile_sql(self, profile):
        if profile.draft.acceptance is not None:
            row = self.store.db.execute('SELECT approval_hash FROM verification_profile_heads WHERE project_id=? AND contract_id=?',
                (profile.project_id, profile.draft.acceptance.id)).fetchone()
            if row is None or row['approval_hash'] != profile.approval_hash:
                raise StoreError('verification_profile_changed')
