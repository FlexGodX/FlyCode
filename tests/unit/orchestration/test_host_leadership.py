"""Exact host ownership precedes recovery; diagnostic leases never grant takeover."""

import json
import os
import select
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import test_store
from test_store import queued, request

setup = test_store.setup


@pytest.fixture(autouse=True)
def isolated_host_authority(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'host-authority'))

SCRIPT = '''
import json, sys
from pathlib import Path
try:
    from flycode.orchestration.host import Host
    with Host(Path(sys.argv[1]), Path(sys.argv[2])) as host:
        if len(sys.argv) > 3:
            from flycode.llm.catalogue import ModelCatalogue
            from flycode.domain.orchestration import AccessScope, ClaimRequest
            payload = json.loads(sys.argv[3])
            store = host.connect(catalogue=ModelCatalogue(payload['url'], database=host.database), clock=lambda: 1000.)
            try:
                result = store.claim(AccessScope.model_validate(payload['scope']), ClaimRequest.model_validate(payload['claim']))
                assert result.attempt is not None, result.reasons
            finally:
                store.close()
        print(json.dumps({'session': host.session.model_dump(mode='json')}), flush=True)
        sys.stdin.readline()
except Exception as error:
    print(json.dumps({'error': getattr(error, 'code', type(error).__name__)}), flush=True)
    sys.exit(2)
'''


def environment():
    import flycode

    return {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)}


@contextmanager
def owned_host(database, root, payload=None):
    args = [sys.executable, '-c', SCRIPT, str(database), str(root)]
    if payload is not None:
        args.append(json.dumps(payload))
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=environment())
    try:
        assert select.select([process.stdout], [], [], 20)[0], 'Host did not report startup'
        message = json.loads(process.stdout.readline())
        assert 'session' in message, message
        yield process, message['session']
    finally:
        if process.poll() is None:
            process.stdin.write('stop\n')
            process.stdin.flush()
        try:
            _stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout, stderr = process.communicate(timeout=15)
        assert not stderr, stderr


def contender(database, root):
    completed = subprocess.run([sys.executable, '-c', SCRIPT, str(database), str(root)],
        input='stop\n', capture_output=True, text=True, timeout=20, env=environment(), check=False)
    assert not completed.stderr, completed.stderr
    return json.loads(completed.stdout)


def snapshot(store):
    tables = ('tasks', 'attempts', 'instances', 'reservations', 'workflows', 'events', 'model_calls', 'tool_calls', 'workers')
    return {table: [tuple(row) for row in store.db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
        for table in tables}


def test_live_exact_owner_blocks_alias_takeover_and_bare_recovery(setup, tmp_path, model_server):
    from flycode.domain.orchestration import AgentProfileDraft
    from flycode.llm.catalogue import ModelCatalogue
    from flycode.orchestration.store import StoreError

    store, owner, runtime, template, _, _, _, _ = setup
    catalogue = ModelCatalogue(model_server.url, database=store.path)
    descriptor = catalogue.discover()[0]
    catalogue.probe(descriptor.model_ref, 'primary')
    store.catalogue = catalogue
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **template.model_dump(include=set(AgentProfileDraft.model_fields)), 'context_limit': 4096}))
    instance = store.register_instance(runtime, profile.id)
    workflow, task = queued(setup, allowed_profile_ids=(profile.id,))
    payload = {'url': model_server.url, 'scope': runtime.model_dump(mode='json'),
        'claim': request(instance, task).model_dump(mode='json')}
    root = tmp_path / 'host-state'
    alias = tmp_path / 'queue-alias.sqlite'
    alias.symlink_to(store.path)
    with owned_host(store.path, root, payload) as (process, session):
        assert process.poll() is None and session['generation'] == 1
        # A stale diagnostic timestamp deliberately cannot release the live lock.
        with store.transaction():
            saved = json.loads(store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0])
            saved['heartbeat_at'] = -100000.
            store.db.execute('UPDATE host_sessions SET data=? WHERE id=1', (json.dumps(saved),))
        before = snapshot(store)
        assert contender(alias, root) == {'error': 'host_busy'}
        with pytest.raises(StoreError, match='host_required'):
            store.recover(runtime)
        assert snapshot(store) == before
        assert store.attempts(owner, workflow.id)[0].state == 'assigned'


