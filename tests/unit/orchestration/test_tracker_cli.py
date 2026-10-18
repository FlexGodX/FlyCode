"""Public CLI processes use the canonical web queue and existing scope gates."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import test_application_coordinator
from test_application_coordinator import native_app, owned_service_control, until

import flycode
from flycode.orchestration.attach import attach_coordinator
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store, default_state_path
from flycode.orchestration.tools import Tools
from flycode.orchestration.wire import receive
from flycode.verification.custody import stopped

managed_pipeline = test_application_coordinator.managed_pipeline


@pytest.fixture(autouse=True)
def cli_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    monkeypatch.setenv('FLYCODE_ARTIFACT_ROOT', str(tmp_path / 'empty-artifacts'))
    monkeypatch.setenv('LMSTUDIO_BASE_URL', 'http://127.0.0.1:1/v1')


def run_cli(*args):
    environment = {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)}
    return subprocess.run([sys.executable, '-m', 'flycode.cli', 'tasks', *map(str, args)],
        env=environment, capture_output=True, text=True, timeout=15, check=False)


def cli(*args, code=0):
    result = run_cli(*args)
    assert result.returncode == code, result.stdout + result.stderr
    assert not (result.stderr if code == 0 else result.stdout)
    return json.loads(result.stdout if code == 0 else result.stderr)


def json_file(path, data):
    path.write_text(json.dumps(data))
    return path


def create(tmp_path, source, *, key='draft', task=None, extra=()):
    draft = json_file(tmp_path / (key + '.json'), task or {'title': 'Repair parser',
        'objective': 'Repair source and preserve owner files.'})
    return cli('create', '--project', source, '--input', draft, '--key', key, *extra)


def test_cli_and_native_web_share_one_restartable_draft_without_execution(tmp_path, monkeypatch, model_server):
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'owner.bin').write_bytes(b'\x00\xff keep owner bytes\r\n')
    project = cli('register', '--project', source)['project_id']
    assert cli('register', '--project', source) == {'project_id': project}
    card = create(tmp_path, source)
    replay = create(tmp_path, source)
    assert card == replay
    identity = card['task']['id']
    assert card['task']['project_id'] == project
    assert card['task']['state'] == 'planned' and card['attempt'] is None
    assert set(card['blockers']) == {'missing_criteria', 'permission', 'missing_budget'}
    assert cli('show', identity, '--project', source) == card
    assert cli('list', '--project', source)['items'] == [card]
    with native_app(source, tmp_path / 'history') as (_, web):
        response = web.get('/api/tasks/' + identity)
        assert response.status_code == 200, response.text
        assert response.json() == card
        assert web.get('/api/tasks').json()['items'] == [card]
    store = Store(default_state_path())
    try:
        assert store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
        assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1
        for table in ('instances', 'attempts', 'workers', 'model_calls', 'tool_calls'):
            assert store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
    finally:
        store.close()
    assert cli('agents', '--project', source) == []
    assert model_server.requests == []
    assert (source / 'owner.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    assert not (source / '.git').exists()


def test_cli_preserves_mutation_revision_keys_and_event_cursors(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    cli('register', '--project', source)
    card = create(tmp_path, source)
    identity = card['task']['id']
    command = {'operation': 'set_metadata', 'expected_revision': 1, 'idempotency_key': 'rename',
        'changes': {'title': 'Renamed parser', 'tags': ['local']}}
    path = json_file(tmp_path / 'command.json', command)
    changed = cli('command', identity, '--project', source, '--input', path)
    assert changed['task']['revision'] == 2 and changed['task']['title'] == 'Renamed parser'
    assert cli('command', identity, '--project', source, '--input', path) == changed
    json_file(path, {**command, 'idempotency_key': 'stale'})
    assert cli('command', identity, '--project', source, '--input', path, code=2) == {
        'error': {'code': 'revision', 'revision': 2}}
    json_file(path, {**command, 'changes': {'title': 'Changed replay must fail'}})
    assert cli('command', identity, '--project', source, '--input', path, code=2) == {'error': {'code': 'idempotency'}}
    comment = cli('comment', identity, '--project', source, '--text', 'Literal <script> feedback', '--key', 'feedback')
    assert cli('comment', identity, '--project', source, '--text', 'Literal <script> feedback', '--key', 'feedback') == comment
    first = cli('events', '--project', source, '--task', identity, '--limit', '1')
    assert len(first['items']) == 1 and first['items'][0]['kind'] == 'workflow_created'
    second = cli('events', '--project', source, '--task', identity, '--limit', '1', '--after', first['next_cursor'])
    assert second['items'][0]['kind'] == 'task_set_metadata'
    third = cli('events', '--project', source, '--task', identity, '--limit', '1', '--after', second['next_cursor'])
    assert third['items'] == [comment] and third['next_cursor'] is None
    journal = cli('events', '--project', source)
    assert [item['kind'] for item in journal['items']] == ['workflow_created', 'task_set_metadata', 'task_comment']
    assert cli('events', '--project', source, '--after', journal['cursor'])['items'] == []
    assert cli('list', '--project', source, '--query', 'RENAMED')['items'][0]['task']['id'] == identity
    assert cli('list', '--project', source, '--state', 'ready')['items'] == []


def test_cli_lost_root_denies_content_but_retains_minimal_stop_replay(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    cli('register', '--project', source)
    card = create(tmp_path, source)
    identity = card['task']['id']
    source.rename(tmp_path / 'displaced')
    source.mkdir()
    (source / 'replacement.txt').write_text('Replacement must stay untouched')
    assert cli('show', identity, '--project', source, code=2) == {'error': {'code': 'project_changed'}}
    assert cli('stops') == {'items': [{'task_id': identity, 'revision': 1, 'state': 'planned', 'paused': False}], 'next_cursor': None}
    args = ('stop', identity, '--operation', 'pause', '--expected-revision', '1', '--key', 'stop')
    paused = cli(*args)
    assert paused == {'task_id': identity, 'revision': 2, 'state': 'planned', 'paused': True}
    assert cli(*args) == paused
    assert cli('stop', identity, '--operation', 'cancel', '--expected-revision', '1', '--key', 'stale', code=2) == {
        'error': {'code': 'revision', 'revision': 2}}
    assert cli('stop', '0' * 32, '--operation', 'cancel', '--expected-revision', '1', '--key', 'foreign', code=2) == {
        'error': {'code': 'scope'}}
    command = json_file(tmp_path / 'resume.json', {'operation': 'resume', 'expected_revision': 2, 'idempotency_key': 'resume'})
    assert cli('command', identity, '--project', source, '--input', command, code=2) == {'error': {'code': 'project_changed'}}
    assert (source / 'replacement.txt').read_text() == 'Replacement must stay untouched'


def test_cli_never_resolved_project_does_not_gain_stop_from_database_metadata(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    store = Store(default_state_path())
    try:
        from flycode.domain.orchestration import AccessScope, TaskDraft, WorkflowDraft

        project = Projects(store).register(source, actor_id='web-owner')
        scope = AccessScope(actor_id='web-owner', project_ids={project.id}, role='owner')
        workflow = store.create_workflow(scope, WorkflowDraft(project_id=project.id,
            task=TaskDraft(title='Private foreign draft')), key='draft')
    finally:
        store.close()
    assert cli('stops') == {'items': [], 'next_cursor': None}
    assert cli('stop', workflow.root_task_id, '--operation', 'pause', '--expected-revision', '1', '--key', 'no-grant', code=2) == {
        'error': {'code': 'scope'}}
    assert cli('show', workflow.root_task_id, '--project', source)['task']['project_id'] == project.id
    assert cli('stops')['items'][0]['task_id'] == workflow.root_task_id


def test_cli_enqueue_requires_explicit_contract_permission_and_both_budgets(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    cli('register', '--project', source)
    card = create(tmp_path, source)
    path = json_file(tmp_path / 'enqueue.json', {'operation': 'enqueue', 'expected_revision': 1, 'idempotency_key': 'enqueue'})
    assert cli('command', card['task']['id'], '--project', source, '--input', path, code=2) == {'error': {'code': 'incomplete'}}
    task = {'title': 'Approved draft', 'objective': 'Read source and preserve owner files.',
        'acceptance': {'id': 'read', 'version': 1, 'criteria': [{'id': 'result', 'check_ids': ['regression'],
            'expected_case_ids': ['tests/test_example.py::test_example']}]},
        'permissions': {'approved': True, 'tools': ['read_file'], 'read_scopes': ['.']},
        'budget': {'model_calls': 2, 'tool_calls': 3}}
    budget = json_file(tmp_path / 'budget.json', {'max_model_calls': 4, 'max_tool_calls': 10})
    complete = create(tmp_path, source, key='approved', task=task, extra=('--workflow-budget', budget))
    json_file(path, {'operation': 'enqueue', 'expected_revision': 1, 'idempotency_key': 'enqueue-approved'})
    ready = cli('command', complete['task']['id'], '--project', source, '--input', path)
    assert ready['task']['state'] == 'ready' and ready['attempt'] is None
    assert 'root_not_active' in ready['blockers']
    store = Store(default_state_path())
    try:
        assert store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 0
        assert store.db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] == 0
        assert store.db.execute('SELECT COUNT(*) FROM tool_calls').fetchone()[0] == 0
    finally:
        store.close()


def test_cli_child_creation_and_foreign_ids_preserve_project_scope(tmp_path):
    source, other = tmp_path / 'source', tmp_path / 'other'
    source.mkdir()
    other.mkdir()
    cli('register', '--project', source)
    cli('register', '--project', other)
    parent = create(tmp_path, source)
    child = create(tmp_path, source, key='child', task={'title': 'Child', 'parent_id': parent['task']['id']},
        extra=('--workflow', parent['task']['workflow_id']))
    assert child['task']['workflow_id'] == parent['task']['workflow_id']
    assert child['task']['parent_id'] == parent['task']['id']
    assert cli('show', child['task']['id'], '--project', other, code=2) == {'error': {'code': 'scope'}}
    path = json_file(tmp_path / 'foreign.json', {'title': 'Foreign child', 'parent_id': parent['task']['id']})
    assert cli('create', '--project', other, '--input', path, '--key', 'foreign', '--workflow', parent['task']['workflow_id'], code=2) == {
        'error': {'code': 'scope'}}
    assert len(cli('list', '--project', source)['items']) == 2
    assert cli('list', '--project', other)['items'] == []


@pytest.mark.parametrize(('kind', 'expected'), [
    ('malformed', 'invalid_fields'), ('nested', 'invalid_fields'), ('unknown-authority', 'invalid_fields'), ('nonfinite', 'invalid_fields'),
    ('oversized', 'invalid_input'), ('fifo', 'invalid_input'), ('directory', 'invalid_input'),
])
def test_cli_rejects_bad_or_blocking_input_without_creating_state(tmp_path, kind, expected):
    source = tmp_path / 'source'
    source.mkdir()
    path = tmp_path / 'input.json'
    if kind == 'malformed':
        path.write_text('{private_secret_value')
    elif kind == 'nested':
        path.write_text('[' * 10000 + '0' + ']' * 10000)
    elif kind == 'unknown-authority':
        json_file(path, {'title': 'Draft', 'actor_id': 'private_secret_value', 'role': 'runtime'})
    elif kind == 'nonfinite':
        path.write_text('{"title":"private_secret_value","due_at":NaN}')
    elif kind == 'oversized':
        path.write_bytes(b' ' * (1024 * 1024 + 1))
    elif kind == 'fifo':
        os.mkfifo(path)
    else:
        path.mkdir()
    assert cli('create', '--project', source, '--input', path, '--key', 'invalid', code=2) == {'error': {'code': expected}}
    assert not default_state_path().exists()


def test_cli_explicit_database_does_not_write_the_default_queue(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    database = tmp_path / 'chosen.sqlite'
    project = cli('--database', database, 'register', '--project', source)['project_id']
    assert database.is_file() and not default_state_path().exists()
    assert cli('--database', database, 'register', '--project', source) == {'project_id': project}


def test_cli_service_status_never_creates_or_starts_a_host(tmp_path):
    assert not default_state_path().exists()
    assert cli('service', 'status', code=2) == {'error': {'code': 'attachment_identity'}}
    assert not default_state_path().exists()
    assert not (tmp_path / 'authority').exists()


# The test channel is inherited only by this owned daemon; no production route
# accepts a PID or exposes a cleanup mechanism.
WRAPPED_CLI = r'''
import os, runpy, subprocess, sys
import flycode.tracker.cli, flycode.orchestration.attach
original = subprocess.Popen
wrapper = """
import os, runpy, signal, socket, sys
from threading import Thread
from flycode.orchestration.wire import receive, send
control = socket.socket(fileno=int(os.environ['TEST_CLI_SERVICE_CONTROL']))
control.set_inheritable(False)
send(control, {'pid': os.getpid(), 'sid': os.getsid(0)})
def stop_self():
    try:
        if receive(control) == {'stop': True}:
            os.kill(os.getpid(), signal.SIGTERM)
    except EOFError:
        pass
