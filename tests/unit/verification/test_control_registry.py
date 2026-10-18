"""Registered verification inputs preserve bytes and physical project authority."""
import importlib.util

import pytest
from pydantic import ValidationError

from flycode.domain.orchestration import AccessScope
from flycode.domain.verification import CheckSpec
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store, StoreError


def catalogue(store, directory=None):
    assert importlib.util.find_spec('flycode.verification.registry') is not None, 'Verification catalogue is missing'
    from flycode.verification.registry import VerificationRegistry
    return VerificationRegistry(store, directory)


def profile_draft(**changes):
    assert importlib.util.find_spec('flycode.domain.verification_control') is not None, 'Registered profile contract is missing'
    from flycode.domain.verification_control import VerificationProfileDraft
    return VerificationProfileDraft(title='Original pytest suite', **changes)


@pytest.fixture
def registered(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'dirty.bin').write_bytes(b'\x00\xff original\r\n')
    (root / 'untracked.py').write_text('answer = 1\n')
    store = Store(tmp_path / 'state.sqlite')
    project = Projects(store).register(root, actor_id='owner')
    owner = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
    try:
        yield store, root, project, owner, tmp_path / 'verification'
    finally:
        store.close()


def test_captured_dirty_untracked_bytes_survive_owner_edits_and_reopen(registered):
    store, root, project, owner, directory = registered
    registry = catalogue(store, directory)
    subject = registry.register_subject(owner, project.id)
    (root / 'dirty.bin').write_bytes(b'changed by owner')
    (root / 'untracked.py').unlink()
    reopened = Store(store.path)
    try:
        reader = catalogue(reopened)
        assert reader.subject(owner, subject.id).model_dump(mode='json') == subject.model_dump(mode='json')
        copy = reader.subject_root(owner, subject.id)
        assert copy.joinpath('dirty.bin').read_bytes() == b'\x00\xff original\r\n'
        assert copy.joinpath('untracked.py').read_text() == 'answer = 1\n'
    finally:
        reopened.close()
    assert store.db.execute('SELECT count(*) FROM workflows').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM tool_calls').fetchone()[0] == 0
    assert not (root / '.git').exists()


def test_copy_tampering_is_rejected_instead_of_becoming_a_new_subject(registered):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    subject = registry.register_subject(owner, project.id)
    copy = registry.subject_root(owner, subject.id)
    (copy / 'untracked.py').write_text('answer = 999\n')
    with pytest.raises(StoreError, match='verification_subject_changed'):
        registry.subject_root(owner, subject.id)


@pytest.mark.parametrize('role,projects', [('reader', 'same'), ('owner', 'foreign')])
def test_registration_requires_owner_of_project_before_creating_storage(registered, role, projects):
    store, _, project, owner, directory = registered
    scope = AccessScope(actor_id='outsider', role=role,
        project_ids=owner.project_ids if projects == 'same' else {'foreign'})
    with pytest.raises(StoreError, match='scope'):
        catalogue(store, directory).register_subject(scope, project.id)
    assert not directory.exists()


def test_foreign_reader_cannot_obtain_snapshot_or_profile(registered):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    subject = registry.register_subject(owner, project.id)
    profile = registry.register_profile(owner, project.id, profile_draft())
    outsider = AccessScope(actor_id='outsider', project_ids={'other'}, role='reader')
    for read, identity in [(registry.subject, subject.id), (registry.subject_root, subject.id), (registry.profile, profile.id)]:
        with pytest.raises(StoreError, match='scope'):
            read(outsider, identity)


def test_replaced_project_denies_fresh_reads_and_registration(registered):
    store, root, project, owner, directory = registered
    registry = catalogue(store, directory)
    subject = registry.register_subject(owner, project.id)
    root.rename(root.with_name('old-project'))
    root.mkdir()
    for operation in [lambda: registry.subject(owner, subject.id), lambda: registry.register_subject(owner, project.id)]:
        with pytest.raises(StoreError, match='project_changed'):
            operation()


def test_replaced_storage_does_not_silently_recreate_empty_catalogue(registered):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    subject = registry.register_subject(owner, project.id)
    directory.rename(directory.with_name('old-verification'))
    directory.mkdir(mode=0o700)
    with pytest.raises(StoreError, match='verification_storage_changed'):
        catalogue(store).subject_root(owner, subject.id)
    assert not list(directory.iterdir())


