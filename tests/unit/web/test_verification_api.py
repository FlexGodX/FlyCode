"""Verification HTTP commands share registered inputs, ownership and evidence."""

import hashlib
import json
from pathlib import Path

import pytest
from test_canonical_tasks_api import live_session, read_sse, register, session

from flycode.domain.orchestration import AccessScope
from flycode.domain.verification_control import VerificationProfileDraft
from flycode.orchestration.host import Host
from flycode.orchestration.store import Store, default_state_path
from flycode.verification.control import VerificationControl
from flycode.verification.execution import VerificationExecutor
from flycode.verification.registry import VerificationRegistry


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'test_answer.py').write_text('def test_answer(): assert True\n')
    return root


def inputs(project_id):
    store = Store(default_state_path())
    try:
        owner = AccessScope(actor_id='trusted-local', project_ids={project_id}, role='owner')
        registry = VerificationRegistry(store)
        subject = registry.register_subject(owner, project_id)
        profile = registry.register_profile(owner, project_id, VerificationProfileDraft(title='Local pytest'))
        return {'subject_ids': {'target': subject.id}, 'profile_id': profile.id, 'profile_version': 1,
            'budget': {'max_tool_calls': 6, 'max_attempts': 3, 'wall_seconds': 60.}, 'idempotency_key': 'create'}
    finally:
        store.close()


