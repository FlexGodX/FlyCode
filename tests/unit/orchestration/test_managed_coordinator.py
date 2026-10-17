"""A physical database host outlives clients and controls actual stage processes."""

import json
import os
import select
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_store import draft

from flycode.domain.orchestration import (
    AccessScope,
    AgentProfileDraft,
    BudgetAmount,
    ExecutionPermission,
    TaskCommand,
    TaskDraft,
    WorkflowBudget,
    WorkflowDraft,
)
from flycode.domain.verification import CheckSpec
from flycode.llm.catalogue import ModelCatalogue
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.host import Host
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store
from flycode.orchestration.tools import Tools
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def managed_pipeline(tmp_path, model_server, monkeypatch, request):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    store = Store(tmp_path / 'queue.sqlite')
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'src/parser.py'}}]}))
    catalogue = ModelCatalogue(model_server.url, database=store.path)
    model = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    assert model.status == 'ready' and model.roles == ['primary']
    store.catalogue = catalogue
    source = tmp_path / 'source'
    (source / 'src').mkdir(parents=True)
    (source / 'tests').mkdir()
    (source / 'src/parser.py').write_text('value = 0\n')
    (source / 'tests/test_parser.py').write_text('import runpy\ndef test_parse():\n'
        '    assert runpy.run_path("src/parser.py")["value"] == 1\n')
    (source / 'dirty.bin').write_bytes(b'\x00\xff keep owner bytes\r\n')
    project = Projects(store).register(source, actor_id='owner')
    owner = AccessScope(actor_id='owner', project_ids={project.id}, role='owner', global_queue_control=True)
    runtime = AccessScope(actor_id='runtime', project_ids={project.id}, role='runtime')
    permission = ExecutionPermission(approved=True, read_scopes=('.',), write_scopes=('src',),
        tools=('read_file', 'edit_file', 'run_tests'))
    developer = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model.model_ref,
        instructions='Repair only approved source files.', capabilities=('python',), project_ids=(project.id,),
        permissions=permission, context_limit=8192))
    store.register_instance(runtime, developer.id)
    checks_permission = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))
    checker = store.register_profile(owner, AgentProfileDraft(role='reviewer', model_ref=model.model_ref,
        instructions='Independently verify exact output.', project_ids=(project.id,), permissions=checks_permission,
        context_limit=8192))
    store.register_instance(runtime, checker.id)
    root_draft = draft(permission, budget=BudgetAmount(model_calls=2, tool_calls=3))
    workflow = store.create_workflow(owner, WorkflowDraft(project_id=project.id, task=root_draft,
        budget=WorkflowBudget()), key='root')
    root = store.task(owner, workflow.root_task_id)
    writer = store.create_task(owner, workflow.id, root_draft.model_copy(update={
        'parent_id': root.id, 'allowed_profile_ids': (developer.id,)}), key='writer')
    reviewer = store.create_task(owner, workflow.id, TaskDraft(title='Review output', kind='review',
        objective='Independently verify the exact writer output.', parent_id=root.id, acceptance=root.acceptance,
        permissions=checks_permission, budget=BudgetAmount(tool_calls=4), allowed_profile_ids=(checker.id,)), key='review')
    integrator = store.create_task(owner, workflow.id, TaskDraft(title='Integrate output', kind='review',
        objective='Apply and verify the exact reviewed output.', parent_id=root.id, dependencies=(reviewer.id,),
        acceptance=root.acceptance, permissions=checks_permission, budget=BudgetAmount(tool_calls=25),
        allowed_profile_ids=(checker.id,)), key='integrate')
    host_root = tmp_path / 'host-state'
    with Host(store.path, host_root) as bootstrap:
        runtime_root = Path(bootstrap.registration.runtime_root.path)
    dispatcher = Dispatcher(store, runtime_root)
    dispatcher.bind(owner, writer.id, source, expected_revision=writer.revision, key='bind', brain='heuristic_fsm')
    oracle = tmp_path / 'oracle'
    (oracle / 'tests').mkdir(parents=True)
    (oracle / 'tests/test_parser.py').write_bytes((source / 'tests/test_parser.py').read_bytes())
    if getattr(request, 'param', None) == 'slow-review':
        marker = tmp_path / 'review-started'
        (oracle / 'tests/test_parser.py').write_text('import pathlib, runpy, time\n'
            'def test_parse():\n'
            '    value = runpy.run_path("src/parser.py")["value"]\n'
            '    if value == 1:\n'
            f'        pathlib.Path({str(marker)!r}).write_text("started")\n'
            '        time.sleep(60)\n'
            '    assert value == 1\n')
    elif getattr(request, 'param', None) == 'slow-integration':
        marker, counter = tmp_path / 'integration-started', tmp_path / 'successful-checks'
        (oracle / 'tests/test_parser.py').write_text('import pathlib, runpy, time\n'
            'def test_parse():\n'
            '    value = runpy.run_path("src/parser.py")["value"]\n'
            '    if value == 1:\n'
            f'        counter = pathlib.Path({str(counter)!r})\n'
            '        count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
            '        counter.write_text(str(count))\n'
            '        if count >= 4:\n'
            f'            pathlib.Path({str(marker)!r}).write_text("started")\n'
            '            time.sleep(60)\n'
            '    assert value == 1\n')
    checks = [CheckSpec(id=stage, stage=stage, expected_outcome='failed' if stage == 'baseline' else 'passed',
        targets=['tests/test_parser.py::test_parse'], dependencies=[] if previous is None else [previous])
        for stage, previous in [('baseline', None), ('target', 'baseline'), ('regression', 'target'), ('integration', 'regression')]]
    approval = VerificationEngine(runtime_root / 'verification').oracles.approve(root.acceptance, checks, oracle)
    tasks = (root, writer, reviewer, integrator)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
    def approve():
        return Pipelines(store, runtime_root).approve(owner, workflow.id, writer.id, reviewer.id, integrator.id,
            source=source, expected_revisions={item.id: item.revision for item in tasks},
            approval_hashes={item.id: approval for item in tasks}, key='approve')
    try:
        yield store, owner, runtime, workflow, tasks, source, host_root, runtime_root, oracle, checks, approve
    finally:
        store.close()


