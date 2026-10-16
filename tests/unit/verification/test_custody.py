"""A coordinator crash cannot leave registered pytest descendants executing."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'Owned process did not reach the expected state'
        time.sleep(.02)


@pytest.mark.parametrize('linger', [False, True])
def test_coordinator_crash_stops_registered_pytest_group_and_records_receipt(tmp_path, linger):
    subject = tmp_path / 'subject'
    subject.mkdir()
    marker = tmp_path / 'owned-processes.json'
    counter = tmp_path / 'executions.txt'
    output = tmp_path / 'output'
    (subject / 'test_owned.py').write_text(
        'import json, os, subprocess, sys, time\nfrom pathlib import Path\n'
        'def test_owned():\n'
        f'    with Path({str(counter)!r}).open("a") as stream: stream.write("executed\\n")\n'
        '    child = subprocess.Popen([sys.executable, "-c", '
        + repr('import signal, time; ' + ('signal.signal(signal.SIGTERM, signal.SIG_IGN); ' if linger else '')
            + 'print("ready", flush=True); time.sleep(60)') + '], stdout=subprocess.PIPE)\n'
        '    assert child.stdout.readline().strip() == b"ready"\n'
        f'    Path({str(marker)!r}).write_text(json.dumps(dict(leader=os.getpid(), child=child.pid, group=os.getpgrp())))\n'
        '    time.sleep(60)\n')
    coordinator = subprocess.Popen([sys.executable, '-c',
        ('from pathlib import Path; from flycode.verification.runner import run_pytest; '
         f'run_pytest(Path({str(subject)!r}), Path({str(output)!r}), 45, 1, lambda: False)')],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owned = None
    try:
        wait_for(marker.exists)
        owned = json.loads(marker.read_text())
        coordinator.kill()
        coordinator.wait(timeout=5)
        receipt = output / 'process.json'
        if linger:
            from flycode.domain.verification import ProcessRef
            from flycode.verification.custody import stopped

            ref = ProcessRef.model_validate(json.loads(receipt.read_text())['ref'])
            with pytest.raises(BlockingIOError), stopped(ref):
                pytest.fail('Custody was released while a descendant was executing')
        wait_for(lambda: receipt.exists() and json.loads(receipt.read_text()).get('state') == 'stopped')
        stopped = json.loads(receipt.read_text())
        assert stopped['stop_confirmed'] and stopped['started']
        assert stopped['reason'] == 'coordinator_lost'
        wait_for(lambda: not group_exists(owned['group']))
        assert counter.read_text() == 'executed\n'
        assert not (output / 'verdict.json').exists()
    finally:
        if coordinator.poll() is None:
            coordinator.kill()
        coordinator.wait(timeout=5)
        # These IDs came from the fixed fixture this harness launched, never
        # from a project under review or a model-selected command.
        if owned and group_exists(owned['group']):
            assert owned['group'] != os.getpgrp()
            os.killpg(owned['group'], signal.SIGKILL)


def group_exists(group):
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process ownership contract')
def test_registered_runner_reports_normal_completion_and_confirmed_stop(tmp_path):
    from flycode.verification.runner import run_pytest

    subject = tmp_path / 'subject'
    subject.mkdir()
    (subject / 'test_pass.py').write_text('def test_pass():\n    assert True\n')
    result = run_pytest(subject, tmp_path / 'output', 10, 1, lambda: False)
    assert result.complete and result.exit_code == 0
    assert result.process_started and result.stop_confirmed and result.process is not None
    receipt = json.loads((tmp_path / 'output/process.json').read_text())
    assert receipt['state'] == 'stopped' and receipt['stop_confirmed']
    assert receipt['started'] and receipt['reason'] == 'completed'


def test_stop_before_durable_admission_never_executes_subject(tmp_path):
    from flycode.verification.runner import run_pytest

    subject = tmp_path / 'subject'
    subject.mkdir()
    effect = tmp_path / 'unexpected-effect'
    (subject / 'test_effect.py').write_text(
        f'from pathlib import Path\ndef test_effect():\n    Path({str(effect)!r}).touch()\n')
    output = tmp_path / 'output'
    result = run_pytest(subject, output, 10, 1, lambda: (output / 'process.json').exists())
    assert result.cancelled and not result.process_started and result.stop_confirmed
    assert not effect.exists()


def test_custody_receipt_cannot_substitute_a_replaced_lock_object(tmp_path):
    from flycode.verification.custody import stopped
    from flycode.verification.runner import run_pytest

    subject = tmp_path / 'subject'
    subject.mkdir()
    (subject / 'test_pass.py').write_text('def test_pass():\n    assert True\n')
    output = tmp_path / 'output'
    result = run_pytest(subject, output, 10, 1, lambda: False)
    assert result.process is not None
    with stopped(result.process) as receipt:
        assert receipt.stop_confirmed
    (output / 'process.lock').rename(output / 'retained.lock')
    (output / 'process.lock').touch()
    with pytest.raises(ValueError, match='ownership object changed'), stopped(result.process):
        pytest.fail('A replacement ownership object was trusted')


def test_rejected_durable_admission_never_executes_subject(tmp_path):
    from flycode.verification.runner import run_pytest

    subject = tmp_path / 'subject'
    subject.mkdir()
    effect = tmp_path / 'unexpected-effect'
    (subject / 'test_effect.py').write_text(
        f'from pathlib import Path\ndef test_effect():\n    Path({str(effect)!r}).touch()\n')
    def reject(ref):
        raise ValueError('The canonical fence changed')
    result = run_pytest(subject, tmp_path / 'output', 10, 1, lambda: False, on_admit=reject)
    assert not result.complete and not result.process_started and result.stop_confirmed
    assert not effect.exists()


def test_relative_subject_is_resolved_in_coordinator_directory(tmp_path, monkeypatch):
    from flycode.verification.runner import run_pytest

    subject = tmp_path / 'subject'
    subject.mkdir()
    (subject / 'test_pass.py').write_text('def test_pass():\n    assert True\n')
    monkeypatch.chdir(tmp_path)
    result = run_pytest(Path('subject'), tmp_path / 'output', 10, 1, lambda: False)
    assert result.complete and result.exit_code == 0 and result.stop_confirmed


def test_unsupported_custody_is_blocked_without_legacy_execution(tmp_path, monkeypatch):
    from flycode.verification import custody
    from flycode.verification.runner import run_pytest

    subject = tmp_path / 'subject'
    subject.mkdir()
    effect = tmp_path / 'unexpected-effect'
    (subject / 'test_effect.py').write_text(
        f'from pathlib import Path\ndef test_effect():\n    Path({str(effect)!r}).touch()\n')
    monkeypatch.setattr(custody, 'supported', lambda: False, raising=False)
    result = run_pytest(subject, tmp_path / 'output', 10, 1, lambda: False)
    assert result.state == 'blocked' and result.error_class == 'unavailable'
    assert not result.process_started and not effect.exists()
