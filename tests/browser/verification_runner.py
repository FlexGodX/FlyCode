"""Run verification UI against owned HTTP and Coordinator processes."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tests/unit/orchestration'))

from test_application_coordinator import native_app, owned_service_control

from flycode.domain.orchestration import AccessScope
from flycode.domain.verification import CheckSpec
from flycode.domain.verification_control import VerificationProfileDraft
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store
from flycode.verification.registry import VerificationRegistry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--case', choices=['verification', 'desktop', 'projects'], default='verification')
    args = parser.parse_args()
    root = args.output or Path(tempfile.mkdtemp(prefix='flycode-verification-browser-'))
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'source'
    source.mkdir()
    mode = root / 'case-mode'
    mode.write_text('pass')
    marker = root / 'runner-started'
    code = ('from pathlib import Path\nimport time\ndef test_answer():\n'
        f'    mode = Path({str(mode)!r}).read_text()\n'
        f'    Path({str(marker)!r}).touch()\n'
        "    if mode == 'slow': time.sleep(60)\n"
        "    assert mode != 'fail', 'Requested failure is retained'\n")
    (source / 'test_answer.py').write_text(code)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('FLYCODE_STATE_DATABASE', str(root / 'state.sqlite'))
        patch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(root / 'authority'))
        patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
        patch.setenv('LMSTUDIO_BASE_URL', 'http://127.0.0.1:1/v1')
        store = Store(root / 'state.sqlite')
        try:
            project = Projects(store).register(source, actor_id='trusted-owner')
            scope = AccessScope(actor_id='trusted-owner', project_ids={project.id}, role='owner')
            registry = VerificationRegistry(store)
            registry.register_subject(scope, project.id)
            registry.register_profile(scope, project.id, VerificationProfileDraft(title='Проверка проекта',
                checks=[CheckSpec(id='original', timeout=15.)]))
        finally:
            store.close()
        with (owned_service_control() as (_, inherited),
                native_app(source, root / 'history', control=inherited) as (_, client)):
                environment = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
                    'FLYCODE_SCREENSHOTS': str(root / 'screenshots'), 'FLYCODE_MODE_FILE': str(mode),
                    'FLYCODE_CASE_MARKER': str(marker), 'FLYCODE_SOURCE_FILE': str(source / 'test_answer.py'),
                    'FLYCODE_PROJECT_TEST_ROOT': str(root / 'project-cases')}
                with (root / 'browser.log').open('w') as output:
                    result = subprocess.run([os.environ.get('FLYCODE_NODE', 'node'),
                        str(REPO / 'tests/browser' / (args.case + '.cjs'))], env=environment, stdout=output,
                        stderr=subprocess.STDOUT, timeout=120, check=False)
                assert result.returncode == 0, (root / 'browser.log').read_text()
        store = Store(root / 'state.sqlite')
        try:
            assert store.db.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0
            assert store.db.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
            assert (source / 'test_answer.py').read_text() == code
            report = {'passed': True, 'case': args.case, 'plans': store.db.execute('SELECT count(*) FROM verification_plans').fetchone()[0],
                'executions': store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0],
                'model_calls': 0, 'owner_bytes_preserved': True}
            (root / 'result.json').write_text(json.dumps(report, indent=2))
        finally:
            store.close()
    print(root)


if __name__ == '__main__':
    main()
