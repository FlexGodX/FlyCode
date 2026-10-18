"""Freshness readers reject special evidence without blocking a service thread."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_registered_plan_engine import prepared

import flycode

SCRIPT = '''
import sys
from pathlib import Path
from flycode.domain.verification import VerificationVerdict
from flycode.verification.engine import VerificationEngine
verdict = VerificationVerdict.model_validate_json(sys.argv[2])
print('ready', flush=True)
print(VerificationEngine(Path(sys.argv[1])).current(verdict, Path(sys.argv[3])), flush=True)
'''


@pytest.mark.parametrize('name', ['plan.json', 'snapshot.json', 'manifest.json', 'verdict.json', 'linked-plan'])
def test_current_evidence_fifo_or_link_is_rejected_without_waiting_for_a_writer(tmp_path, name):
    engine, plan, root, source = prepared(tmp_path, 'def test_answer(): assert True')
    result = engine.verify_plan(plan, {'target': root})
    bundle = engine.data_dir / result.evidence_id
    if name == 'linked-plan':
        path = bundle / 'pipe'
        os.mkfifo(path)
        (bundle / 'plan.json').unlink()
        (bundle / 'plan.json').symlink_to(path)
    else:
        path = bundle / name
        path.unlink()
        os.mkfifo(path)
    try:
        completed = subprocess.run([sys.executable, '-c', SCRIPT, str(engine.data_dir), result.model_dump_json(), str(source)],
            env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)},
            capture_output=True, text=True, timeout=3, check=False)
    except subprocess.TimeoutExpired as error:
        assert error.stdout is not None and b'ready\n' in error.stdout, 'Reader did not initialize'
        pytest.fail('Evidence freshness blocked waiting on a non-regular file')
    assert completed.returncode == 0 and not completed.stderr, completed.stderr
    assert completed.stdout.splitlines() == ['ready', 'False']
