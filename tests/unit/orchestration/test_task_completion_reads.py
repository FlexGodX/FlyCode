"""Completion admission never waits for a writer to a substituted evidence FIFO."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_pipeline_dispatch import pipeline_ready, published_pipeline  # noqa: F401
from test_store import setup  # noqa: F401
from test_task_completions import approved_task, finish_author  # noqa: F401

import flycode

SCRIPT = '''
import os, sys
from pathlib import Path
from flycode.domain.orchestration import AccessScope
from flycode.domain.results import ReviewRecord
from flycode.orchestration.completions import Completions
from flycode.orchestration.store import Store, StoreError
from flycode.orchestration.verification import accepted
from flycode.verification.engine import VerificationEngine
store = Store(Path(sys.argv[1]))
root = Path(sys.argv[2])
scope = AccessScope(actor_id='reader', project_ids={'p'})
print('ready', flush=True)
if sys.argv[4] == 'manifest-race':
    review = store._load('reviews', sys.argv[5], ReviewRecord)
    engine = VerificationEngine(root / 'verification')
    def swap(*args, **kwargs):
        path = engine.data_dir / review.verdict.evidence_id / 'manifest.json'
        path.unlink()
        os.mkfifo(path)
        return True
    engine.current = swap
    try:
        value = accepted(engine, review.verdict, Path(sys.argv[6]), None)
    except (ValueError, OSError):
        value = False
else:
    value = Completions(store, root).current(scope, sys.argv[3])
print(value, flush=True)
store.close()
'''

WORKSPACE_SCRIPT = '''
import sys
from pathlib import Path
from flycode.domain.orchestration import AccessScope
from flycode.orchestration.completions import Completions
from flycode.orchestration.store import Store, StoreError
store = Store(Path(sys.argv[1]))
scope = AccessScope(actor_id='host', project_ids={'p'}, role='runtime')
print('ready', flush=True)
try:
    Completions(store, Path(sys.argv[2])).complete(scope, sys.argv[3], sys.argv[4],
        expected_revision=int(sys.argv[5]), key='fifo-completion')
    print('unexpected success', flush=True)
except StoreError as error:
    print(error.code, flush=True)
store.close()
'''


@pytest.mark.parametrize('name', ['manifest.json', 'run-result.json', 'manifest-race'])
def test_completion_rejects_special_file_without_blocking(approved_task, name):  # noqa: F811
    prepared, review = approved_task
    store, _, _, _, _, _, _, _, dispatcher, *_ = prepared
    proof = finish_author(prepared, review)
    directory = dispatcher.root / 'artifacts' / proof.artifact_id
    if name != 'manifest-race':
        path = directory / name
        path.unlink()
        os.mkfifo(path)
    try:
        result = subprocess.run([sys.executable, '-c', SCRIPT, str(store.path), str(dispatcher.root), proof.id, name,
            review.id, str(directory / 'target')], env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)},
            capture_output=True, text=True, timeout=3, check=False)
    except subprocess.TimeoutExpired as error:
        assert error.stdout and b'ready\n' in error.stdout, 'Reader did not initialize'
        pytest.fail('Completion evidence reader blocked on a FIFO')
    assert result.returncode == 0 and not result.stderr, result.stderr
    assert result.stdout.splitlines() == ['ready', 'False']


def test_completion_preflight_rejects_workspace_manifest_fifo(approved_task):  # noqa: F811
    from flycode.orchestration.artifacts import Artifacts

    prepared, review = approved_task
    store, owner, _, _, _, author, _, _, dispatcher, *_ = prepared
    task = store.task(owner, author.id)
    artifacts = Artifacts(store, dispatcher.root)
    artifact = artifacts.for_attempt(owner, task.current_attempt_id)
    workspace = artifacts.workspaces.get(owner, artifact.attempt_id)
    path = artifacts.workspaces.root / workspace.id / 'workspace.json'
    path.unlink()
    os.mkfifo(path)
    try:
        result = subprocess.run([sys.executable, '-c', WORKSPACE_SCRIPT, str(store.path), str(dispatcher.root), artifact.id,
            review.id, str(task.revision)], env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)},
            capture_output=True, text=True, timeout=3, check=False)
    except subprocess.TimeoutExpired as error:
        assert error.stdout and b'ready\n' in error.stdout, 'Reader did not initialize'
        pytest.fail('Completion preflight blocked on a workspace FIFO')
    assert result.returncode == 0 and not result.stderr, result.stderr
    assert result.stdout.splitlines() == ['ready', 'workspace_integrity']
