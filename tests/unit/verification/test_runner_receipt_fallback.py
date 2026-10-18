"""A failed final receipt must not block the registered runner fallback."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import flycode
from flycode.domain.verification import CheckAttempt

SCRIPT = """
import sys
from pathlib import Path
from flycode.verification.runner import run_pytest
print('ready', flush=True)
result = run_pytest(Path(sys.argv[1]), Path(sys.argv[2]), 8, 1, lambda: False)
print(result.model_dump_json(), flush=True)
"""

def test_runner_fallback_rejects_fifo_after_guardian_receipt_write_failure(tmp_path):
    subject = tmp_path / 'subject'
    subject.mkdir()
    marker = tmp_path / 'receipt-replaced'
    code = ("import json, os, time\nfrom pathlib import Path\n"
        "def test_receipt():\n"
        "    root = Path(os.environ['FLYCODE_CASE_REPORT']).parent\n"
        "    path = root / 'process.json'\n"
        "    deadline = time.monotonic() + 3\n"
        "    while not json.loads(path.read_text()).get('started'):\n"
        "        assert time.monotonic() < deadline\n"
        "        time.sleep(.01)\n"
        "    (root / 'process.tmp').write_text('occupied final receipt slot')\n"
        "    path.unlink()\n"
        "    os.mkfifo(path)\n"
        f"    Path({str(marker)!r}).touch()\n")
    (subject / 'test_receipt.py').write_text(code)
    try:
        result = subprocess.run([sys.executable, '-c', SCRIPT, str(subject), str(tmp_path / 'output')],
            env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)},
            capture_output=True, text=True, timeout=5, check=False)
    except subprocess.TimeoutExpired as error:
        assert marker.exists() and error.stdout is not None and b'ready\n' in error.stdout, 'Fixture did not initialize'
        pytest.fail('Runner fallback blocked on FIFO after final receipt write failure')
    assert result.returncode == 0 and not result.stderr, result.stderr
    attempt = CheckAttempt.model_validate_json(result.stdout.splitlines()[-1])
    assert marker.exists() and not attempt.complete and not attempt.stop_confirmed
    assert attempt.state == 'interrupted' and attempt.error_class == 'cleanup'
