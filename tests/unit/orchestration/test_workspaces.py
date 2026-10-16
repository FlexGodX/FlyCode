"""Attempt workspaces preserve exact input bytes and never modify the source."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.orchestration.store import Store, StoreError


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True).stdout


def test_workspace_has_independent_fixed_checkout_and_preserves_source(owned, tmp_path):
    from flycode.orchestration.workspaces import Workspaces

    store, owner, host, workflow, _, attempt, source = owned
    (source / 'raw.bin').write_bytes(b'\x00\xff\r\n')
    (source / 'данные [1].txt').write_bytes(b'unchanged Unicode path\n')
    (source / 'script.sh').write_text('#!/bin/sh\necho ok\n')
    (source / 'script.sh').chmod(0o755)
    workspaces = Workspaces(store, tmp_path / 'artifacts')
    record = workspaces.prepare(host, attempt.id, attempt.fence, source)
    repo = workspaces.repository(owner, attempt.id)
    assert record.snapshot.base is None
    assert record.checkout_revision == git(repo, 'rev-parse', 'HEAD').decode().strip()
    assert git(repo, 'status', '--porcelain') == b''
    assert git(repo, 'show', 'HEAD:raw.bin') == b'\x00\xff\r\n'
    assert git(repo, 'show', 'HEAD:данные [1].txt') == b'unchanged Unicode path\n'
    assert (repo / 'script.sh').stat().st_mode & 0o777 == 0o755
    assert workspaces.get(owner, attempt.id) == record
    assert workspaces.prepare(host, attempt.id, attempt.fence, source) == record
    (repo / 'src/example.py').write_text('changed in worker\n')
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    assert workspaces.get(owner, attempt.id) == record
    assert sum(e.kind == 'workspace_created' for e in store.events(owner, workflow.id)) == 1


def test_dirty_git_inventory_modes_and_untracked_are_preserved_without_hooks(owned, tmp_path, monkeypatch):
    from flycode.orchestration.workspaces import Workspaces

    store, _, host, _, _, attempt, source = owned
    git(source, 'init', '--quiet')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Workspace test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'Initial')
    original_head = git(source, 'rev-parse', 'HEAD').decode().strip()
    raw_source = 'answer = 1\r\n'.encode('utf-16')
    (source / 'src/example.py').write_bytes(raw_source)
    (source / 'untracked.txt').write_bytes(b'keep untracked\n')
    (source / '.gitattributes').write_text('*.py text eol=lf working-tree-encoding=UTF-16\n')
    before = git(source, 'status', '--porcelain', '-z')
    hooks = tmp_path / 'untrusted-hooks'
    hooks.mkdir()
    marker = tmp_path / 'hook-ran'
    (hooks / 'pre-commit').write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    (hooks / 'pre-commit').chmod(0o755)
    config = tmp_path / 'gitconfig'
    config.write_text(f'[core]\n hooksPath = {hooks}\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    workspaces = Workspaces(store, tmp_path / 'artifacts')
    record = workspaces.prepare(host, attempt.id, attempt.fence, source)
    repo = workspaces.repository(host, attempt.id)
    assert record.snapshot.base == original_head
    assert any('untracked.txt' in entry for entry in record.snapshot.inventory)
    assert git(repo, 'show', 'HEAD:src/example.py') == raw_source
    assert (repo / 'untracked.txt').read_bytes() == b'keep untracked\n'
    assert git(source, 'status', '--porcelain', '-z') == before
    assert git(source, 'rev-parse', 'HEAD').decode().strip() == original_head
    assert not marker.exists()


def test_workspace_manifest_and_baseline_tampering_are_detected(owned, tmp_path):
    from flycode.orchestration.workspaces import Workspaces

    store, owner, host, _, _, attempt, source = owned
    workspaces = Workspaces(store, tmp_path / 'artifacts')
    record = workspaces.prepare(host, attempt.id, attempt.fence, source)
    directory = workspaces.root / record.id
    manifest = directory / 'workspace.json'
    original = manifest.read_bytes()
    manifest.write_text('{}')
    with pytest.raises(StoreError, match='workspace_integrity'):
        workspaces.repository(owner, attempt.id)
    manifest.write_bytes(original)
    (directory / 'baseline/src/example.py').write_text('changed baseline')
    with pytest.raises(StoreError, match='workspace_integrity'):
        workspaces.repository(owner, attempt.id)


def test_workspace_never_uses_project_as_artifact_storage(owned):
    from flycode.orchestration.workspaces import Workspaces

    store, _, host, _, _, attempt, source = owned
    with pytest.raises(StoreError, match='workspace_location'):
        Workspaces(store, source / 'artifacts').prepare(host, attempt.id, attempt.fence, source)
    assert not (source / 'artifacts').exists()


def test_revoked_attempt_cannot_create_a_workspace(owned, tmp_path):
    from flycode.orchestration.workspaces import Workspaces

    store, _, host, _, _, attempt, source = owned
    store.recover(host)
    with pytest.raises(StoreError, match='fence'):
        Workspaces(store, tmp_path / 'artifacts').prepare(host, attempt.id, attempt.fence, source)
    assert not (tmp_path / 'artifacts').exists()


def test_extra_excluded_baseline_file_is_still_tampering(owned, tmp_path):
    from flycode.orchestration.workspaces import Workspaces

    store, owner, host, _, _, attempt, source = owned
    workspaces = Workspaces(store, tmp_path / 'artifacts')
    record = workspaces.prepare(host, attempt.id, attempt.fence, source)
    (workspaces.root / record.id / 'baseline/.env').write_text('unexpected bytes')
    with pytest.raises(StoreError, match='workspace_integrity'):
        workspaces.get(owner, attempt.id)


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_preparation_delegates_to_shared_capture_without_starting_execution(owned, tmp_path, monkeypatch):
    from flycode.orchestration import workspaces as module

    store, owner, host, workflow, _, attempt, source = owned
    capture = module.capture
    calls = []
    def observed(root, destination):
        assert not store.db.in_transaction
        calls.append((root, destination.name))
        return capture(root, destination)
    monkeypatch.setattr(module, 'capture', observed)
    module.Workspaces(store, tmp_path / 'artifacts').prepare(host, attempt.id, attempt.fence, source)
    assert calls == [(source, 'baseline')]
    assert store._attempt(attempt.id).state == 'assigned'
    assert store.workflow(owner, workflow.id).started_at is None
    assert store.budget(owner, workflow.id).spent.model_calls == 0
    assert store.budget(owner, workflow.id).spent.tool_calls == 0


@pytest.mark.parametrize('owned', [{'base_snapshot_hash': 'owner-approved-other-bytes'}], indirect=True)
def test_changed_approved_base_blocks_workspace_publication(owned, tmp_path):
    from flycode.orchestration.workspaces import Workspaces

    store, _, host, _, _, attempt, source = owned
    with pytest.raises(StoreError, match='snapshot_changed'):
        Workspaces(store, tmp_path / 'artifacts').prepare(host, attempt.id, attempt.fence, source)
    assert not list((tmp_path / 'artifacts').iterdir())
    assert store.db.execute('SELECT COUNT(*) FROM workspaces').fetchone()[0] == 0


def test_concurrent_preparation_publishes_one_workspace(owned, tmp_path):
    from flycode.orchestration.workspaces import Workspaces

    store, owner, host, workflow, _, attempt, source = owned
    barrier = Barrier(2)
    def prepare(_):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            barrier.wait(timeout=3)
            return Workspaces(connection, tmp_path / 'artifacts').prepare(host, attempt.id, attempt.fence, source)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(prepare, range(2)))
    assert records[0] == records[1]
    assert len(list((tmp_path / 'artifacts').iterdir())) == 1
    assert sum(e.kind == 'workspace_created' for e in store.events(owner, workflow.id)) == 1
