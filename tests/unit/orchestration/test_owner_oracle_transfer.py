"""Runtime oracle import reads pinned regular bytes and activates complete copies."""

import importlib.util
import os
import shutil
import time

import pytest
from test_owner_recipe_profiles import (  # noqa: F401
    live_parent,
    managed_pipeline,
    parent_ready,
    pinned_parent,
    pipeline_ready,
)

from flycode.orchestration.store import StoreError
from flycode.verification.oracle import OracleRegistry
from flycode.verification.registry import VerificationRegistry


def transfer(p):
    assert importlib.util.find_spec('flycode.verification.oracle_transfer') is not None, 'Pinned oracle transfer is missing'
    from flycode.verification.oracle_transfer import OracleTransfer
    return OracleTransfer(p['store'])


def profile_directory(p, profile):
    registry = VerificationRegistry(p['store'])
    project = registry._project(p['owner'], profile.project_id)
    storage = registry._storage(project)
    return registry._directory(storage, 'oracles', project.id, profile.id)


def clear_runtime(p):
    directory = p['dispatcher'].root / 'verification/oracles'
    shutil.rmtree(directory)
    return directory


def test_registered_import_never_uses_legacy_path_oracle_reader(pinned_parent, monkeypatch):  # noqa: F811
    p = pinned_parent
    clear_runtime(p)
    def forbidden(*args, **kwargs):
        pytest.fail('Registered import used an unbounded path reader')
    monkeypatch.setattr(OracleRegistry, 'read', forbidden)
    profile = p['profile_pins'][p['child'].id]
    assert transfer(p).install(p['owner'], profile) == profile.approval_hash


def test_host_loss_during_staging_cannot_activate_runtime_head(pinned_parent, monkeypatch):  # noqa: F811
    from flycode.verification import oracle_transfer

    p = pinned_parent
    directory = clear_runtime(p)
    original = oracle_transfer.write_files
    def drain(descriptor, files, modes):
        original(descriptor, files, modes)
        if 'approval.json' in files:
            p['store'].host.drain()
    monkeypatch.setattr(oracle_transfer, 'write_files', drain)
    profile = p['profile_pins'][p['child'].id]
    with pytest.raises(StoreError, match='host_'):
        transfer(p).install(p['owner'], profile)
    assert not (directory / 'heads.json').exists()


def test_truncated_pending_file_is_never_overwritten_or_activated(pinned_parent, monkeypatch):  # noqa: F811
    from flycode.verification import oracle_transfer

    p = pinned_parent
    directory = clear_runtime(p)
    profile = p['profile_pins'][p['child'].id]
    original = oracle_transfer.write_files
    def truncated(descriptor, files, modes):
        if 'approval.json' in files:
            original(descriptor, {'approval.json': b'{truncated'}, {'approval.json': 0o600})
            raise OSError('interrupted during a file write')
        original(descriptor, files, modes)
    monkeypatch.setattr(oracle_transfer, 'write_files', truncated)
    with pytest.raises(OSError, match='interrupted'):
        transfer(p).install(p['owner'], profile)
    monkeypatch.setattr(oracle_transfer, 'write_files', original)
    with pytest.raises(ValueError, match='bytes or modes'):
        transfer(p).install(p['owner'], profile)
    assert (directory / profile.approval_hash / 'approval.json').read_bytes() == b'{truncated'
    assert not (directory / 'heads.json').exists()


def test_distinct_contracts_and_exact_retry_preserve_both_runtime_heads(pinned_parent):  # noqa: F811
    p = pinned_parent
    directory = clear_runtime(p)
    service = transfer(p)
    profiles = [p['profile_pins'][p[key].id] for key in ('child', 'parent')]
    for profile in profiles:
        assert service.install(p['owner'], profile) == profile.approval_hash
    heads = OracleRegistry(directory)._heads()
    assert heads == {profile.draft.acceptance.id: profile.approval_hash for profile in profiles}
    for profile in profiles:
        assert service.install(p['owner'], profile) == profile.approval_hash
        assert OracleRegistry(directory).resolve(profile.approval_hash, profile.draft.acceptance).fingerprint() == profile.approval_hash
    assert OracleRegistry(directory)._heads() == heads
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


