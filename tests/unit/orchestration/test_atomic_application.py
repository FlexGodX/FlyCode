"""Real file installation must retain edits made at its last comparison boundary."""

import hashlib

import pytest

from flycode.domain.results import FileChange, FileVersion
from flycode.orchestration import application
from flycode.orchestration.store import StoreError


def file_version(data):
    return FileVersion(sha256=hashlib.sha256(data).hexdigest(), mode=0o644)


@pytest.fixture
def installation(tmp_path):
    source, baseline, candidate, journal = [tmp_path / name for name in ('source', 'before', 'after', 'journal')]
    for directory in (source, baseline, candidate, journal):
        directory.mkdir()
    for directory, data in ((source, b'before\n'), (baseline, b'before\n'), (candidate, b'after\n')):
        (directory / 'value.txt').write_bytes(data)
        (directory / 'value.txt').chmod(0o644)
    change = FileChange(path='value.txt', before=file_version(b'before\n'), after=file_version(b'after\n'))
    return application.Application(journal, source, baseline, candidate), change, source / 'value.txt'


def test_edit_after_last_comparison_survives_installation_and_rollback(installation, monkeypatch):
    installer, change, target = installation
    original = application.version
    inspections = 0

    def compare_then_edit(path):
        nonlocal inspections
        result = original(path)
        if path == target:
            inspections += 1
            if inspections == 2:
                path.write_bytes(b'user concurrent edit\n')
        return result

    monkeypatch.setattr(application, 'version', compare_then_edit)
    with pytest.raises(StoreError, match='integration_conflict'):
        installer.apply((change,), lambda: None)
    assert inspections >= 2
    assert installer.rollback((change,))
    assert target.read_bytes() == b'user concurrent edit\n'


def test_install_and_rollback_keep_original_inode_and_mode(installation):
    installer, change, target = installation
    original_inode = target.stat().st_ino
    installer.apply((change,), lambda: None)
    assert target.read_bytes() == b'after\n'
    assert installer.rollback((change,))
    assert target.read_bytes() == b'before\n'
    assert target.stat().st_ino == original_inode
    assert target.stat().st_mode & 0o777 == 0o644


def test_rollback_preserves_new_edit_to_installed_file(installation):
    installer, change, target = installation
    installer.apply((change,), lambda: None)
    target.write_bytes(b'user after installation\n')
    assert not installer.rollback((change,))
    assert target.read_bytes() == b'user after installation\n'


def test_late_write_through_original_descriptor_invalidates_application(installation):
    installer, change, target = installation
    with target.open('r+b') as original:
        installer.apply((change,), lambda: None)
        assert installer.current((change,))
        original.write(b'late user bytes\n')
        original.truncate()
        original.flush()
        assert not installer.current((change,))
    assert installer.rollback((change,))
    assert target.read_bytes() == b'late user bytes\n'


def test_restart_rolls_back_using_saved_original_object(installation):
    installer, change, target = installation
    inode = target.stat().st_ino
    installer.apply((change,), lambda: None)
    restarted = application.Application(installer.directory, installer.source, installer.baseline, installer.candidate)
    assert restarted.rollback((change,))
    assert target.read_bytes() == b'before\n' and target.stat().st_ino == inode


def test_completed_rollback_replay_preserves_late_original_and_new_owner_edits(installation):
    installer, change, target = installation
    with target.open('r+b') as original:
        installer.apply((change,), lambda: None)
        original.write(b'late original bytes\n')
        original.truncate()
        original.flush()
        assert installer.rollback((change,))
    assert target.read_bytes() == b'late original bytes\n'
    target.write_bytes(b'owner continued editing after rollback\n')
    inode = target.stat().st_ino
    restarted = application.Application(installer.directory, installer.source, installer.baseline, installer.candidate)
    assert restarted.rollback((change,))
    assert target.read_bytes() == b'owner continued editing after rollback\n'
    assert target.stat().st_ino == inode


@pytest.mark.parametrize('deleting', [False, True])
def test_create_delete_and_rollback_preserve_objects(installation, deleting):
    installer, _, target = installation
    original_inode = target.stat().st_ino
    if deleting:
        change = FileChange(path='value.txt', before=file_version(b'before\n'), after=None)
    else:
        target.unlink()
        change = FileChange(path='value.txt', before=None, after=file_version(b'after\n'))
    installer.apply((change,), lambda: None)
    assert target.exists() is not deleting
    assert installer.rollback((change,))
    if deleting:
        assert target.read_bytes() == b'before\n'
        assert target.stat().st_ino == original_inode
    else:
        assert not target.exists()


@pytest.mark.parametrize('deleting', [False, True])
def test_concurrent_create_or_delete_never_discards_user_bytes(installation, monkeypatch, deleting):
    installer, _, target = installation
    if deleting:
        change = FileChange(path='value.txt', before=file_version(b'before\n'), after=None)
    else:
        target.unlink()
        change = FileChange(path='value.txt', before=None, after=file_version(b'after\n'))
    original = application.version
    inspections = 0

    def compare_then_edit(path):
        nonlocal inspections
        result = original(path)
        if path == target:
            inspections += 1
            if inspections == 2:
                path.write_bytes(b'user concurrent bytes\n')
        return result

    monkeypatch.setattr(application, 'version', compare_then_edit)
    with pytest.raises((OSError, StoreError)):
        installer.apply((change,), lambda: None)
    assert inspections == 2
    installer.rollback((change,))
    assert target.read_bytes() == b'user concurrent bytes\n'


def test_partial_apply_and_stop_restore_prior_files(installation):
    installer, change, target = installation
    second = FileChange(path='other.txt', before=None, after=file_version(b'after\n'))
    (installer.candidate / 'other.txt').write_bytes(b'after\n')
    (installer.candidate / 'other.txt').chmod(0o644)
    calls = 0

    def stop_after_first_file():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise StoreError('cancelled')

    with pytest.raises(StoreError, match='cancelled'):
        installer.apply((change, second), stop_after_first_file)
    assert installer.rollback((change, second))
    assert target.read_bytes() == b'before\n'
    assert not (installer.source / 'other.txt').exists()