def test_first_adoption_refuses_unproven_manual_owner_without_fencing(setup, tmp_path):
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import StoreError

    store, _owner, runtime, _, instance, *_ = setup
    _, task = queued(setup)
    store.claim(runtime, request(instance, task))
    before = snapshot(store)
    with pytest.raises(StoreError, match='host_adoption_busy'), Host(store.path, tmp_path / 'host-state'):
        pytest.fail('Manual owner was adopted without proof')
    assert snapshot(store) == before
    assert store.db.execute('SELECT COUNT(*) FROM host_registry').fetchone()[0] == 0


def test_dead_owned_host_advances_generation_but_recovery_does_not_repeat(setup, tmp_path, model_server):
    from flycode.domain.orchestration import AgentProfileDraft
    from flycode.llm.catalogue import ModelCatalogue
    from flycode.orchestration.host import Host

    store, owner, runtime, template, _, _, _, _ = setup
    catalogue = ModelCatalogue(model_server.url, database=store.path)
    descriptor = catalogue.discover()[0]
    catalogue.probe(descriptor.model_ref, 'primary')
    store.catalogue = catalogue
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **template.model_dump(include=set(AgentProfileDraft.model_fields)), 'context_limit': 4096}))
    instance = store.register_instance(runtime, profile.id)
    workflow, task = queued(setup, allowed_profile_ids=(profile.id,))
    payload = {'url': model_server.url, 'scope': runtime.model_dump(mode='json'),
        'claim': request(instance, task).model_dump(mode='json')}
    root = tmp_path / 'host-state'
    with owned_host(store.path, root, payload) as (process, first):
        before = store.task(owner, task.id)
        process.kill()
        process.wait(timeout=15)
    with Host(store.path, root) as second:
        assert second.session.generation == first['generation'] + 1
        second.recover()
        after = store.task(owner, task.id)
        assert after.fence == before.fence + 1
        assert store.attempts(owner, workflow.id)[0].state == 'reconciling'
        assert store.db.execute('SELECT uncertain FROM reservations').fetchone()[0] == 1
        assert store.budget(owner, workflow.id).reserved.model_calls == 2
        recovered = snapshot(store)
        second.recover()
        assert snapshot(store) == recovered


@pytest.mark.parametrize('replacement', ['lock', 'database', 'root', 'pin'])
def test_replaced_authority_never_bootstraps_a_second_owner(tmp_path, replacement):
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import Store, StoreError

    database, root = tmp_path / 'queue.sqlite', tmp_path / 'host-state'
    Store(database).close()
    with Host(database, root) as first:
        registration = first.registration
        if replacement == 'lock':
            path = Path(registration.lock_path)
            path.rename(path.with_suffix('.old'))
            path.write_bytes(b'')
        elif replacement == 'database':
            database.rename(database.with_suffix('.old'))
            database.write_bytes(b'')
        elif replacement == 'root':
            path = Path(registration.runtime_root.path)
            path.rename(path.with_name(path.name + '-old'))
            path.mkdir()
        else:
            Path(first.pin_path).unlink()
        before = database.read_bytes()
        with pytest.raises(StoreError, match='host_identity|host_ownership_unknown'), Host(database, root):
            pytest.fail('Changed host authority accepted')
        with pytest.raises(StoreError, match='host_identity|host_ownership_unknown'):
            first.check()
        assert database.read_bytes() == before


def test_hardlinked_database_is_refused_before_sqlite_wal_open(tmp_path):
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import Store, StoreError

    database = tmp_path / 'queue.sqlite'
    Store(database).close()
    linked = tmp_path / 'linked.sqlite'
    os.link(database, linked)
    before = database.read_bytes()
    with pytest.raises(StoreError, match='host_database_link'), Host(linked, tmp_path / 'host-state'):
        pytest.fail('Hard-linked SQLite accepted')
    assert database.read_bytes() == before
    assert not Path(str(linked) + '-wal').exists()


def test_closed_session_cannot_recover_or_claim_with_stale_authority(setup, tmp_path):
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import StoreError

    store, _, runtime, _, instance, *_ = setup
    _, task = queued(setup)
    with Host(store.path, tmp_path / 'host-state') as first:
        managed = first.connect(catalogue=store.catalogue, clock=store.clock)
    try:
        before = snapshot(store)
        with pytest.raises(StoreError, match='host_closed|host_fence'):
            managed.recover(runtime)
        with pytest.raises(StoreError, match='host_closed|host_fence'):
            managed.claim(runtime, request(instance, task))
        assert snapshot(store) == before
    finally:
        managed.close()