Thread(target=stop_self, daemon=True).start()
sys.argv = ['flycode.orchestration.coordinator', *sys.argv[1:]]
runpy.run_module('flycode.orchestration.coordinator', run_name='__main__')
"""
def launch(args, *values, **options):
    if isinstance(args, list) and 'flycode.orchestration.coordinator' in args:
        assert options['start_new_session'] and options['stdin'] == subprocess.DEVNULL
        assert options['stdout'] != subprocess.PIPE and options['stderr'] != subprocess.PIPE
        args = [args[0], '-c', wrapper, *args[3:]]
        options['pass_fds'] = (int(os.environ['TEST_CLI_SERVICE_CONTROL']),)
    return original(args, *values, **options)
subprocess.Popen = launch
sys.argv = ['flycode', *sys.argv[1:]]
runpy.run_module('flycode.cli', run_name='__main__')
'''


def test_cli_launch_exit_and_other_clients_preserve_one_detached_service(tmp_path):
    from flycode.orchestration.host import Host

    source = tmp_path / 'source'
    source.mkdir()
    with Host(default_state_path(), tmp_path / 'host'):
        pass
    with owned_service_control() as (control, inherited):
        environment = {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent),
            'TEST_CLI_SERVICE_CONTROL': str(inherited.fileno())}
        result = subprocess.run([sys.executable, '-c', WRAPPED_CLI, 'tasks', 'service', 'attach'],
            env=environment, pass_fds=(inherited.fileno(),), capture_output=True, text=True, timeout=15, check=False)
        inherited.close()
        assert result.returncode == 0 and not result.stderr, result.stderr
        status = json.loads(result.stdout)
        assert set(status) == {'registration_id', 'session_id', 'generation', 'phase'} and status['phase'] == 'ready'
        metadata = receive(control)
        assert metadata['pid'] == metadata['sid'] and metadata['pid'] != os.getpid()
        assert cli('service', 'status') == status
        assert cli('service', 'attach') == status
        with native_app(source, tmp_path / 'history') as (_, web):
            assert web.get('/api/tasks/coordinator').json() == status
        assert cli('service', 'status') == status
    store = Store(default_state_path())
    try:
        assert json.loads(store.db.execute('SELECT data FROM host_sessions').fetchone()[0])['phase'] == 'stopped'
        for table in ('tasks', 'instances', 'attempts', 'model_calls', 'tool_calls'):
            assert store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize(('managed_pipeline', 'operation', 'root_loss'), [
    ('slow-review', 'pause', True), ('slow-review', 'cancel', False),
    ('slow-integration', 'pause', False), ('slow-integration', 'cancel', True),
], indirect=['managed_pipeline'])
def test_cli_stop_controls_actual_verification_without_refunding_unknown_effects(managed_pipeline, operation, root_loss,
        model_server, tmp_path, monkeypatch, coordinator_processes):
    store, owner, _, workflow, tasks, source, _, _, _, _, approve = managed_pipeline
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    cli('register', '--project', source)
    approve()
    with attach_coordinator(store.path) as client:
        status = client.status().model_dump(mode='json')
    until(lambda: (tmp_path / 'review-started').exists() or (tmp_path / 'integration-started').exists(), timeout=45)
    target = tasks[2] if (tmp_path / 'review-started').exists() else tasks[3]
    assert cli('service', 'status') == status
    before_budget = store.budget(owner, workflow.id)
    before_deadline = store.workflow(owner, workflow.id).deadline_at
    before_calls = len(model_server.requests)
    if root_loss:
        source.rename(tmp_path / 'displaced')
        source.mkdir()
        (source / 'replacement.txt').write_text('Replacement must stay untouched')
        assert cli('show', target.id, '--project', source, code=2) == {'error': {'code': 'project_changed'}}
    handle = next(item for item in cli('stops')['items'] if item['task_id'] == target.id)
    started = time.monotonic()
    receipt = cli('stop', target.id, '--operation', operation, '--expected-revision', handle['revision'], '--key', 'actual-stop')
    assert time.monotonic() - started < 1
    assert set(receipt) == {'task_id', 'revision', 'state', 'paused'}
    until(lambda: any(call.status == 'uncertain' for call in Tools(store).calls(owner, workflow.id)), timeout=10)
    call = next(call for call in Tools(store).calls(owner, workflow.id) if call.status == 'uncertain')
    assert call.process is not None
    def confirmed():
        try:
            with stopped(call.process) as evidence:
                return evidence.started and evidence.stop_confirmed
        except BlockingIOError:
            return False
    until(confirmed, timeout=10)
    assert store.budget(owner, workflow.id).spent == before_budget.spent
    assert store.budget(owner, workflow.id).reserved.tool_calls > 0
    assert store.workflow(owner, workflow.id).deadline_at == before_deadline
    assert len(model_server.requests) == before_calls
    original = tmp_path / 'displaced' if root_loss else source
    assert (original / 'src/parser.py').read_text() == 'value = 0\n'
    assert (original / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    if root_loss:
        assert (source / 'replacement.txt').read_text() == 'Replacement must stay untouched'
    assert len(coordinator_processes) == 1 and coordinator_processes[0].poll() is None


def test_cli_changed_create_replay_does_not_create_a_second_task(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    cli('register', '--project', source)
    original = create(tmp_path, source)
    path = json_file(tmp_path / 'changed-draft.json', {'title': 'Different payload'})
    assert cli('create', '--project', source, '--input', path, '--key', 'draft', code=2) == {'error': {'code': 'idempotency'}}
    assert cli('list', '--project', source)['items'] == [original]


def test_cli_list_stop_keysets_and_archive_use_current_canonical_records(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    cli('register', '--project', source)
    cards = [create(tmp_path, source, key='draft-' + str(n), task={'title': 'Draft ' + str(n)}) for n in range(3)]
    first = cli('list', '--project', source, '--limit', 1)
    second = cli('list', '--project', source, '--limit', 1, '--after', first['next_cursor'])
    third = cli('list', '--project', source, '--limit', 1, '--after', second['next_cursor'])
    assert [first['items'][0]['task']['id'], second['items'][0]['task']['id'], third['items'][0]['task']['id']] == [card['task']['id'] for card in cards]
    assert third['next_cursor'] is None
    handle = cli('stops', '--limit', 1)
    assert handle['items'][0]['task_id'] == cards[0]['task']['id']
    following = cli('stops', '--limit', 1, '--after', handle['next_cursor'])
    assert following['items'][0]['task_id'] == cards[1]['task']['id']
    archive = json_file(tmp_path / 'archive.json', {'operation': 'archive', 'expected_revision': 1, 'idempotency_key': 'archive'})
    cli('command', cards[0]['task']['id'], '--project', source, '--input', archive)
    assert [card['task']['id'] for card in cli('list', '--project', source)['items']] == [card['task']['id'] for card in cards[1:]]
    assert len(cli('list', '--project', source, '--archived')['items']) == 3
    cli('stop', cards[2]['task']['id'], '--operation', 'cancel', '--expected-revision', 1, '--key', 'cancel-last')
    assert cli('stops') == {'items': [{'task_id': cards[1]['task']['id'], 'revision': 1, 'state': 'planned', 'paused': False}], 'next_cursor': None}


@pytest.mark.parametrize('args', [
    ('list', '--after', '-1'), ('list', '--after', '9223372036854775808'),
    ('list', '--limit', '0'), ('list', '--limit', '101'), ('show', 'not-an-id'),
    ('list', '--actor', 'foreign'), ('list', '--role', 'runtime'),
])
def test_cli_parser_rejects_invalid_cursor_ids_and_authority_options_before_state(tmp_path, args):
    source = tmp_path / 'source'
    source.mkdir()
    result = run_cli(*args, '--project', source)
    assert result.returncode == 2 and not result.stdout and 'error:' in result.stderr
    assert not default_state_path().exists()


@pytest.mark.parametrize('data', [
    {'operation': 'set_state', 'expected_revision': 1, 'idempotency_key': 'forbidden'},
    {'operation': 'resume', 'expected_revision': 0, 'idempotency_key': 'invalid'},
    {'operation': 'resume', 'expected_revision': 1, 'idempotency_key': 'invalid', 'role': 'runtime'},
    {'operation': 'set_metadata', 'expected_revision': 1, 'idempotency_key': 'invalid', 'changes': {'title': float('nan')}},
])
def test_cli_invalid_command_json_cannot_create_state_or_echo_input(tmp_path, data):
    source = tmp_path / 'source'
    source.mkdir()
    path = json_file(tmp_path / 'command.json', data)
    assert cli('command', '0' * 32, '--project', source, '--input', path, code=2) == {'error': {'code': 'invalid_fields'}}
    assert not default_state_path().exists()


def test_cli_invalid_budget_and_child_budget_conflict_have_no_side_effects(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    draft = json_file(tmp_path / 'draft.json', {'title': 'Draft'})
    budget = json_file(tmp_path / 'budget.json', {'max_tool_calls': 121})
    assert cli('create', '--project', source, '--input', draft, '--key', 'invalid', '--workflow-budget', budget, code=2) == {
        'error': {'code': 'invalid_fields'}}
    assert cli('create', '--project', source, '--input', draft, '--key', 'invalid', '--workflow-budget', budget,
        '--workflow', '0' * 32, code=2) == {'error': {'code': 'scope'}}
    assert not default_state_path().exists()
