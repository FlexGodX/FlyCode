"""An expected owner fingerprint must be checked before import has effects."""

import hashlib
import os

import pytest

from flycode.domain.verification import (
    AcceptanceContract,
    AcceptanceCriterion,
    CheckSpec,
)
from flycode.verification.oracle import OracleRegistry


@pytest.fixture
def imported(tmp_path):
    source = tmp_path / 'owner-oracle'
    source.mkdir()
    target = source / 'test_oracle.py'
    target.write_bytes(b'def test_behavior():\n    assert True\n')
    target.chmod(0o640)
    contract = AcceptanceContract(id='import-contract', version=1, criteria=[AcceptanceCriterion(
        id='R1', check_ids=['target', 'regression', 'integration'],
        expected_case_ids=['test_oracle.py::test_behavior'])])
    checks = [CheckSpec(id=stage, stage=stage, targets=['test_oracle.py'],
        dependencies=[] if stage == 'baseline' else ['baseline'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    source_registry = OracleRegistry(tmp_path / 'catalogue')
    expected = source_registry.approve(contract, checks, source)
    runtime = OracleRegistry(tmp_path / 'runtime')
    prior = runtime.approve(contract, checks, source)
    assert prior == expected
    return source, contract, checks, source_registry, runtime, expected


def inventory(root):
    return {path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(),
        path.stat().st_mode) for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('owned_fd', [False, True])
@pytest.mark.parametrize('change', ['bytes', 'mode', 'contract', 'checks', 'invalid_hash'])
def test_changed_import_cannot_replace_the_active_head(imported, owned_fd, change):
    source, contract, checks, _, runtime, expected = imported
    before = inventory(runtime.directory)
    if change == 'bytes':
        (source / 'test_oracle.py').write_bytes(b'def test_behavior():\n    assert False\n')
    elif change == 'mode':
        (source / 'test_oracle.py').chmod(0o600)
    elif change == 'contract':
        contract = contract.model_copy(update={'version': 2})
    elif change == 'checks':
        checks = [item.model_copy(update={'timeout': 7.0}) for item in checks]
    else:
        expected = 'invalid'
    descriptor = os.open(runtime.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        importer = OracleRegistry(runtime.directory, directory_fd=descriptor) if owned_fd else runtime
        with pytest.raises(ValueError, match='expected.*approval|approval.*expected'):
            importer.approve(contract, checks, source, expected_hash=expected)
    finally:
        os.close(descriptor)
    assert inventory(runtime.directory) == before
    old = runtime._heads()['import-contract']
    assert runtime.resolve(old, imported[1]).fingerprint() == old


@pytest.mark.parametrize('owned_fd', [False, True])
def test_exact_import_preserves_registered_bytes_checks_and_modes(imported, tmp_path, owned_fd):
    source, contract, checks, registered, _, expected = imported
    destination = tmp_path / 'new-runtime'
    destination.mkdir(mode=0o700)
    descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        runtime = OracleRegistry(destination, directory_fd=descriptor) if owned_fd else OracleRegistry(destination)
        assert runtime.approve(contract, checks, source, expected_hash=expected) == expected
    finally:
        os.close(descriptor)
    assert OracleRegistry(destination).resolve(expected, contract) == registered.resolve(expected, contract)
    assert (destination / expected / 'files/test_oracle.py').read_bytes() == (source / 'test_oracle.py').read_bytes()
    assert (destination / expected / 'files/test_oracle.py').stat().st_mode & 0o777 == 0o640


def test_expected_hash_does_not_bypass_contract_supersession(imported):
    source, contract, checks, registered, runtime, expected = imported
    newer = contract.model_copy(update={'version': 2})
    key = registered.approve(newer, checks, source)
    assert runtime.approve(newer, checks, source, expected_hash=key) == key
    before = inventory(runtime.directory)
    with pytest.raises(ValueError, match='newer owner version'):
        runtime.approve(contract, checks, source, expected_hash=expected)
    assert inventory(runtime.directory) == before
    assert runtime.resolve(key, newer).fingerprint() == key


@pytest.mark.parametrize('change', ['data', 'project_column', 'missing'])
def test_plan_publication_pins_the_exact_profile_row(tmp_path, monkeypatch, change):
    from flycode.domain.orchestration import AccessScope
    from flycode.domain.verification_control import (
        VerificationBudget,
        VerificationPlanDraft,
        VerificationProfileDraft,
    )
    from flycode.orchestration.projects import Projects
    from flycode.orchestration.store import Store, StoreError
    from flycode.verification.registry import VerificationRegistry

    source = tmp_path / 'project'
    source.mkdir()
    (source / 'product.py').write_text('VALUE = 1\n')
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    store = Store(tmp_path / 'state.sqlite')
    try:
        project = Projects(store).register(source, actor_id='owner')
        other = Projects(store).register(foreign, actor_id='other')
        scope = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
        registry = VerificationRegistry(store, tmp_path / 'registered')
        subject = registry.register_subject(scope, project.id)
        draft = VerificationProfileDraft(title='Original')
        profile = registry.register_profile(scope, project.id, draft)
        request = VerificationPlanDraft(subject_ids={'target': subject.id},
            profile_id=profile.id, profile_version=profile.version,
            budget=VerificationBudget(max_tool_calls=8, max_attempts=1, wall_seconds=30),
            idempotency_key='create-plan')
        original = registry.profile
        calls = []

        def change_after_preflight(owner, identity):
            result = original(owner, identity)
            calls.append(identity)
            if len(calls) == 2:
                connection = Store(store.path)
                try:
                    if change == 'data':
                        changed = result.model_copy(update={'draft': result.draft.model_copy(update={'title': 'Changed after preflight'})})
                        connection.db.execute('UPDATE verification_profiles SET data=? WHERE id=?', (changed.model_dump_json(), identity))
                    elif change == 'project_column':
                        connection.db.execute('UPDATE verification_profiles SET project_id=? WHERE id=?', (other.id, identity))
                    else:
                        connection.db.execute('DELETE FROM verification_profiles WHERE id=?', (identity,))
                finally:
                    connection.close()
            return result

        monkeypatch.setattr(registry, 'profile', change_after_preflight)
        with pytest.raises(StoreError, match='verification_profile_changed'):
            registry.create_plan(scope, request)
        assert len(calls) == 2
        for table in ('verification_plans', 'verification_events', 'model_calls', 'tool_calls'):
            assert store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
        assert store.db.execute('SELECT count(*) FROM commands WHERE key=?', ('create-plan',)).fetchone()[0] == 0
        assert (source / 'product.py').read_text() == 'VALUE = 1\n'
    finally:
        store.close()


@pytest.mark.parametrize('acceptance', [False, True])
def test_profile_publication_guard_uses_only_SQL(imported, tmp_path, monkeypatch, acceptance):
    from flycode.domain.orchestration import AccessScope
    from flycode.domain.verification_control import VerificationProfileDraft
    from flycode.orchestration.projects import Projects
    from flycode.orchestration.sources import Sources
    from flycode.orchestration.store import Store
    from flycode.verification.registry import VerificationRegistry

    oracle, contract, checks, _, _, _ = imported
    source = tmp_path / 'project'
    source.mkdir()
    store = Store(tmp_path / 'state.sqlite')
    try:
        project = Projects(store).register(source, actor_id='owner')
        scope = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
        registry = VerificationRegistry(store, tmp_path / 'registered')
        draft = VerificationProfileDraft(title='Original', acceptance=contract, checks=checks) if acceptance else VerificationProfileDraft(title='Original')
        profile = registry.register_profile(scope, project.id, draft, oracle_root=oracle if acceptance else None)
        snapshot = registry.profile(scope, profile.id)

        def forbidden(*args, **kwargs):
            raise AssertionError('Physical inspection inside publication transaction')

        monkeypatch.setattr(registry, 'profile', forbidden)
        monkeypatch.setattr(Sources, 'prepare', forbidden)
        monkeypatch.setattr(OracleRegistry, 'read', forbidden)
        with store.transaction():
            registry._profile_sql(snapshot)
    finally:
        store.close()


@pytest.mark.parametrize('change', ['data', 'project_column', 'missing', 'head'])
def test_acceptance_SQL_guard_rejects_changed_profile(imported, tmp_path, change):
    from flycode.domain.orchestration import AccessScope
    from flycode.domain.verification_control import VerificationProfileDraft
    from flycode.orchestration.projects import Projects
    from flycode.orchestration.store import Store, StoreError
    from flycode.verification.registry import VerificationRegistry

    oracle, contract, checks, _, _, _ = imported
    source, foreign = tmp_path / 'project', tmp_path / 'foreign'
    source.mkdir()
    foreign.mkdir()
    store = Store(tmp_path / 'state.sqlite')
    try:
        project = Projects(store).register(source, actor_id='owner')
        other = Projects(store).register(foreign, actor_id='other')
        scope = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
        registry = VerificationRegistry(store, tmp_path / 'registered')
        profile = registry.register_profile(scope, project.id,
            VerificationProfileDraft(title='Original', acceptance=contract, checks=checks), oracle_root=oracle)
        snapshot = registry.profile(scope, profile.id)
        connection = Store(store.path)
        try:
            if change == 'data':
                altered = profile.model_copy(update={'draft': profile.draft.model_copy(update={'title': 'Altered'})})
                connection.db.execute('UPDATE verification_profiles SET data=? WHERE id=?', (altered.model_dump_json(), profile.id))
            elif change == 'project_column':
                connection.db.execute('UPDATE verification_profiles SET project_id=? WHERE id=?', (other.id, profile.id))
            elif change == 'missing':
                connection.db.execute('DELETE FROM verification_profiles WHERE id=?', (profile.id,))
            else:
                VerificationRegistry(connection).register_profile(scope, project.id,
                    VerificationProfileDraft(title='Newer', acceptance=contract.model_copy(update={'version': 2}), checks=checks),
                    oracle_root=oracle)
        finally:
            connection.close()
        with store.transaction(), pytest.raises(StoreError, match='verification_profile_changed'):
            registry._profile_sql(snapshot)
    finally:
        store.close()