@contextmanager
def running_coordinator(database, host_root, endpoint):
    import flycode

    environment = {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent),
        'LMSTUDIO_BASE_URL': endpoint}
    process = subprocess.Popen([sys.executable, '-m', 'flycode.orchestration.coordinator',
        '--database', str(database), '--state-root', str(host_root)], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    entered = False
    try:
        assert select.select([process.stdout], [], [], 15)[0], 'Coordinator startup did not report readiness'
        line = process.stdout.readline()
        if not line:
            _stdout, stderr = process.communicate(timeout=10)
            pytest.fail('Coordinator startup failed: ' + stderr)
        status = json.loads(line)
        assert 'session' in status, status
        entered = True
        yield process, status['session']
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            _stdout, stderr = process.communicate(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout, stderr = process.communicate(timeout=15)
        if entered:
            assert not stderr, stderr


def until(predicate, *, timeout=25):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'Coordinator did not reach the expected canonical state'
        time.sleep(.02)


def test_native_pipeline_survives_client_close_and_has_no_idle_command_flood(managed_pipeline, model_server):
    store, owner, _, workflow, tasks, source, host_root, runtime_root, _, _, approve = managed_pipeline
    approve()
    client = Store(store.path)
    with running_coordinator(store.path, host_root, model_server.url) as (process, session):
        client.close()
        until(lambda: store.workflow(owner, workflow.id).state == 'succeeded', timeout=40)
        assert process.poll() is None
        assert json.loads(store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0])['id'] == session['id']
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == 3 and {item.task_id for item in attempts} == {item.id for item in tasks[1:]}
        assert all(item.state == 'succeeded' for item in store.tasks(owner, workflow.id))
        identity = store.db.execute('SELECT id FROM integrations').fetchone()[0]
        assert Integration(store, runtime_root).current(owner, identity)
        assert store.budget(owner, workflow.id).reserved == BudgetAmount()
        assert (source / 'src/parser.py').read_text() == 'value = 1\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        time.sleep(.7)
        before = [tuple(row) for row in store.db.execute('SELECT * FROM commands')]
        time.sleep(.8)
        assert [tuple(row) for row in store.db.execute('SELECT * FROM commands')] == before


def test_dispatcher_selects_without_launching_native_checks(managed_pipeline):
    store, _, runtime, workflow, _, _, host_root, runtime_root, _, _, approve = managed_pipeline
    approve()
    with Host(store.path, host_root) as leader:
        connection = leader.connect(catalogue=store.catalogue)
        try:
            result = Dispatcher(connection, runtime_root).select(runtime, key='select-only')
            assert result.attempt is not None and result.attempt.state == 'assigned'
            assert store.db.execute('SELECT COUNT(*) FROM workers').fetchone()[0] == 0
            assert store.budget(runtime, workflow.id).spent == BudgetAmount()
        finally:
            connection.close()


@pytest.mark.parametrize('managed_pipeline', ['slow-review'], indirect=True)
def test_control_heartbeats_and_owner_stop_remain_live_during_real_review(managed_pipeline, model_server):
    from flycode.verification.custody import stopped

    store, owner, _, workflow, tasks, source, host_root, _, _, _, approve = managed_pipeline
    approve()
    with running_coordinator(store.path, host_root, model_server.url) as (process, _):
        until(lambda: (source.parent / 'review-started').exists())
        first_pulse = json.loads(store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0])['heartbeat_at']
        until(lambda: json.loads(store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0])['heartbeat_at']
            > first_pulse, timeout=12)
        assert process.poll() is None
        before = store.budget(owner, workflow.id)
        deadline = store.workflow(owner, workflow.id).deadline_at
        current = store.task(owner, tasks[2].id)
        started = time.monotonic()
        store.command(owner, current.id, TaskCommand(operation='pause', expected_revision=current.revision,
            idempotency_key='pause-check'))
        assert time.monotonic() - started < 1
        until(lambda: any(call.status == 'uncertain' for call in Tools(store).calls(owner, workflow.id)), timeout=10)
        call = next(call for call in Tools(store).calls(owner, workflow.id) if call.status == 'uncertain')
        assert call.process is not None
        with stopped(call.process) as receipt:
            assert receipt.started and receipt.stop_confirmed
        assert store._attempt(call.attempt_id).state in {'stopping', 'reconciling'}
        assert store.budget(owner, workflow.id).spent == before.spent
        assert store.budget(owner, workflow.id).reserved.tool_calls > 0
        assert store.workflow(owner, workflow.id).deadline_at == deadline
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'