def test_second_storage_path_cannot_redirect_registered_subjects(registered):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    registry.register_subject(owner, project.id)
    other = directory.with_name('other-verification')
    with pytest.raises(StoreError, match='verification_storage_changed'):
        catalogue(store, other).register_subject(owner, project.id)
    assert not other.exists()


def test_storage_inside_source_is_refused_before_capture(registered):
    store, root, project, owner, _ = registered
    with pytest.raises(StoreError, match='verification_storage_location'):
        catalogue(store, root / 'evidence').register_subject(owner, project.id)
    assert not (root / 'evidence').exists()


def test_source_mutation_during_capture_leaves_no_published_subject(registered, monkeypatch):
    store, root, project, owner, directory = registered
    registry = catalogue(store, directory)
    from flycode.verification import registry as module
    capture = module.capture

    def changing_capture(source, destination=None, **kwargs):
        result = capture(source, destination, **kwargs)
        if destination is not None:
            (root / 'dirty.bin').write_bytes(b'changed during capture')
        return result

    monkeypatch.setattr(module, 'capture', changing_capture)
    with pytest.raises(StoreError, match='verification_source_changed'):
        registry.register_subject(owner, project.id)
    assert store.db.execute('SELECT count(*) FROM verification_subjects').fetchone()[0] == 0
    assert not list(directory.glob('subjects/*'))
    assert (root / 'dirty.bin').read_bytes() == b'changed during capture'


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_unsafe_subject_input_never_publishes_a_partial_copy(registered, kind):
    store, root, project, owner, directory = registered
    registry = catalogue(store, directory)
    path = root / 'unsafe'
    if kind == 'symlink':
        path.symlink_to(root / 'dirty.bin')
    else:
        import os
        os.mkfifo(path)
    with pytest.raises(ValueError, match='symlink|special'):
        registry.register_subject(owner, project.id)
    assert store.db.execute('SELECT count(*) FROM verification_subjects').fetchone()[0] == 0
    assert not list(directory.glob('subjects/*'))


def test_profile_is_immutable_registered_configuration_without_execution(registered):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    profile = registry.register_profile(owner, project.id, profile_draft(checks=[CheckSpec(timeout=7)], reruns=2))
    assert registry.profile(owner, profile.id).draft.checks[0].timeout == 7
    assert registry.profile(owner, profile.id).draft.reruns == 2
    assert profile.version == 1
    assert profile.approval_hash is None
    assert store.db.execute('SELECT count(*) FROM tool_calls').fetchone()[0] == 0
    with pytest.raises(ValidationError):
        profile.version = 2


@pytest.mark.parametrize('check', [CheckSpec(targets=['../outside.py']), CheckSpec(oracle_version='arbitrary'),
    CheckSpec(subject_hash='other'), CheckSpec(required=False), CheckSpec(stage='integration')])
def test_unsupported_suite_profile_cannot_approve_arbitrary_execution(registered, check):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    with pytest.raises(ValueError):
        registry.register_profile(owner, project.id, profile_draft(checks=[check]))
    assert store.db.execute('SELECT count(*) FROM verification_profiles').fetchone()[0] == 0


def test_revoked_project_cannot_register_profile(registered):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    Projects(store).revoke(owner, project.id, expected_revision=project.revision)
    with pytest.raises(StoreError, match='project_revoked'):
        registry.register_profile(owner, project.id, profile_draft())
    assert not directory.exists()