@pytest.mark.parametrize('kind', ['bytes', 'mode', 'symlink', 'hardlink', 'fifo', 'missing', 'extra', 'ancestor'])
def test_changed_source_after_profile_preflight_cannot_change_runtime_heads(pinned_parent, monkeypatch, tmp_path, kind):  # noqa: F811
    p = pinned_parent
    profile = p['profile_pins'][p['child'].id]
    directory = p['dispatcher'].root / 'verification/oracles'
    before = (directory / 'heads.json').read_bytes()
    source = profile_directory(p, profile) / profile.approval_hash
    leaf = source / 'files/tests/test_parser.py'
    original, changed = VerificationRegistry.profile, []

    def replace(registry, scope, identity):
        result = original(registry, scope, identity)
        if identity == profile.id and not changed:
            changed.append(identity)
            if kind == 'bytes':
                leaf.write_bytes(b'changed')
            elif kind == 'mode':
                leaf.chmod(0o700)
            elif kind in {'symlink', 'hardlink', 'fifo', 'missing'}:
                raw = leaf.read_bytes()
                leaf.unlink()
                if kind in {'symlink', 'hardlink'}:
                    outside = tmp_path / 'outside.py'
                    outside.write_bytes(raw)
                    outside.chmod(0o644)
                    leaf.symlink_to(outside) if kind == 'symlink' else os.link(outside, leaf)
                    if kind == 'hardlink':
                        os.link(leaf, tmp_path / 'another-link')
                elif kind == 'fifo':
                    os.mkfifo(leaf)
            elif kind == 'extra':
                (source / 'files/extra.py').write_bytes(b'not approved')
            else:
                moved = source.with_name('moved-approval')
                source.rename(moved)
                source.symlink_to(moved, target_is_directory=True)
        return result

    monkeypatch.setattr(VerificationRegistry, 'profile', replace)
    start = time.monotonic()
    with pytest.raises((ValueError, OSError)):
        transfer(p).install(p['owner'], profile)
    assert time.monotonic() - start < 3
    assert changed and (directory / 'heads.json').read_bytes() == before
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def test_new_owner_version_cannot_reactivate_an_older_runtime_contract(pinned_parent):  # noqa: F811
    p = pinned_parent
    profile = p['profile_pins'][p['child'].id]
    source = profile_directory(p, profile) / profile.approval_hash / 'files'
    newer = VerificationRegistry(p['store']).register_profile(p['owner'], profile.project_id,
        profile.draft.model_copy(update={'acceptance': profile.draft.acceptance.model_copy(update={'version': 2})}), oracle_root=source)
    service = transfer(p)
    assert service.install(p['owner'], newer) == newer.approval_hash
    old = OracleRegistry(profile_directory(p, profile)).read(profile.approval_hash)
    files = {name: (source / name).read_bytes() for name in old.files}
    before = (p['dispatcher'].root / 'verification/oracles/heads.json').read_bytes()
    with pytest.raises(ValueError):
        service.import_captured(old, files, expected_hash=profile.approval_hash)
    assert (p['dispatcher'].root / 'verification/oracles/heads.json').read_bytes() == before


def test_expected_hash_is_checked_before_destination_creation(pinned_parent):  # noqa: F811
    p = pinned_parent
    profile = p['profile_pins'][p['child'].id]
    source = profile_directory(p, profile) / profile.approval_hash
    approved = OracleRegistry(source.parent).read(profile.approval_hash)
    files = {name: (source / 'files' / name).read_bytes() for name in approved.files}
    directory = clear_runtime(p)
    with pytest.raises(ValueError):
        transfer(p).import_captured(approved, files, expected_hash='0' * 64)
    assert not directory.exists()


def test_partial_private_copy_retries_without_activating_incomplete_bytes(pinned_parent, monkeypatch):  # noqa: F811
    p = pinned_parent
    profile = p['profile_pins'][p['child'].id]
    directory = clear_runtime(p)
    service = transfer(p)
    from flycode.verification import oracle_transfer as module
    original, failed = module.write_files, []

    def interrupt(descriptor, files, modes):
        result = original(descriptor, files, modes)
        if 'approval.json' in files and not failed:
            failed.append(True)
            raise RuntimeError('crash before activation')
        return result

    with monkeypatch.context() as patch:
        patch.setattr(module, 'write_files', interrupt)
        with pytest.raises(RuntimeError, match='crash before activation'):
            service.install(p['owner'], profile)
    assert failed and not (directory / 'heads.json').exists()
    assert service.install(p['owner'], profile) == profile.approval_hash
    assert OracleRegistry(directory).resolve(profile.approval_hash, profile.draft.acceptance).fingerprint() == profile.approval_hash


def test_replaced_destination_directory_is_not_written_or_activated(pinned_parent, monkeypatch):  # noqa: F811
    p = pinned_parent
    profile = p['profile_pins'][p['parent'].id]
    directory = clear_runtime(p)
    service = transfer(p)
    from flycode.verification import oracle_transfer as module
    original, replaced = module.write_files, []

    def replace(descriptor, files, modes):
        result = original(descriptor, files, modes)
        if 'approval.json' in files and not replaced:
            replaced.append(True)
            target = directory / profile.approval_hash
            target.rename(directory / 'old-owned-copy')
            target.mkdir()
            (target / 'sentinel').write_bytes(b'keep replacement')
        return result

    monkeypatch.setattr(module, 'write_files', replace)
    with pytest.raises((ValueError, OSError)):
        service.install(p['owner'], profile)
    assert replaced and not (directory / 'heads.json').exists()
    target = directory / profile.approval_hash
    assert {item.name for item in target.iterdir()} == {'sentinel'}
    assert (target / 'sentinel').read_bytes() == b'keep replacement'
