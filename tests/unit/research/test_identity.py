"""Admission must bind executable verification and dispatch policy bytes."""

import pytest


@pytest.mark.parametrize('package', ['verification', 'orchestration', 'tracker'])
def test_policy_fingerprint_changes_with_result_or_dispatch_implementation(tmp_path, monkeypatch, package):
    from flycode.research import identity

    (tmp_path / 'agent.py').write_text('agent = 1')
    (tmp_path / 'cli.py').write_text('cli = 1')
    directory = tmp_path / package
    directory.mkdir()
    implementation = directory / 'policy.py'
    implementation.write_text('allow = False')
    monkeypatch.setattr(identity, '__file__', str(tmp_path / 'research' / 'identity.py'))
    before = identity.implementation_fingerprint()
    implementation.write_text('allow = True')
    assert identity.implementation_fingerprint() != before