def test_graceful_shutdown_keeps_kernel_leadership_until_actual_http_returns(managed_pipeline, model_server):
    from threading import Event

    from flycode.orchestration.resources import Resources
    from flycode.orchestration.store import StoreError

    store, owner, _, workflow, _, _, host_root, _, _, _, approve = managed_pipeline
    approve()
    entered, release = Event(), Event()
    respond = model_server.respond
    def hold(request):
        entered.set()
        assert release.wait(20)
        return respond(request)
    model_server.respond = hold
    with running_coordinator(store.path, host_root, model_server.url) as (process, _):
        try:
            assert entered.wait(10)
            process.terminate()
            until(lambda: json.loads(store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0])['phase']
                == 'draining', timeout=10)
            assert process.poll() is None
            with pytest.raises(StoreError, match='host_busy'), Host(store.path, host_root):
                pytest.fail('A second host acquired leadership while the old host still owned HTTP')
        finally:
            release.set()
        until(lambda: process.poll() is not None, timeout=15)
    calls = Resources(store).calls(owner, workflow.id)
    assert len(calls) == 1 and calls[0].status == 'completed' and calls[0].late
    assert calls[0].usage.total_tokens == 11
    assert len(store.attempts(owner, workflow.id)) == 1
    assert store.budget(owner, workflow.id).spent.model_calls == 1
    assert store.budget(owner, workflow.id).reserved.model_calls == 1


