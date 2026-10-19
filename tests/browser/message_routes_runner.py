"""Real protected owner route form against an isolated live Host."""

import argparse
import json
import os
import shutil
import subprocess
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from owner_runner import (
    REPO,
    counts,
    inventory,
    live_parent,
    managed_pipeline,
    model_server,
    native_app,
    parent_ready,
    pinned_parent,
    pipeline_ready,
)
from test_owner_message_routes import communication_parent

import flycode
from flycode.orchestration.attach import AttachmentServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    assert not root.is_relative_to(REPO)
    root.mkdir(parents=True, exist_ok=False)
    node = shutil.which(os.environ.get('FLYCODE_NODE', 'node'))
    assert node
    expected = Path(os.environ.get('FLYCODE_EXPECTED_PACKAGE', str(REPO / 'src')))
    assert Path(flycode.__file__).is_relative_to(expected), flycode.__file__
    inputs = inventory()
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    report = {'passed': False, 'prepared': False, 'root': str(root)}
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            patch.setenv('FLYCODE_STATE_DATABASE', str(root / 'state.sqlite'))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(
                root, server, patch, SimpleNamespace(param=None)))
            prepared = parent_ready.__wrapped__(pipeline_ready.__wrapped__(managed), server)
            p = stack.enter_context(contextmanager(live_parent.__wrapped__)(
                prepared, managed, patch, SimpleNamespace(param=None)))
            p = communication_parent.__wrapped__(pinned_parent.__wrapped__(p))
            store = p['store']
            before = counts(store)
            assert before['attempts'] == before['tool_calls'] == 0
            stack.enter_context(AttachmentServer(store.host))
            _, client = stack.enter_context(native_app(p['source'], root / 'history'))
            report.update(prepared=True, before=before, database=str(store.path))
            env = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
                'FLYCODE_OWNER_TASK': p['parent'].id, 'FLYCODE_MESSAGE_SENDER': p['child'].id,
                'FLYCODE_SCREENSHOTS': str(root / 'screenshots')}
            with (root / 'browser.log').open('w') as output:
                completed = subprocess.run([node, str(REPO / 'tests/browser/message-routes.cjs')],
                    env=env, stdout=output, stderr=subprocess.STDOUT, timeout=70, check=False)
            assert completed.returncode == 0, (root / 'browser.log').read_text()
            assert counts(store)['attempts'] == counts(store)['tool_calls'] == 0
            grants = list(store.db.execute('SELECT * FROM message_route_grants'))
            assert len(grants) == 2
            head = store.db.execute('SELECT grants.data FROM message_route_heads AS heads '
                'JOIN message_route_grants AS grants ON grants.id=heads.grant_id').fetchone()
            assert json.loads(head['data'])['edges'] == []
            report.update(after=counts(store), grants=len(grants), passed=True)
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        report['seconds'] = time.monotonic() - started
        report['inputs_unchanged'] = inventory() == inputs
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        assert report['inputs_unchanged'], 'Source changed during browser verification'


if __name__ == '__main__':
    main()