@pytest.mark.parametrize('operation', ['expire', 'dispatch', 'start', 'worker', 'review', 'integration', 'model', 'tool',
    'heartbeat', 'worker-reconcile', 'review-reconcile', 'integration-reconcile'])
def test_bare_runtime_cannot_bypass_managed_admission(setup, tmp_path, operation):
    from flycode.domain import ToolAction
    from flycode.orchestration.dispatch import Dispatcher
    from flycode.orchestration.host import Host
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.runtime import Runtime
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    store, _, runtime, _, instance, *_ = setup
    _, task = queued(setup)
    root = tmp_path / 'runtime'
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'sample.py').write_text('value = 1\n')
    with Host(store.path, tmp_path / 'host-state') as leader:
        managed = leader.connect(catalogue=store.catalogue, clock=store.clock)
        try:
            attempt = managed.claim(runtime, request(instance, task)).attempt
            managed.confirm_started(runtime, attempt.id, attempt.fence)
            before = snapshot(store)
            calls = {
                'heartbeat': lambda: store.heartbeat(runtime, attempt.id, attempt.fence),
                'worker-reconcile': lambda: Runtime(store, root).reconcile(runtime, attempt.id),
                'review-reconcile': lambda: Reviews(store, root).reconcile(runtime, attempt.id),
                'integration-reconcile': lambda: Integration(store, root).reconcile(runtime, 'missing'),
                'expire': lambda: store.expire(runtime),
                'dispatch': lambda: Dispatcher(store, root).tick(runtime, key='bypass'),
                'start': lambda: store.confirm_started(runtime, attempt.id, attempt.fence),
                'worker': lambda: Runtime(store, root).launch(runtime, attempt.id, attempt.fence, source),
                'review': lambda: Reviews(store, root).run(runtime, attempt.id, attempt.fence, 'missing', approval_hash='missing'),
                'integration': lambda: Integration(store, root).run(runtime, attempt.id, attempt.fence, (), (), approval_hashes={}),
                'model': lambda: Resources(store).begin(runtime, attempt.id, attempt.fence, key='bypass'),
                'tool': lambda: Tools(store).begin(runtime, attempt.id, attempt.fence, root=source,
                    action=ToolAction(id='read', tool='read_file', arguments={'path': 'sample.py'})),
            }
            returned = None
            try:
                with pytest.raises(StoreError, match='host_required'):
                    returned = calls[operation]()
                assert snapshot(store) == before
            finally:
                # An unexpected successful worker launch must not outlive RED.
                if operation == 'worker' and returned is not None:
                    returned.close()
                    assert returned.finished.wait(20)
        finally:
            managed.close()


def test_initializing_lock_is_busy_before_registration_is_published(tmp_path):
    import hashlib

    from flycode.orchestration.store import Store
    from flycode.verification.custody import locked

    database, root = tmp_path / 'queue.sqlite', tmp_path / 'host-state'
    Store(database).close()
    directory = root / hashlib.sha256(str(database.resolve()).encode()).hexdigest()
    directory.mkdir(parents=True)
    with locked(directory / 'coordinator.lock', create=True):
        assert contender(database, root) == {'error': 'host_busy'}
    assert contender(database, root) == {'error': 'host_ownership_unknown'}


@pytest.mark.parametrize('authority_loss', [False, True])
def test_releasing_host_fences_work_before_retaining_actual_late_http_usage(setup, tmp_path, model_server, authority_loss):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from openai import OpenAI

    from flycode.orchestration.host import Host
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _, instance, *_ = setup
    workflow, task = queued(setup)
    entered, release = Event(), Event()
    def held(request):
        entered.set()
        assert release.wait(15)
        return model_server.completion('[]')
    model_server.respond = held
    calls_before = len(model_server.requests)
    with Host(store.path, tmp_path / 'host-state') as leader:
        managed = leader.connect(catalogue=store.catalogue, clock=store.clock)
        try:
            attempt = managed.claim(runtime, request(instance, task)).attempt
            managed.confirm_started(runtime, attempt.id, attempt.fence)
        finally:
            managed.close()
        def actual_request():
            connection = leader.connect(catalogue=store.catalogue, clock=store.clock)
            try:
                with OpenAI(base_url=model_server.url, api_key='test', max_retries=0, timeout=12) as client:
                    return Resources(connection).execute(runtime, attempt.id, attempt.fence, key='actual-held-http',
                        request=lambda: client.chat.completions.create(model=attempt.profile_snapshot.model_id,
                            messages=[{'role': 'user', 'content': 'Inspect the source without changing it.'}]))
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(actual_request)
            try:
                assert entered.wait(10)
                if authority_loss:
                    path = Path(leader.registration.lock_path)
                    path.rename(path.with_suffix('.old'))
                    path.write_bytes(b'')
                leader.close()
                release.set()
                with pytest.raises(StoreError, match='fence'):
                    future.result(timeout=20)
            finally:
                release.set()
        calls = Resources(store).calls(runtime, workflow.id)
        assert len(calls) == 1 and calls[0].status == 'completed' and calls[0].late
        assert calls[0].usage.total_tokens == 11
        assert len(model_server.requests) == calls_before + 1
        assert store.attempts(owner, workflow.id)[0].state == ('running' if authority_loss else 'reconciling')
        assert store.budget(owner, workflow.id).spent.model_calls == 1
        assert store.budget(owner, workflow.id).reserved.model_calls == 1
        assert not store.attempts(owner, workflow.id)[0].stop_confirmed