def create(client, body):
    response = client.post('/api/verification/plans', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def command(client, plan, operation, key):
    return client.post('/api/verification/plans/' + plan['id'] + '/commands', json={
        'operation': operation, 'expected_revision': plan['revision'], 'idempotency_key': key})


def test_verification_reads_require_token_and_same_origin(workspace, tmp_path):
    with session(workspace, tmp_path / 'history') as client:
        token = client.headers.pop('X-FlyCode-Token')
        for route in ('/api/verification/catalogue', '/api/verification/plans',
                '/api/verification/plans/' + '0' * 32):
            assert client.get(route).status_code == 403
            assert client.get(route, headers={'X-FlyCode-Token': token,
                'Origin': 'https://foreign.example'}).status_code == 403


def test_registered_http_create_start_replay_cancel_and_rerun_share_store(workspace, tmp_path):
    with session(workspace, tmp_path / 'history') as client:
        project = register(client)
        body = inputs(project)
        catalogue = client.get('/api/verification/catalogue')
        assert catalogue.status_code == 200, catalogue.text
        assert catalogue.json()['subjects'][0]['id'] == body['subject_ids']['target']
        assert catalogue.json()['profiles'][0]['id'] == body['profile_id']
        plan = create(client, body)
        assert plan['state'] == 'draft' and plan['revision'] == 1
        assert create(client, body) == plan
        assert client.get('/api/verification/plans').json()['items'] == [plan]
        started = command(client, plan, 'start', 'start')
        assert started.status_code == 200, started.text
        assert started.json()['state'] == 'queued'
        assert command(client, plan, 'start', 'start').json() == started.json()
        assert command(client, plan, 'start', 'stale').status_code == 409
        current = client.get('/api/verification/plans/' + plan['id']).json()
        assert current['state'] == 'queued' and current['reserved'] == 1 and current['spent'] == 0
        assert command(client, current, 'cancel', 'cancel').status_code == 200
        cancelled = client.get('/api/verification/plans/' + plan['id']).json()
        assert cancelled['state'] == 'cancelled' and cancelled['reserved'] == 0
        assert command(client, cancelled, 'rerun', 'rerun').json()['number'] == 2
        store = Store(default_state_path())
        try:
            assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 2
            for table in ('attempts', 'model_calls', 'tool_calls'):
                assert store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        finally:
            store.close()
        assert '/private/' not in json.dumps(current)


def test_foreign_scope_and_unregistered_arguments_are_rejected(workspace, tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    with session(workspace, tmp_path / 'history') as client, session(other, tmp_path / 'foreign') as foreign:
        body = inputs(register(client))
        plan = create(client, body)
        assert foreign.get('/api/verification/plans').json()['items'] == []
        assert foreign.get('/api/verification/catalogue').json() == {'subjects': [], 'profiles': []}
        for suffix in ('', '/events', '/artifacts/' + '0' * 64):
            assert foreign.get('/api/verification/plans/' + plan['id'] + suffix).status_code == 403
        assert foreign.post('/api/verification/plans', json=body).status_code == 403
        assert command(foreign, plan, 'cancel', 'foreign-cancel').status_code == 403
        for extra in ({'argv': ['sh']}, {'subject_path': '/tmp'}, {'url': 'https://foreign.example'}):
            assert client.post('/api/verification/plans', json={**body, **extra}).status_code == 422
        assert client.get('/api/verification/plans/' + plan['id'] + '/events?after=9999').status_code == 422


@pytest.mark.parametrize('tamper', ['content', 'symlink', 'hardlink', 'fifo'])
def test_real_execution_artifacts_are_scoped_hashed_downloads_and_freshness_is_rechecked(workspace, tmp_path, tamper):
    with session(workspace, tmp_path / 'history') as client:
        project = register(client)
        plan = create(client, inputs(project))
        started = command(client, plan, 'start', 'start').json()
        with Host(default_state_path(), tmp_path / 'host') as host:
            store = host.connect()
            try:
                runtime = AccessScope(actor_id='host', project_ids={project}, role='runtime')
                assigned = VerificationControl(store).claim(runtime, started['id'])
                result = VerificationExecutor(store, assigned.id, assigned.fence).run()
                assert result.current and result.verdict.status == 'passed'
            finally:
                store.close()
        current = client.get('/api/verification/plans/' + plan['id']).json()
        assert current['current'] and current['verdict']['status'] == 'passed'
        assert current['spent'] == 1 and current['reserved'] == 0
        assert len(current['attempts']) == 1
        artifact = next(item for item in current['artifacts'] if item['name'].endswith('junit.xml'))
        assert len(artifact['id']) == 64 and 'path' not in artifact
        route = '/api/verification/plans/' + plan['id'] + '/artifacts/' + artifact['id']
        response = client.get(route)
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('application/octet-stream')
        assert 'attachment' in response.headers['content-disposition']
        assert hashlib.sha256(response.content).hexdigest() == artifact['sha256']
        assert client.get(route[:-64] + '0' * 64).status_code == 404
        (workspace / 'test_answer.py').write_text('def test_answer(): assert False\n')
        changed = client.get('/api/verification/plans/' + plan['id']).json()
        assert not changed['current'] and 'subject_changed' in changed['blockers']
        assert changed['verdict'] == current['verdict']
        bundle = Path(result.coordinator_root.path) / 'evidence' / result.verdict.evidence_id
        report = next(bundle.glob('attempt-*/junit.xml'))
        if tamper == 'content':
            report.write_text('<script>changed report</script>')
        else:
            import os

            victim = tmp_path / 'private-report'
            victim.write_text('Do not disclose this file')
            report.unlink()
            if tamper == 'fifo':
                os.mkfifo(report)
            elif tamper == 'symlink':
                report.symlink_to(victim)
            else:
                os.link(victim, report)
        assert client.get(route).status_code == 409


def test_prior_grant_can_only_cancel_after_root_loss(workspace, tmp_path):
    with session(workspace, tmp_path / 'history') as client:
        plan = create(client, inputs(register(client)))
        command(client, plan, 'start', 'start')
        current = client.get('/api/verification/plans/' + plan['id']).json()
        workspace.rename(tmp_path / 'displaced')
        workspace.mkdir()
        assert client.get('/api/verification/plans/' + plan['id']).status_code == 403
        handles = client.get('/api/verification/stops')
        assert handles.status_code == 200, handles.text
        assert handles.json() == {'items': [{'plan_id': plan['id'], 'revision': current['revision'],
            'state': 'queued'}], 'next_cursor': None}
        response = command(client, current, 'cancel', 'cancel-after-root-loss')
        assert response.status_code == 200 and response.json()['state'] == 'cancelled'
        assert command(client, current, 'cancel', 'cancel-after-root-loss').json() == response.json()
        assert command(client, current, 'rerun', 'denied').status_code == 403


def test_download_accepts_a_real_report_above_two_megabytes_within_runner_limit(workspace, tmp_path):
    (workspace / 'test_answer.py').write_text(
        'def test_answer(record_property):\n'
        "    record_property('details', 'x' * (2 * 1024 * 1024 + 1024))\n")
    with session(workspace, tmp_path / 'history') as client:
        project = register(client)
        plan = create(client, inputs(project))
        started = command(client, plan, 'start', 'start').json()
        with Host(default_state_path(), tmp_path / 'host') as host:
            store = host.connect()
            try:
                assigned = VerificationControl(store).claim(
                    AccessScope(actor_id='host', project_ids={project}, role='runtime'), started['id'])
                result = VerificationExecutor(store, assigned.id, assigned.fence).run()
                assert result.verdict.status == 'passed' and result.current
            finally:
                store.close()
        view = client.get('/api/verification/plans/' + plan['id']).json()
        artifact = next(item for item in view['artifacts'] if item['name'].endswith('junit.xml'))
        response = client.get('/api/verification/plans/' + plan['id'] + '/artifacts/' + artifact['id'])
        assert response.status_code == 200, response.text[:200]
        assert 2 * 1024 * 1024 < len(response.content) < 8 * 1024 * 1024
        assert hashlib.sha256(response.content).hexdigest() == artifact['sha256']


def test_missing_old_manifest_cannot_block_download_of_an_intact_later_report(workspace, tmp_path):
    with session(workspace, tmp_path / 'history') as client:
        project = register(client)
        plan = create(client, inputs(project))
        started = command(client, plan, 'start', 'start').json()
        with Host(default_state_path(), tmp_path / 'host') as host:
            store = host.connect()
            try:
                runtime = AccessScope(actor_id='host', project_ids={project}, role='runtime')
                assigned = VerificationControl(store).claim(runtime, started['id'])
                first = VerificationExecutor(store, assigned.id, assigned.fence).run()
                current = client.get('/api/verification/plans/' + plan['id']).json()
                second = command(client, current, 'rerun', 'rerun').json()
                assigned = VerificationControl(store).claim(runtime, second['id'])
                result = VerificationExecutor(store, assigned.id, assigned.fence).run()
                assert first.current and result.current
            finally:
                store.close()
        view = client.get('/api/verification/plans/' + plan['id']).json()
        artifact = next(item for item in view['artifacts'] if item['execution_id'] == result.id)
        old = Path(first.coordinator_root.path) / 'evidence' / first.verdict.evidence_id / 'manifest.json'
        old.unlink()
        latest = client.get('/api/verification/plans/' + plan['id']).json()
        assert latest['current'] and artifact in latest['artifacts']
        response = client.get('/api/verification/plans/' + plan['id'] + '/artifacts/' + artifact['id'])
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == artifact['sha256']


def test_actual_verification_sse_reconnects_without_duplicate_commands(workspace, tmp_path):
    with live_session(workspace, tmp_path / 'history') as client:
        plan = create(client, inputs(register(client)))
        route = '/api/verification/plans/' + plan['id'] + '/events'
        with client.stream('GET', route) as stream:
            assert stream.status_code == 200
            frames = stream.iter_lines()
            snapshot = read_sse(frames)
            assert snapshot['event'] == 'snapshot' and snapshot['data']['state'] == 'draft'
            started = command(client, plan, 'start', 'start')
            assert started.status_code == 200
            queued = read_sse(frames)
            assert queued['event'] == 'change' and queued['data']['kind'] == 'execution_queued'
            assert queued['id'] > snapshot['id']
        with client.stream('GET', route, headers={'Last-Event-ID': str(snapshot['id'])}) as stream:
            frames = stream.iter_lines()
            restored = read_sse(frames)
            assert restored['event'] == 'snapshot' and restored['id'] == snapshot['id']
            assert restored['data']['replay_after'] == snapshot['id']
            assert read_sse(frames) == queued
            current = client.get('/api/verification/plans/' + plan['id']).json()
            assert command(client, current, 'cancel', 'cancel').status_code == 200
            cancelled = read_sse(frames)
            assert cancelled['id'] > queued['id'] and cancelled['data']['kind'] == 'execution_cancelled'
        store = Store(default_state_path())
        try:
            assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 1
        finally:
            store.close()


def test_sse_snapshot_cannot_advance_past_a_change_missing_from_its_state(workspace, tmp_path, monkeypatch):
    original = VerificationControl.get
    committed = []
    with live_session(workspace, tmp_path / 'history') as client:
        plan = create(client, inputs(register(client)))

        def start_between_reads(control, scope, identity):
            record = original(control, scope, identity)
            if identity == plan['id'] and not committed:
                committed.append(True)
                connection = Store(default_state_path())
                try:
                    from flycode.domain.verification_control import VerificationCommand

                    VerificationControl(connection).command(scope, identity, VerificationCommand(operation='start',
                        expected_revision=1, idempotency_key='concurrent-start'))
                finally:
                    connection.close()
            return record

        monkeypatch.setattr(VerificationControl, 'get', start_between_reads)
        with client.stream('GET', '/api/verification/plans/' + plan['id'] + '/events') as stream:
            frames = stream.iter_lines()
            snapshot = read_sse(frames)
            store = Store(default_state_path())
            try:
                changes = list(store.db.execute("SELECT sequence FROM verification_events WHERE plan_id=? "
                    "AND json_extract(data,'$.kind')='execution_queued'", (plan['id'],)))
                assert len(changes) == 1
                change_cursor = changes[0]['sequence']
            finally:
                store.close()
            assert snapshot['data']['state'] == 'queued' or snapshot['id'] < change_cursor
            if snapshot['data']['state'] != 'queued':
                assert read_sse(frames)['data']['kind'] == 'execution_queued'
