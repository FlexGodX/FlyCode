"""Actual CLI processes register inputs and dispatch through the shared Host."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import flycode
from flycode.orchestration.attach import attach_coordinator
from flycode.orchestration.store import Store, default_state_path
from flycode.verification.control import VerificationControl


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    monkeypatch.setenv('LMSTUDIO_BASE_URL', 'http://127.0.0.1:1/v1')
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'test_answer.py').write_text('def test_answer(): assert True\n')
    return root


def run_cli(*args, code=0):
    result = subprocess.run([sys.executable, '-m', 'flycode.cli', *map(str, args)],
        env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).parent.parent)},
        capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == code, result.stdout + result.stderr
    return json.loads(result.stdout if code == 0 else result.stderr)


def registered_plan(tmp_path, source):
    subject = run_cli('verification', 'register-subject', '--project', source)
    profile_file = tmp_path / 'profile.json'
    profile_file.write_text(json.dumps({'title': 'Original tests', 'checks': [{'id': 'original', 'timeout': 5.}]}))
    profile = run_cli('verification', 'register-profile', '--project', source, '--input', profile_file)
    request = tmp_path / 'plan.json'
    request.write_text(json.dumps({'subject_ids': {'target': subject['id']},
        'profile_id': profile['id'], 'profile_version': profile['version'],
        'budget': {'max_tool_calls': 2, 'max_attempts': 3, 'wall_seconds': 60.}, 'idempotency_key': 'plan'}))
    plan = run_cli('verification', 'create', '--project', source, '--input', request)
    assert run_cli('verification', 'create', '--project', source, '--input', request) == plan
    return plan


def test_cli_registers_non_git_inputs_and_draft_without_execution(tmp_path, source):
    plan = registered_plan(tmp_path, source)
    assert plan['state'] == 'draft' and plan['revision'] == 1
    assert run_cli('verification', 'show', plan['id'], '--project', source) == plan
    assert run_cli('verification', 'list', '--project', source)['items'] == [plan]
    store = Store(default_state_path())
    try:
        for table in ('attempts', 'verification_executions', 'model_calls', 'tool_calls'):
            assert not store.db.execute(f'SELECT 1 FROM {table}').fetchone()
    finally:
        store.close()
    assert not (source / '.git').exists()


def test_cli_start_survives_client_exit_and_reproduces_old_evidence(tmp_path, source, coordinator_processes):
    plan = registered_plan(tmp_path, source)
    # Own the daemon's actual Popen in the parent; child CLI exit is not cleanup.
    with attach_coordinator(default_state_path()) as service:
        assert service.status().phase == 'ready'
    receipt = run_cli('verify', '--plan-id', plan['id'], '--project', source,
        '--expected-revision', 1, '--key', 'start')
    assert receipt['state'] == 'queued'

    def settled(identity):
        deadline = time.monotonic() + 12
        store = Store(default_state_path())
        try:
            while time.monotonic() < deadline:
                execution = VerificationControl(store)._execution(identity)
                if execution.state in {'completed', 'interrupted', 'cancelled', 'blocked'}:
                    return execution
                time.sleep(.05)
        finally:
            store.close()
        pytest.fail('Owned verification did not settle')

    first = settled(receipt['id'])
    assert first.current and first.verdict.status == 'passed'
    current = run_cli('verification', 'show', plan['id'], '--project', source)
    assert current['current'] and current['spent'] == 1 and current['reserved'] == 0
    manifest = Path(first.coordinator_root.path) / 'evidence' / first.verdict.evidence_id / 'manifest.json'
    saved = manifest.with_name('saved-manifest.json')
    manifest.rename(saved)
    try:
        assert run_cli('verify', '--evidence-id', first.verdict.evidence_id, '--reproduce', '--project', source,
            '--expected-revision', current['revision'], '--key', 'missing-evidence', code=2)['error']['code'] == 'evidence_changed'
    finally:
        saved.rename(manifest)
    second = run_cli('verify', '--evidence-id', first.verdict.evidence_id, '--reproduce', '--project', source,
        '--expected-revision', current['revision'], '--key', 'reproduce')
    rerun = settled(second['id'])
    assert rerun.current and rerun.verdict.status == 'passed'
    assert rerun.number == 2 and rerun.deadline_at == first.deadline_at
    assert rerun.verdict.evidence_id != first.verdict.evidence_id
    final = run_cli('verification', 'show', plan['id'], '--project', source)
    assert final['spent'] == 2 and len(final['attempts']) == 2
    assert run_cli('verify', '--plan-id', plan['id'], '--operation', 'rerun', '--project', source,
        '--expected-revision', final['revision'], '--key', 'exhausted', code=2)['error']['code'] == 'budget'
    assert coordinator_processes and coordinator_processes[0].poll() is None


def test_cli_rejects_foreign_project_and_unknown_evidence(tmp_path, source, coordinator_processes):
    plan = registered_plan(tmp_path, source)
    other = tmp_path / 'other'
    other.mkdir()
    run_cli('verification', 'register-subject', '--project', other)
    assert run_cli('verification', 'show', plan['id'], '--project', other, code=2)['error']['code'] == 'scope'
    assert run_cli('verify', '--plan-id', plan['id'], '--project', other,
        '--expected-revision', 1, '--key', 'foreign', code=2)['error']['code'] == 'scope'
    assert run_cli('verify', '--evidence-id', '0' * 32, '--reproduce', '--project', source,
        '--expected-revision', 1, '--key', 'absent', code=2)['error']['code'] == 'not_found'


def test_legacy_verify_repo_still_runs_real_registered_pytest(tmp_path, source):
    result = run_cli('verify', '--repo', source, '--output', tmp_path / 'legacy')
    assert result['status'] == 'passed' and result['scope'] == 'registered-suite'


@pytest.mark.parametrize('flags', [('--operation', 'cancel'), ('--reproduce',)])
def test_registered_only_flags_cannot_accidentally_launch_legacy_checks(tmp_path, source, flags):
    marker = tmp_path / 'unrequested-run'
    (source / 'test_answer.py').write_text(
        f'from pathlib import Path\ndef test_answer(): Path({str(marker)!r}).touch()\n')
    result = run_cli('verify', '--repo', source, '--output', tmp_path / 'legacy', *flags, code=2)
    assert result['error']['code'] == 'command_mode'
    assert not marker.exists() and not (tmp_path / 'legacy').exists()