@pytest.mark.parametrize('lose_before_publish', [False, 'entry', 'prepared'])
def test_managed_real_worker_uses_current_host_in_its_actual_rpc_and_control_threads(
        setup, tmp_path, model_server, monkeypatch, lose_before_publish):
    from test_dispatcher import source_tree

    from flycode.domain.orchestration import AgentProfileDraft
    from flycode.orchestration import artifacts as artifact_module
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.host import Host
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.runtime import Runtime
    from flycode.orchestration.tools import Tools

    store, owner, runtime, template, *_ = setup
    import time

    store.clock = time.time
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **template.model_dump(include=set(AgentProfileDraft.model_fields)), 'context_limit': 8192}))
    instance = store.register_instance(runtime, profile.id)
    workflow, task = queued(setup, allowed_profile_ids=(profile.id,))
    source = source_tree(tmp_path)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'src/parser.py'}}]}))
    with Host(store.path, tmp_path / 'host-state') as leader:
        published = []
        reserved = []
        publish = Artifacts.publish
        def lose_authority_at_publication(artifacts, *args, **kwargs):
            reserved.append(artifacts.store.budget(owner, workflow.id).reserved)
            if lose_before_publish == 'entry':
                replace_host_lock(leader)
            published.append(True)
            return publish(artifacts, *args, **kwargs)
        monkeypatch.setattr(Artifacts, 'publish', lose_authority_at_publication)
        write = artifact_module.atomic_json
        def lose_after_preparation(path, content):
            result = write(path, content)
            if lose_before_publish == 'prepared':
                replace_host_lock(leader)
            return result
        monkeypatch.setattr(artifact_module, 'atomic_json', lose_after_preparation)
        managed = leader.connect(catalogue=store.catalogue, clock=store.clock)
        handle = None
        try:
            attempt = managed.claim(runtime, request(instance, task)).attempt
            service = Runtime(managed, Path(leader.registration.runtime_root.path))
            handle = service.launch(runtime, attempt.id, attempt.fence, source, brain='heuristic_fsm')
            assert handle.finished.wait(20), handle.error
            record = service.worker(owner, attempt.id)
            assert record.stop_confirmed and record.result_hash, record.error
            assert published
            if lose_before_publish:
                assert store._attempt(attempt.id).state == 'reconciling'
                assert not store.db.execute('SELECT 1 FROM artifacts WHERE attempt_id=?', (attempt.id,)).fetchone()
                assert store.budget(owner, workflow.id).reserved == reserved[0]
                workspace = service.workspaces.get(owner, attempt.id)
                assert (service.workspaces.root / workspace.id / 'run-result.json').is_file()
            else:
                assert store._attempt(attempt.id).state == 'awaiting_review'
            assert any(call.status == 'completed' for call in Resources(store).calls(owner, workflow.id))
            assert any(call.tool == 'read_file' and call.status == 'completed' for call in Tools(store).calls(owner, workflow.id))
            assert (source / 'src/parser.py').read_text() == 'value = 0\n'
            assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff original bytes\r\n'
        finally:
            if handle is not None:
                handle.close()
            managed.close()