def test_acceptance_profile_uses_independent_owner_oracle_and_detects_tampering(registered, tmp_path):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
    oracle = tmp_path / 'owner-oracle'
    oracle.mkdir()
    (oracle / 'test_answer.py').write_text('def test_answer():\n    assert True\n')
    checks = [CheckSpec(id=stage, stage=stage, targets=['test_answer.py::test_answer'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    contract = AcceptanceContract(id='owner-criteria', version=1, criteria=[AcceptanceCriterion(id='answer',
        check_ids=['target', 'regression', 'integration'], expected_case_ids=['test_answer.py::test_answer'])])
    profile = registry.register_profile(owner, project.id, profile_draft(checks=checks, acceptance=contract), oracle_root=oracle)
    assert profile.approval_hash is not None
    assert registry.profile(owner, profile.id).draft.acceptance.version == 1
    candidates = list(directory.glob('oracles/*/*/*/files/test_answer.py'))
    assert len(candidates) == 1
    candidates[0].write_text('def test_answer():\n    assert False\n')
    with pytest.raises(StoreError, match='verification_profile_changed'):
        registry.profile(owner, profile.id)


def test_atomic_schema20_failure_preserves_schema19_and_existing_records(registered, monkeypatch):
    import sqlite3

    store, _, project, _, _ = registered
    tables = ('canonical_projects', 'project_grants', 'commands', 'events')
    before = {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables}
    for name in ('verification_events', 'verification_reservations', 'verification_executions', 'verification_plans',
            'verification_profile_heads', 'verification_profiles', 'verification_subjects', 'verification_storage'):
        store.db.execute('DROP TABLE ' + name)
    store.db.execute('PRAGMA user_version=19')
    connect = sqlite3.connect

    def deny_version(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '20' else sqlite3.SQLITE_OK)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', deny_version)
        with pytest.raises(sqlite3.DatabaseError):
            Store(store.path)
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == 19
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'verification_%'").fetchone()
    assert {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables} == before
    reopened = Store(store.path)
    try:
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0] == 22
        assert Projects(reopened).get(project.id) == project
    finally:
        reopened.close()


def test_two_connections_preserve_both_registered_subjects(registered):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, _, project, owner, directory = registered
    barrier = Barrier(2)

    def register():
        connection = Store(store.path)
        try:
            barrier.wait(timeout=5)
            subject = catalogue(connection, directory).register_subject(owner, project.id)
            return subject.id
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(register) for _ in range(2)]
        identities = [future.result(timeout=10) for future in futures]
    assert len(set(identities)) == 2
    registry = catalogue(store)
    for identity in identities:
        assert (registry.subject_root(owner, identity) / 'dirty.bin').read_bytes() == b'\x00\xff original\r\n'
    assert store.db.execute('SELECT count(*) FROM verification_storage').fetchone()[0] == 1


@pytest.mark.parametrize('component', ['subjects', 'oracles'])
def test_private_subdirectory_symlink_cannot_redirect_capture(registered, tmp_path, component):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    registry.register_profile(owner, project.id, profile_draft())
    outside = tmp_path / 'outside'
    outside.mkdir()
    if component == 'subjects':
        (directory / component).symlink_to(outside, target_is_directory=True)
        operation = lambda: registry.register_subject(owner, project.id)
    else:
        (directory / component).mkdir()
        (directory / component / project.id).symlink_to(outside, target_is_directory=True)
        from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
        checks = [CheckSpec(id=stage, stage=stage, targets=['test_answer.py'])
            for stage in ('baseline', 'target', 'regression', 'integration')]
        contract = AcceptanceContract(id='answer', version=1, criteria=[AcceptanceCriterion(id='answer',
            check_ids=['target'], expected_case_ids=['test_answer.py::test_answer'])])
        oracle = tmp_path / 'oracle'
        oracle.mkdir()
        (oracle / 'test_answer.py').write_text('def test_answer():\n    assert True\n')
        operation = lambda: registry.register_profile(owner, project.id,
            profile_draft(checks=checks, acceptance=contract), oracle_root=oracle)
    with pytest.raises(StoreError, match='verification_storage_changed'):
        operation()
    assert not list(outside.iterdir())


def test_rejected_new_profile_does_not_revoke_previous_owner_approval(registered, tmp_path, monkeypatch):
    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
    from flycode.verification.oracle import OracleRegistry

    store, root, project, owner, directory = registered
    registry = catalogue(store, directory)
    oracle = tmp_path / 'oracle'
    oracle.mkdir()
    (oracle / 'test_answer.py').write_text('def test_answer():\n    assert True\n')
    checks = [CheckSpec(id=stage, stage=stage, targets=['test_answer.py'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    contract = AcceptanceContract(id='answer', version=1, criteria=[AcceptanceCriterion(id='answer',
        check_ids=['target'], expected_case_ids=['test_answer.py::test_answer'])])
    old = registry.register_profile(owner, project.id, profile_draft(checks=checks, acceptance=contract), oracle_root=oracle)
    approve = OracleRegistry.approve
    backup = root.with_name('original-project')

    def replace_source(self, *args, **kwargs):
        result = approve(self, *args, **kwargs)
        root.rename(backup)
        root.mkdir()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(OracleRegistry, 'approve', replace_source)
        with pytest.raises(StoreError, match='project_changed'):
            registry.register_profile(owner, project.id,
                profile_draft(checks=checks, acceptance=contract.model_copy(update={'version': 2})), oracle_root=oracle)
    root.rmdir()
    backup.rename(root)
    assert registry.profile(owner, old.id).approval_hash == old.approval_hash
    assert store.db.execute('SELECT count(*) FROM verification_profiles').fetchone()[0] == 1


def test_sql_writer_failure_keeps_previous_profile_current(registered, tmp_path):
    import sqlite3

    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion

    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    oracle = tmp_path / 'oracle'
    oracle.mkdir()
    (oracle / 'test_answer.py').write_text('def test_answer():\n    assert True\n')
    checks = [CheckSpec(id=stage, stage=stage, targets=['test_answer.py'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    contract = AcceptanceContract(id='answer', version=1, criteria=[AcceptanceCriterion(id='answer',
        check_ids=['target'], expected_case_ids=['test_answer.py::test_answer'])])
    original = registry.register_profile(owner, project.id, profile_draft(checks=checks, acceptance=contract), oracle_root=oracle)
    locker = Store(store.path)
    store.db.execute('PRAGMA busy_timeout=30')
    try:
        with locker.transaction(), pytest.raises(sqlite3.OperationalError, match='locked'):
            registry.register_profile(owner, project.id,
                profile_draft(checks=checks, acceptance=contract.model_copy(update={'version': 2})), oracle_root=oracle)
    finally:
        locker.close()
    assert registry.profile(owner, original.id).approval_hash == original.approval_hash
    assert store.db.execute('SELECT count(*) FROM verification_profiles').fetchone()[0] == 1
    assert len(list(directory.glob('oracles/*/*'))) == 1


def test_concurrent_older_profile_cannot_replace_committed_newer_approval(registered, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
    from flycode.verification.oracle import OracleRegistry

    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    oracle = tmp_path / 'oracle'
    oracle.mkdir()
    (oracle / 'test_answer.py').write_text('def test_answer():\n    assert True\n')
    checks = [CheckSpec(id=stage, stage=stage, targets=['test_answer.py'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    contract = AcceptanceContract(id='answer', version=1, criteria=[AcceptanceCriterion(id='answer',
        check_ids=['target'], expected_case_ids=['test_answer.py::test_answer'])])
    old = registry.register_profile(owner, project.id, profile_draft(checks=checks, acceptance=contract), oracle_root=oracle)
    prepared, release = Event(), Event()
    approve = OracleRegistry.approve

    def pause_version_two(self, contract, *args, **kwargs):
        result = approve(self, contract, *args, **kwargs)
        if contract.version == 2:
            prepared.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(OracleRegistry, 'approve', pause_version_two)

    def register_older():
        connection = Store(store.path)
        try:
            return catalogue(connection, directory).register_profile(owner, project.id,
                profile_draft(checks=checks, acceptance=contract.model_copy(update={'version': 2})), oracle_root=oracle)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(register_older)
        try:
            assert prepared.wait(5)
            newer = registry.register_profile(owner, project.id,
                profile_draft(checks=checks, acceptance=contract.model_copy(update={'version': 3})), oracle_root=oracle)
        finally:
            release.set()
        with pytest.raises(ValueError, match='newer owner version'):
            pending.result(timeout=5)
    assert registry.profile(owner, newer.id).draft.acceptance.version == 3
    with pytest.raises(StoreError, match='verification_profile_changed'):
        registry.profile(owner, old.id)
    assert store.db.execute('SELECT count(*) FROM verification_profiles').fetchone()[0] == 2


def test_subject_parent_replacement_cannot_copy_bytes_outside_private_storage(registered, tmp_path, monkeypatch):
    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    from flycode.verification import registry as module
    capture = module.capture
    outside = tmp_path / 'outside'
    outside.mkdir()

    def replace_parent(source, destination=None, **kwargs):
        if destination is not None:
            parent = destination.parent
            parent.rename(parent.with_name(parent.name + '-saved'))
            parent.symlink_to(outside, target_is_directory=True)
        return capture(source, destination, **kwargs)

    monkeypatch.setattr(module, 'capture', replace_parent)
    with pytest.raises(StoreError, match='verification_storage_changed|verification_subject_changed'):
        registry.register_subject(owner, project.id)
    assert not list(outside.iterdir())
    assert store.db.execute('SELECT count(*) FROM verification_subjects').fetchone()[0] == 0


def test_oracle_parent_replacement_cannot_copy_bytes_outside_private_storage(registered, tmp_path, monkeypatch):
    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
    from flycode.verification.oracle import OracleRegistry

    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    outside, oracle = tmp_path / 'outside', tmp_path / 'oracle'
    outside.mkdir()
    oracle.mkdir()
    (oracle / 'test_answer.py').write_text('def test_answer():\n    assert True\n')
    checks = [CheckSpec(id=stage, stage=stage, targets=['test_answer.py'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    contract = AcceptanceContract(id='answer', version=1, criteria=[AcceptanceCriterion(id='answer',
        check_ids=['target'], expected_case_ids=['test_answer.py::test_answer'])])
    approve = OracleRegistry.approve

    def replace_parent(self, *args, **kwargs):
        parent = self.directory
        parent.rename(parent.with_name(parent.name + '-saved'))
        parent.symlink_to(outside, target_is_directory=True)
        return approve(self, *args, **kwargs)

    monkeypatch.setattr(OracleRegistry, 'approve', replace_parent)
    with pytest.raises(StoreError, match='verification_storage_changed|verification_profile_changed'):
        registry.register_profile(owner, project.id, profile_draft(checks=checks, acceptance=contract), oracle_root=oracle)
    assert not list(outside.iterdir())
    assert store.db.execute('SELECT count(*) FROM verification_profiles').fetchone()[0] == 0


def test_replacement_before_stage_pin_cannot_adopt_external_directory(registered, tmp_path, monkeypatch):
    from pathlib import Path

    from flycode.orchestration.sources import Sources

    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    outside = tmp_path / 'outside'
    outside.mkdir()
    prepare = Sources.prepare
    replaced = False

    def replace_before_pin(self, path):
        nonlocal replaced
        path = Path(path)
        if path.parent.name == 'subjects' and not replaced:
            replaced = True
            (outside / path.name).mkdir()
            path.parent.rename(path.parent.with_name('saved-subjects'))
            path.parent.symlink_to(outside, target_is_directory=True)
        return prepare(self, path)

    monkeypatch.setattr(Sources, 'prepare', replace_before_pin)
    with pytest.raises(StoreError, match='verification_storage_changed'):
        registry.register_subject(owner, project.id)
    assert not list(outside.rglob('dirty.bin'))
    assert store.db.execute('SELECT count(*) FROM verification_subjects').fetchone()[0] == 0


def test_second_source_read_cannot_publish_an_external_identical_tree(registered, tmp_path, monkeypatch):
    import shutil

    from flycode.verification import registry as module

    store, _, project, owner, directory = registered
    registry = catalogue(store, directory)
    capture = module.capture
    outside = tmp_path / 'outside'
    outside.mkdir()
    copied = None

    def replace_after_copy(source, destination=None, **kwargs):
        nonlocal copied
        if destination is not None:
            copied = destination
        elif copied is not None:
            parent = copied.parent.parent
            shutil.copytree(copied.parent, outside / copied.parent.name)
            parent.rename(parent.with_name('saved-subjects'))
            parent.symlink_to(outside, target_is_directory=True)
        return capture(source, destination, **kwargs)

    monkeypatch.setattr(module, 'capture', replace_after_copy)
    with pytest.raises(StoreError, match='verification_storage_changed'):
        registry.register_subject(owner, project.id)
    assert store.db.execute('SELECT count(*) FROM verification_subjects').fetchone()[0] == 0