def test_restart_confirms_saved_worker_custody_without_source_or_replaying_unknown_http(managed_pipeline, model_server):
    from threading import Event

    from flycode.orchestration.resources import Resources
    from flycode.orchestration.runtime import Runtime

    store, owner, _, workflow, _, source, host_root, runtime_root, _, _, approve = managed_pipeline
    approve()
    entered, release = Event(), Event()
    respond = model_server.respond
    def hold(request):
        entered.set()
        assert release.wait(20)
        return respond(request)
    model_server.respond = hold
    with running_coordinator(store.path, host_root, model_server.url) as (old, previous):
        try:
            assert entered.wait(10)
            attempt = store.attempts(owner, workflow.id)[0]
            old.kill()
            old.wait(timeout=10)
        finally:
            release.set()
    before = store.budget(owner, workflow.id)
    requests = len(model_server.requests)
    source.rename(source.with_name('source-offline'))
    with running_coordinator(store.path, host_root, model_server.url) as (current, session):
        assert session['generation'] == previous['generation'] + 1
        until(lambda: Runtime(store, runtime_root).worker(owner, attempt.id).stop_confirmed, timeout=10)
        assert current.poll() is None
        assert store._attempt(attempt.id).state == 'reconciling'
        assert not store._attempt(attempt.id).stop_confirmed
        assert len(store.attempts(owner, workflow.id)) == 1
        calls = Resources(store).calls(owner, workflow.id)
        assert len(calls) == 1 and calls[0].status == 'uncertain' and calls[0].usage is None
        assert store.budget(owner, workflow.id).spent == before.spent
        assert store.budget(owner, workflow.id).reserved == before.reserved
        assert len(model_server.requests) == requests
        time.sleep(.8)
        commands = [tuple(row) for row in store.db.execute('SELECT * FROM commands')]
        events = [tuple(row) for row in store.db.execute('SELECT * FROM events')]
        time.sleep(.8)
        assert [tuple(row) for row in store.db.execute('SELECT * FROM commands')] == commands
        assert [tuple(row) for row in store.db.execute('SELECT * FROM events')] == events
    assert (source.with_name('source-offline') / 'src/parser.py').read_text() == 'value = 0\n'
    assert (source.with_name('source-offline') / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'


@pytest.mark.parametrize(('stage', 'managed_pipeline'), [('review', 'slow-review'), ('integration', 'slow-integration')],
    indirect=['managed_pipeline'])
def test_restart_reconciles_exact_stage_checks_and_keeps_unknown_effects(managed_pipeline, model_server, stage):
    from flycode.verification.custody import stopped

    store, owner, _, workflow, _, source, host_root, _, _, _, approve = managed_pipeline
    approve()
    table = 'review_executions' if stage == 'review' else 'integrations'
    with running_coordinator(store.path, host_root, model_server.url) as (old, previous):
        until(lambda: (source.parent / (stage + '-started')).exists(), timeout=30)
        record = json.loads(store.db.execute('SELECT data FROM ' + table).fetchone()[0])
        identity = record['attempt_id'] if stage == 'review' else record['id']
        attempt_id = record['attempt_id']
        old.kill()
        old.wait(timeout=10)
    before = store.budget(owner, workflow.id)
    requests = len(model_server.requests)
    source.rename(source.with_name('source-offline'))
    with running_coordinator(store.path, host_root, model_server.url) as (current, session):
        assert session['generation'] == previous['generation'] + 1
        until(lambda: json.loads(store.db.execute('SELECT data FROM ' + table + ' WHERE id=?',
            (identity,)).fetchone()[0])['error'] == 'effects_unknown', timeout=10)
        assert current.poll() is None
        assert store._attempt(attempt_id).state == 'reconciling'
        assert not store._attempt(attempt_id).stop_confirmed
        call = next(call for call in Tools(store).calls(owner, workflow.id)
            if call.attempt_id == attempt_id and call.status == 'uncertain')
        with stopped(call.process) as receipt:
            assert receipt.started and receipt.stop_confirmed
        assert store.budget(owner, workflow.id).spent == before.spent
        assert store.budget(owner, workflow.id).reserved == before.reserved
        assert len(model_server.requests) == requests
        if stage == 'integration':
            assert store.db.execute("SELECT 1 FROM source_claims WHERE state!='released'").fetchone()
    assert (source.with_name('source-offline') / 'src/parser.py').read_text() == 'value = 0\n'


def test_late_actual_http_acknowledgement_during_recovery_is_not_cached_as_processed(managed_pipeline, model_server, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from openai import OpenAI

    from flycode.orchestration.coordinator import Coordinator
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.store import StoreError

    store, owner, runtime, workflow, _, _, host_root, runtime_root, _, _, approve = managed_pipeline
    approve()
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    entered, release, stop, raced = Event(), Event(), Event(), Event()
    respond = model_server.respond
    def hold(request):
        entered.set()
        assert release.wait(20)
        return respond(request)
    model_server.respond = hold
    first = Host(store.path, host_root)
    first.__enter__()
    connection = first.connect(catalogue=store.catalogue)
    try:
        attempt = Dispatcher(connection, runtime_root).select(runtime, key='select-held-transport').attempt
        connection.confirm_started(runtime, attempt.id, attempt.fence)
    finally:
        connection.close()
    def invoke():
        connection = first.connect(catalogue=store.catalogue)
        try:
            with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client:
                return Resources(connection).execute(runtime, attempt.id, attempt.fence, key='held-transport',
                    request=lambda: client.chat.completions.create(model=attempt.profile_snapshot.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect the approved parser without changing it.'}]))
        finally:
            connection.close()
    confirm = Store.confirm_stopped
    with ThreadPoolExecutor(max_workers=2) as pool:
        http = pool.submit(invoke)
        try:
            assert entered.wait(10)
            first.close()
            def acknowledge_after_observation(connection, scope, identity, fence, **kwargs):
                try:
                    return confirm(connection, scope, identity, fence, **kwargs)
                except StoreError as error:
                    if identity == attempt.id and error.code == 'resource_busy' and not raced.is_set():
                        raced.set()
                        release.set()
                        with pytest.raises(StoreError, match='fence'):
                            http.result(timeout=10)
                    raise
            monkeypatch.setattr(Store, 'confirm_stopped', acknowledge_after_observation)
            with Host(store.path, host_root) as current:
                coordinator = pool.submit(Coordinator(current).run, stop.is_set)
                try:
                    assert raced.wait(10)
                    until(lambda: store._attempt(attempt.id).stop_confirmed, timeout=4)
                    assert store._attempt(attempt.id).state == 'failed'
                    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
                    assert store.budget(owner, workflow.id).spent.model_calls == 1
                    calls = Resources(store).calls(owner, workflow.id)
                    assert len(calls) == 1 and calls[0].late and calls[0].usage.total_tokens == 11
                finally:
                    stop.set()
                    coordinator.result(timeout=15)
        finally:
            release.set()
            stop.set()
            first.close()


@pytest.mark.parametrize('managed_pipeline', ['slow-review'], indirect=True)
def test_recovery_retries_unchanged_coordinator_lock_without_duplicate_events(managed_pipeline, model_server):
    from contextlib import ExitStack

    from flycode.verification.custody import locked, stopped

    store, owner, _, workflow, _, source, host_root, runtime_root, _, _, approve = managed_pipeline
    approve()
    with running_coordinator(store.path, host_root, model_server.url) as (old, _):
        until(lambda: (source.parent / 'review-started').exists())
        record = json.loads(store.db.execute('SELECT data FROM review_executions').fetchone()[0])
        old.kill()
        old.wait(timeout=10)
    call = next(call for call in Tools(store).calls(owner, workflow.id)
        if call.attempt_id == record['attempt_id'] and call.process is not None)
    def group_stopped():
        try:
            with stopped(call.process):
                return True
        except (OSError, ValueError):
            return False
    until(group_stopped, timeout=10)
    lock = runtime_root / 'reviews' / record['attempt_id'] / 'coordinator.lock'
    with ExitStack() as locks:
        locks.enter_context(locked(lock, identity=tuple(record['coordinator_identity'])))
        with running_coordinator(store.path, host_root, model_server.url):
            until(lambda: any(event.kind == 'reconciliation_blocked' and event.data['reason'] == 'coordinator_running'
                for event in store.events(owner, workflow.id)), timeout=10)
            time.sleep(.8)
            before = [tuple(row) for row in store.db.execute('SELECT * FROM events')]
            commands = [tuple(row) for row in store.db.execute('SELECT * FROM commands')]
            time.sleep(1.3)
            assert [tuple(row) for row in store.db.execute('SELECT * FROM events')] == before
            assert [tuple(row) for row in store.db.execute('SELECT * FROM commands')] == commands
            locks.close()
            until(lambda: json.loads(store.db.execute('SELECT data FROM review_executions WHERE id=?',
                (record['attempt_id'],)).fetchone()[0])['error'] == 'effects_unknown', timeout=4)
            assert not store._attempt(record['attempt_id']).stop_confirmed


def test_completed_integration_retries_source_settlement_after_exact_lock_release(managed_pipeline, model_server, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack
    from threading import Event

    from flycode.orchestration.coordinator import Coordinator
    from flycode.orchestration.sources import Sources
    from flycode.verification.custody import locked

    store, owner, _, workflow, _, source, host_root, _, _, _, approve = managed_pipeline
    approve()
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    held, stop = Event(), Event()
    settle = Integration._settle_source
    with ExitStack() as locks, Host(store.path, host_root) as host, ThreadPoolExecutor(max_workers=1) as pool:
        def retain_terminal_lock(service, scope, identity):
            record = service.get(scope, identity)
            if record.state == 'completed' and not held.is_set():
                claim = Sources(service.store).get(record.source_claim_id)
                locks.enter_context(locked(Path(claim.coordinator_path), identity=claim.coordinator_identity))
                held.set()
            return settle(service, scope, identity)
        monkeypatch.setattr(Integration, '_settle_source', retain_terminal_lock)
        running = pool.submit(Coordinator(host).run, stop.is_set)
        try:
            assert held.wait(30)
            record = json.loads(store.db.execute('SELECT data FROM integrations').fetchone()[0])
            claim = record['source_claim_id']
            assert store._attempt(record['attempt_id']).stop_confirmed
            assert store.workflow(owner, workflow.id).state == 'succeeded'
            assert Sources(store).get(claim).state == 'held'
            time.sleep(1.5)
            locks.close()
            until(lambda: Sources(store).get(claim).state == 'released', timeout=4)
            assert store.budget(owner, workflow.id).reserved == BudgetAmount()
            assert (source / 'src/parser.py').read_text() == 'value = 1\n'
            assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        finally:
            locks.close()
            stop.set()
            running.result(timeout=15)