def test_host_cannot_authorize_a_connection_to_a_copied_database(tmp_path):
    import sqlite3

    from flycode.orchestration.host import Host
    from flycode.orchestration.store import Store, StoreError

    original, copied = tmp_path / 'queue.sqlite', tmp_path / 'copy.sqlite'
    Store(original).close()
    with Host(original, tmp_path / 'host-state') as leader:
        connection = leader.connect()
        destination = sqlite3.connect(copied)
        try:
            connection.db.backup(destination)
        finally:
            destination.close()
            connection.close()
        before = copied.read_bytes()
        with pytest.raises(StoreError, match='host_identity'):
            unexpected = Store(copied, host=leader)
            unexpected.close()
        assert copied.read_bytes() == before
        assert not Path(str(copied) + '-wal').exists()


def test_host_context_is_single_use_and_cannot_refresh_old_connection_authority(tmp_path):
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import Store, StoreError

    database = tmp_path / 'queue.sqlite'
    Store(database).close()
    leader = Host(database, tmp_path / 'host-state')
    with leader:
        old = leader.connect()
    try:
        with pytest.raises(StoreError, match='host_reused'), leader:
            pytest.fail('Old connections silently acquired a new session')
        with Host(database, tmp_path / 'host-state'), pytest.raises(StoreError, match='host_closed|host_fence'):
            old._host_guard()
    finally:
        old.close()


def test_empty_replacement_cannot_choose_a_different_runtime_root_to_avoid_path_pin(tmp_path):
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import Store, StoreError

    database = tmp_path / 'queue.sqlite'
    Store(database).close()
    with Host(database, tmp_path / 'first-runtime'):
        database.rename(database.with_suffix('.old'))
        database.write_bytes(b'')
        with pytest.raises(StoreError, match='host_identity|host_ownership_unknown'), Host(database, tmp_path / 'second-runtime'):
            pytest.fail('Runtime root selection bypassed the existing path pin')
        assert database.read_bytes() == b''


def test_close_cannot_leave_an_admission_between_recovery_and_session_revocation(setup, tmp_path, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from flycode.orchestration.host import Host
    from flycode.orchestration.store import Store, StoreError

    store, owner, runtime, _, instance, *_ = setup
    workflow, task = queued(setup)
    entered, release = Event(), Event()
    recover = Store.recover
    def gated_recover(connection, scope):
        result = recover(connection, scope)
        entered.set()
        assert release.wait(10)
        return result
    with Host(store.path, tmp_path / 'host-state') as leader:
        managed = leader.connect(catalogue=store.catalogue, clock=store.clock)
        monkeypatch.setattr(Store, 'recover', gated_recover)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(leader.close)
                try:
                    deadline = time.monotonic() + 10
                    while not entered.is_set() and not future.done():
                        assert time.monotonic() < deadline
                        time.sleep(.005)
                    if entered.is_set():
                        # The real old recovery has committed, with no SQL lock
                        # held by this gate. A separate connection can now admit.
                        assert managed.claim(runtime, request(instance, task)).attempt is not None
                    else:
                        with pytest.raises(StoreError, match='host_closed|host_fence'):
                            managed.claim(runtime, request(instance, task))
                finally:
                    release.set()
                    future.result(timeout=15)
            assert all(attempt.state not in {'assigned', 'running', 'stopping'}
                for attempt in store.attempts(owner, workflow.id))
        finally:
            release.set()
            managed.close()


@pytest.fixture
def managed_artifact(setup, tmp_path, model_server):
    import shutil
    import time

    from test_result_delivery import checks

    from flycode.domain.orchestration import AgentProfileDraft, BudgetAmount
    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.host import Host
    from flycode.orchestration.runtime import Runtime
    from flycode.verification import VerificationEngine

    store, owner, runtime, template, *_ = setup
    store.clock = time.time
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **template.model_dump(include=set(AgentProfileDraft.model_fields)), 'context_limit': 8192}))
    instance = store.register_instance(runtime, profile.id)
    contract = AcceptanceContract(id='repair', version=1, criteria=[AcceptanceCriterion(id='result',
        check_ids=['regression'], expected_case_ids=['tests/test_example.py::test_example'])])
    workflow, task = queued(setup, acceptance=contract, budget=BudgetAmount(model_calls=2, tool_calls=3),
        allowed_profile_ids=(profile.id,))
    source = tmp_path / 'source'
    (source / 'src').mkdir(parents=True)
    (source / 'tests').mkdir()
    (source / 'src/example.py').write_text('answer = 0\n')
    (source / 'tests/test_example.py').write_text('import runpy\ndef test_example():\n'
        '    assert runpy.run_path("src/example.py")["answer"] == 1\n')
    (source / 'user-dirty.bin').write_bytes(b'\x00\xff user bytes\r\n')
    oracle = tmp_path / 'oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'edit_file', 'arguments': {'path': 'src/example.py', 'old_text': '0', 'new_text': '1'}}]}))
    with Host(store.path, tmp_path / 'host-state') as leader:
        managed = leader.connect(catalogue=store.catalogue, clock=store.clock)
        try:
            claim = managed.claim(runtime, request(instance, task).model_copy(update={'reserve': task.budget}))
            assert claim.attempt is not None, claim.reasons
            attempt = claim.attempt
            service = Runtime(managed, Path(leader.registration.runtime_root.path))
            handle = service.launch(runtime, attempt.id, attempt.fence, source, brain='heuristic_fsm')
            try:
                assert handle.finished.wait(20), handle.error
                assert handle.error is None and service.worker(owner, attempt.id).stop_confirmed
            finally:
                handle.close()
            artifact = Artifacts(managed, service.root).for_attempt(owner, attempt.id)
            approval = VerificationEngine(service.root / 'verification').oracles.approve(task.acceptance, checks(), oracle)
            frame = (managed, owner, runtime, workflow, task, attempt, source)
            yield leader, frame, service, artifact, approval
        finally:
            managed.close()


