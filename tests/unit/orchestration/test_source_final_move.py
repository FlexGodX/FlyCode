"""Reproduction only: replacing the source after its last guard must not redirect a rename."""
import shutil
from contextlib import contextmanager

import pytest
from test_atomic_application import installation  # noqa: F401

from flycode.orchestration.sources import Sources
from flycode.orchestration.store import Store, StoreError


@pytest.mark.parametrize('phase', ['apply', 'rollback'])
@pytest.mark.parametrize('nested', [False, True])
def test_root_replacement_at_native_move_never_modifies_replacement(installation, tmp_path, monkeypatch, phase, nested):  # noqa: F811
    installer, change, target = installation
    if nested:
        for root in (installer.source, installer.baseline, installer.candidate):
            (root / 'nested').mkdir()
            (root / 'value.txt').rename(root / 'nested/value.txt')
        change = change.model_copy(update={'path': 'nested/value.txt'})
        target = installer.source / change.path
    relative = target.relative_to(installer.source)
    store = Store(tmp_path / 'source.sqlite')
    sources = Sources(store)
    reference = sources.prepare(installer.source)
    original_root = tmp_path / 'displaced-source'
    checked = []
    moved = []
    def guard():
        if sources.prepare(installer.source) != reference:
            raise StoreError('source_changed')
        checked.append(True)
    try:
        if phase == 'rollback':
            installer.apply((change,), guard)
        expected = b'before\n' if phase == 'apply' else b'after\n'
        checked.clear()
        native = installer.rename.move
        def replace_then_move(*args, **kwargs):
            assert checked and not moved
            installer.source.rename(original_root)
            shutil.copytree(original_root, installer.source)
            assert installer.source.stat().st_ino != original_root.stat().st_ino
            moved.append(True)
            native(*args, **kwargs)
        monkeypatch.setattr(installer.rename, 'move', replace_then_move)
        if phase == 'apply':
            with pytest.raises(StoreError):
                installer.apply((change,), guard)
        else:
            assert not installer.rollback((change,), guard)
        assert moved
        assert (original_root / relative).read_bytes() == expected
        assert target.read_bytes() == expected, 'Native rename wrote into a replacement physical source root'
    finally:
        store.close()


def test_root_replacement_before_parent_creation_does_not_create_in_replacement(installation, tmp_path, monkeypatch):  # noqa: F811
    from flycode.orchestration import application

    installer, change, _ = installation
    relative = 'created/deep/new.txt'
    change = change.model_copy(update={'path': relative, 'before': None})
    (installer.candidate / 'created/deep').mkdir(parents=True)
    (installer.candidate / relative).write_bytes(b'after\n')
    store = Store(tmp_path / 'source.sqlite')
    sources = Sources(store)
    reference = sources.prepare(installer.source)
    previous = tmp_path / 'displaced-source'
    identity = application._identity
    replaced = []
    def guard():
        if sources.prepare(installer.source) != reference:
            raise StoreError('source_changed')
    def replace_after_private_preparation(path):
        result = identity(path)
        if path == installer.recovery / '0' and not replaced:
            installer.source.rename(previous)
            shutil.copytree(previous, installer.source)
            replaced.append(True)
        return result
    monkeypatch.setattr(application, '_identity', replace_after_private_preparation)
    try:
        with pytest.raises(StoreError, match='source_changed'):
            installer.apply((change,), guard)
        assert replaced
        assert not (installer.source / 'created').exists(), 'Preparation created directories in a replaced physical root'
        assert not (previous / 'created').exists()
        assert (installer.source / 'value.txt').read_bytes() == (previous / 'value.txt').read_bytes() == b'before\n'
    finally:
        store.close()


def test_nested_native_parents_remain_relative_to_claimed_root_after_pin(installation, tmp_path, monkeypatch):  # noqa: F811
    from flycode.orchestration import rename

    installer, change, _ = installation
    for root in (installer.source, installer.baseline, installer.candidate):
        (root / 'nested/deep').mkdir(parents=True)
        (root / 'value.txt').rename(root / 'nested/deep/value.txt')
    change = change.model_copy(update={'path': 'nested/deep/value.txt'})
    previous = tmp_path / 'displaced-source'
    directory = rename.directory
    pinned = []
    @contextmanager
    def replace_after_root_pin(path, **kwargs):
        with directory(path, **kwargs) as descriptor:
            if path == installer.source and kwargs.get('identity') is not None and not pinned:
                installer.source.rename(previous)
                shutil.copytree(previous, installer.source)
                pinned.append(True)
            yield descriptor
    monkeypatch.setattr(rename, 'directory', replace_after_root_pin)
    # This changes the pathname after the physical root is pinned. A no-op
    # policy guard isolates the native adapter's supported ownership boundary.
    with pytest.raises(StoreError, match='integration_conflict'):
        installer.apply((change,), lambda: None)
    assert pinned
    assert (installer.source / change.path).read_bytes() == b'before\n'
    assert (previous / change.path).read_bytes() == b'after\n'
