"""A saved custody reference never authorizes blocking special-file reads."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event

import pytest

import flycode
from flycode.verification.runner import run_pytest

SCRIPT = '''
import sys
from flycode.domain.verification import ProcessRef
from flycode.verification.custody import stopped
ref = ProcessRef.model_validate_json(sys.argv[1])
print('ready', flush=True)
try:
    with stopped(ref):
        print('accepted', flush=True)
except (OSError, ValueError):
    print('rejected', flush=True)
'''


@pytest.mark.parametrize('name', ['process.lock', 'process.json'])
def test_stopped_custody_rejects_fifo_without_blocking_recovery(tmp_path, name):
    subject = tmp_path / 'subject'
    subject.mkdir()
    (subject / 'test_answer.py').write_text('def test_answer(): assert True')
    result = run_pytest(subject, tmp_path / 'output', 10, 1, lambda: False)
    assert result.stop_confirmed and result.process is not None
    path = tmp_path / 'output' / name
    path.unlink()
    os.mkfifo(path)
    try:
        completed = subprocess.run([sys.executable, '-c', SCRIPT, result.process.model_dump_json()],
            env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)},
            capture_output=True, text=True, timeout=3, check=False)
    except subprocess.TimeoutExpired as error:
        assert error.stdout is not None and b'ready\n' in error.stdout, 'Custody reader did not initialize'
        pytest.fail('Custody reader waited on a FIFO instead of rejecting it')
    assert completed.returncode == 0 and not completed.stderr, completed.stderr
    assert completed.stdout.splitlines() == ['ready', 'rejected']


@pytest.mark.parametrize('stage', ['launch', 'admission'])
def test_guardian_rejects_special_launch_or_admission_record_before_subject_start(tmp_path, monkeypatch, stage):
    from flycode.verification import custody

    subject = tmp_path / 'subject'
    subject.mkdir()
    marker = tmp_path / 'unexpected-start'
    (subject / 'test_answer.py').write_text('from pathlib import Path\ndef test_answer():\n'
        f'    Path({str(marker)!r}).touch()\n')
    owned, prepared = [], Event()
    spawn = subprocess.Popen

    def tracked(*args, **kwargs):
        process = spawn(*args, **kwargs)
        owned.append(process)
        return process

    def substitute(ref):
        path = Path(ref.directory) / ('process-launch.json' if stage == 'launch' else 'process.json')
        path.unlink()
        os.mkfifo(path)
        prepared.set()

    monkeypatch.setattr(custody.subprocess, 'Popen', tracked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_pytest, subject, tmp_path / 'output', 8, 1, lambda: False,
            **({'on_prepare': substitute} if stage == 'launch' else {'on_admit': substitute}))
        try:
            assert prepared.wait(4), 'Guardian fixture did not reach durable preparation'
            try:
                result = future.result(timeout=2)
            except TimeoutError:
                pytest.fail('Guardian blocked reading a special launch/admission record')
            assert not result.complete and not marker.exists()
            assert owned and all(process.poll() is not None for process in owned)
        finally:
            # Explicit Popen handles belong to this fixture; no receipt PID is signalled.
            for process in owned:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            future.result(timeout=8)