def replace_host_lock(leader):
    path = Path(leader.registration.lock_path)
    path.rename(path.with_suffix('.old'))
    path.write_bytes(b'')


def test_registered_check_cannot_start_after_host_lock_replacement(managed_artifact, monkeypatch):
    from test_result_delivery import reviewer

    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    leader, frame, service, artifact, approval = managed_artifact
    store, owner, runtime, workflow, _, _, source = frame
    checker = reviewer(frame, [artifact])
    before = store.budget(owner, workflow.id).spent
    begin = Tools.begin_check
    changed = []
    def replace_before_admission(tools, *args, **kwargs):
        if not changed:
            replace_host_lock(leader)
            changed.append(True)
        return begin(tools, *args, **kwargs)
    monkeypatch.setattr(Tools, 'begin_check', replace_before_admission)
    try:
        Reviews(store, service.root).run(runtime, checker.id, checker.fence, artifact.id, approval_hash=approval)
    except StoreError:
        pass
    assert changed
    assert not [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
    assert store.budget(owner, workflow.id).spent == before
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_integrator_rechecks_host_before_actual_source_rename(managed_artifact, monkeypatch):
    from test_result_delivery import reviewer

    from flycode.orchestration.application import Application
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    leader, frame, service, artifact, approval = managed_artifact
    store, owner, runtime, _, task, _, source = frame
    checker = reviewer(frame, [artifact])
    review = Reviews(store, service.root).run(runtime, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'approved'
    integrator = reviewer(frame, [artifact])
    apply, move = Application.apply, Application._move
    injected, observed_source_bytes = [], []
    def replace_before_apply(application, *args, **kwargs):
        replace_host_lock(leader)
        injected.append(True)
        return apply(application, *args, **kwargs)
    def observe_actual_move(application, *args, **kwargs):
        outcome = move(application, *args, **kwargs)
        observed_source_bytes.append((source / 'src/example.py').read_bytes())
        return outcome
    monkeypatch.setattr(Application, 'apply', replace_before_apply)
    monkeypatch.setattr(Application, '_move', observe_actual_move)
    try:
        Integration(store, service.root).run(runtime, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval})
    except StoreError:
        pass
    assert injected
    assert all(content == b'answer = 0\n' for content in observed_source_bytes), observed_source_bytes
    assert (source / 'src/example.py').read_bytes() == b'answer = 0\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert store.task(owner, task.id).state != 'succeeded'


def test_review_retains_real_evidence_but_cannot_approve_after_host_authority_loss(managed_artifact, monkeypatch):
    from test_result_delivery import reviewer

    from flycode.orchestration.review import Reviews
    from flycode.verification import VerificationEngine

    leader, frame, service, artifact, approval = managed_artifact
    store, owner, runtime, _, _, _, _ = frame
    checker = reviewer(frame, [artifact])
    verify = VerificationEngine.verify_plan
    def lose_after_real_checks(engine, *args, **kwargs):
        verdict = verify(engine, *args, **kwargs)
        assert verdict.status == 'passed'
        replace_host_lock(leader)
        return verdict
    monkeypatch.setattr(VerificationEngine, 'verify_plan', lose_after_real_checks)
    record = Reviews(store, service.root).run(runtime, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert record.verdict.evidence_id
    assert record.status == 'inconclusive'
    assert store.task(owner, checker.task_id).state != 'succeeded'


def test_registered_process_rechecks_host_at_final_go_boundary(managed_artifact, monkeypatch):
    from test_result_delivery import reviewer

    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools
    from flycode.verification.custody import stopped

    leader, frame, service, artifact, approval = managed_artifact
    store, owner, runtime, workflow, _, _, _ = frame
    checker = reviewer(frame, [artifact])
    bind = Tools.bind_process
    references = []
    def lose_before_go(tools, scope, identity, ref):
        references.append(ref)
        replace_host_lock(leader)
        return bind(tools, scope, identity, ref)
    monkeypatch.setattr(Tools, 'bind_process', lose_before_go)
    try:
        Reviews(store, service.root).run(runtime, checker.id, checker.fence, artifact.id, approval_hash=approval)
    except StoreError:
        pass
    assert len(references) == 1
    with stopped(references[0]) as receipt:
        assert not receipt.started, receipt
    calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
    assert len(calls) == 1 and calls[0].process is None
    assert calls[0].status == 'failed' and calls[0].late
    assert store.task(owner, checker.task_id).state != 'succeeded'


def test_close_during_physical_check_preserves_exact_late_http_accounting(setup, tmp_path, model_server, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, get_ident

    from openai import OpenAI

    from flycode.orchestration.host import Host
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _, instance, *_ = setup
    workflow, task = queued(setup)
    inspected, release, replied = Event(), Event(), Event()
    thread = []
    model_server.respond = lambda request: model_server.completion('[]')
    before = len(model_server.requests)
    with Host(store.path, tmp_path / 'host-state') as leader:
        managed = leader.connect(catalogue=store.catalogue, clock=store.clock)
        try:
            attempt = managed.claim(runtime, request(instance, task)).attempt
            managed.confirm_started(runtime, attempt.id, attempt.fence)
        finally:
            managed.close()
        physical = leader._physical
        def gate_finishing_check(registration, pin):
            physical(registration, pin)
            if thread and get_ident() == thread[0] and replied.is_set():
                inspected.set()
                assert release.wait(15)
        monkeypatch.setattr(leader, '_physical', gate_finishing_check)
        def actual_request():
            thread.append(get_ident())
            connection = leader.connect(catalogue=store.catalogue, clock=store.clock)
            try:
                with OpenAI(base_url=model_server.url, api_key='test', max_retries=0, timeout=12) as client:
                    def transport():
                        response = client.chat.completions.create(model=attempt.profile_snapshot.model_id,
                            messages=[{'role': 'user', 'content': 'Inspect the source without changing it.'}])
                        replied.set()
                        return response
                    return Resources(connection).execute(runtime, attempt.id, attempt.fence,
                        key='close-during-ack', request=transport)
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(actual_request)
            try:
                assert inspected.wait(10)
                leader.close()
                release.set()
                with pytest.raises(StoreError, match='fence'):
                    future.result(timeout=20)
            finally:
                release.set()
        calls = Resources(store).calls(owner, workflow.id)
        assert len(calls) == 1 and calls[0].status == 'completed' and calls[0].late
        assert calls[0].usage.total_tokens == 11
        assert len(model_server.requests) == before + 1
        assert store.budget(owner, workflow.id).spent.model_calls == 1
        assert store.budget(owner, workflow.id).reserved.model_calls == 1


@pytest.mark.parametrize('damage', ['missing', 'foreign'])
def test_initialized_host_cannot_recreate_missing_or_foreign_session(tmp_path, damage):
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import Store

    database, root = tmp_path / 'queue.sqlite', tmp_path / 'host-state'
    Store(database).close()
    with Host(database, root):
        pass
    store = Store(database)
    try:
        if damage == 'missing':
            store.db.execute('DELETE FROM host_sessions')
        else:
            raw = json.loads(store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0])
            raw['registration_id'] = 'different-registration'
            store.db.execute('UPDATE host_sessions SET data=? WHERE id=1', (json.dumps(raw),))
        before = [tuple(row) for row in store.db.execute('SELECT * FROM host_sessions')]
        assert contender(database, root) == {'error': 'host_ownership_unknown'}
        assert [tuple(row) for row in store.db.execute('SELECT * FROM host_sessions')] == before
    finally:
        store.close()


def test_schema18_creation_failure_rolls_back_both_tables_and_keeps_queue(setup, monkeypatch):
    import sqlite3

    from flycode.orchestration.store import Store

    store, *_ = setup
    queued(setup)
    before = snapshot(store)
    store.db.execute('DROP TABLE host_registry')
    store.db.execute('DROP TABLE host_sessions')
    store.db.execute('PRAGMA user_version=17')
    connect = sqlite3.connect
    def deny(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'host_sessions' else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', deny)
        with pytest.raises(sqlite3.DatabaseError):
            Store(store.path)
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == 17
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('host_registry','host_sessions')").fetchone()
    assert snapshot(store) == before
    migrated = Store(store.path)
    try:
        assert migrated.db.execute('PRAGMA user_version').fetchone()[0] == 21
        assert snapshot(migrated) == before
    finally:
        migrated.close()


def test_simultaneous_first_start_has_one_exact_host_and_no_losing_recovery(tmp_path):
    from flycode.orchestration.store import Store

    database, root = tmp_path / 'queue.sqlite', tmp_path / 'host-state'
    store = Store(database)
    before = snapshot(store)
    processes = []
    script = SCRIPT.replace('try:\n', 'sys.stdin.readline()\ntry:\n', 1)
    try:
        for _ in range(2):
            processes.append(subprocess.Popen([sys.executable, '-c', script, str(database), str(root)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment()))
        for process in processes:
            process.stdin.write('start\n')
            process.stdin.flush()
        messages = []
        for process in processes:
            assert select.select([process.stdout], [], [], 20)[0], 'Host race did not report startup'
            messages.append(json.loads(process.stdout.readline()))
        assert sum('session' in message for message in messages) == 1, messages
        assert [message['error'] for message in messages if 'error' in message] == ['host_busy']
        assert next(message['session'] for message in messages if 'session' in message)['generation'] == 1
        assert snapshot(store) == before
    finally:
        for process in processes:
            if process.poll() is None:
                process.stdin.write('stop\n')
                process.stdin.flush()
            try:
                _stdout, stderr = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = process.communicate(timeout=20)
            assert not stderr, stderr
        store.close()


@pytest.mark.parametrize('table', ['host_registry', 'host_sessions'])
@pytest.mark.parametrize('boundary', ['before_validation', 'after_validation'])
def test_malformed_host_metadata_retains_actual_http_result(setup, tmp_path, model_server, monkeypatch, table, boundary):
    from threading import Event

    from openai import OpenAI

    from flycode.orchestration.host import Host
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _, instance, *_ = setup
    workflow, task = queued(setup)
    replied, damaged = Event(), Event()
    model_server.respond = lambda request: model_server.completion('[]')
    before = len(model_server.requests)
    with Host(store.path, tmp_path / 'host-state') as leader:
        managed = leader.connect(catalogue=store.catalogue, clock=store.clock)
        try:
            attempt = managed.claim(runtime, request(instance, task)).attempt
            managed.confirm_started(runtime, attempt.id, attempt.fence)
            validate = leader.validate_sql
            def corrupt_at_acknowledgement(connection):
                if replied.is_set() and not damaged.is_set():
                    assert not connection.db.in_transaction
                    damaged.set()
                    if boundary == 'before_validation':
                        connection.db.execute('UPDATE ' + table + ' SET data=? WHERE id=1', ('{',))
                        return validate(connection)
                    result = validate(connection)
                    connection.db.execute('UPDATE ' + table + ' SET data=? WHERE id=1', ('{',))
                    return result
                return validate(connection)
            monkeypatch.setattr(leader, 'validate_sql', corrupt_at_acknowledgement)
            with OpenAI(base_url=model_server.url, api_key='test', max_retries=0, timeout=12) as client:
                def transport():
                    response = client.chat.completions.create(model=attempt.profile_snapshot.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect the source without changing it.'}])
                    replied.set()
                    return response
                with pytest.raises(StoreError, match='fence'):
                    Resources(managed).execute(runtime, attempt.id, attempt.fence,
                        key='malformed-host-metadata', request=transport)
            assert replied.is_set() and damaged.is_set()
            calls = Resources(managed).calls(owner, workflow.id)
            assert len(calls) == 1 and calls[0].status == 'completed' and calls[0].late
            assert calls[0].usage.total_tokens == 11
            assert len(model_server.requests) == before + 1
            assert managed.budget(owner, workflow.id).spent.model_calls == 1
            assert managed.budget(owner, workflow.id).reserved.model_calls == 1
            with pytest.raises(StoreError, match='host_'):
                managed._host_guard()
        finally:
            managed.close()
