"""Canonical task materials bind exact saved bytes, never client paths."""

import base64
import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from test_tracker_cli import cli
from test_tracker_results import (  # noqa: F401
    approve_parent,
    live_parent,
    managed_pipeline,
    native_app,
    parent_ready,
    pipeline_ready,
    wait_handles,
)

from flycode.domain.orchestration import AccessScope
from flycode.orchestration.store import StoreError
from flycode.tracker.service import TrackerService


def finish(p, *, child_only=False):
    approve_parent(p)
    for number in range(18):
        p['dispatcher'].tick(p['host'], key=f'material-route-{number}')
        wait_handles(p['dispatcher'])
        if (p['store'].task(p['owner'], p['child'].id).completion_id if child_only else
                p['store'].workflow(p['owner'], p['workflow'].id).state == 'succeeded'):
            return
    pytest.fail('Native route did not reach requested verified stage')


def test_materials_expose_exact_saved_patch_and_independent_report_bytes(live_parent):  # noqa: F811
    p = live_parent
    finish(p, child_only=True)
    tracker = TrackerService(p['store'])
    before = p['store'].budget(p['owner'], p['workflow'].id)
    assert hasattr(tracker, 'materials'), 'Canonical task result material catalogue is missing'
    page = tracker.materials(p['owner'], p['child'].id)
    assert page.task_id == p['child'].id
    baseline = next(item for item in page.items if item.kind == 'patch' and item.stage == 'baseline' and item.name == 'src/parser.py')
    target = next(item for item in page.items if item.kind == 'patch' and item.stage == 'target' and item.name == 'src/parser.py')
    for item, expected in [(baseline, b'value = 0\n'), (target, b'value = 1\n')]:
        metadata, raw = tracker.material(p['owner'], p['child'].id, item.id)
        assert metadata == item and raw == expected
        assert hashlib.sha256(raw).hexdigest() == item.sha256
    report = next(item for item in page.items if item.kind == 'review' and item.name.endswith('/junit.xml'))
    _, raw = tracker.material(p['owner'], p['child'].id, report.id)
    assert b'test_parse' in raw and b'<testcase' in raw
    assert str(p['dispatcher'].root) not in page.model_dump_json()
    assert str(p['source']) not in page.model_dump_json()
    with pytest.raises(StoreError, match='not_found'):
        tracker.material(p['owner'], p['parent'].id, target.id)
    foreign = AccessScope(actor_id='foreign', project_ids={'elsewhere'})
    with pytest.raises(StoreError, match='scope'):
        tracker.materials(foreign, p['child'].id)
    assert p['store'].budget(p['owner'], p['workflow'].id) == before
    assert (p['source'] / 'src/parser.py').read_bytes() == b'value = 0\n'


def test_public_materials_share_actual_cli_http_bytes_and_remain_historical(live_parent, tmp_path):  # noqa: F811
    p = live_parent
    finish(p)
    with native_app(p['source'], tmp_path / 'history') as (_, client):
        assert client.post('/api/tasks/projects', json={'selector': '.'}).status_code == 201
        cli('register', '--project', p['source'])
        route = '/api/tasks/' + p['child'].id + '/materials'
        response = client.get(route)
        assert response.status_code == 200, response.text
        assert client.get(route, headers={'X-FlyCode-Token': ''}).status_code == 403
        assert client.get(route, headers={'Origin': 'https://foreign.invalid'}).status_code == 403
        shown = cli('materials', p['child'].id, '--project', p['source'])
        assert response.json() == shown
        target = next(item for item in shown['items'] if item['kind'] == 'patch' and item['stage'] == 'target')
        downloaded = client.get(route + '/' + target['id'])
        assert downloaded.status_code == 200 and downloaded.content == b'value = 1\n'
        assert downloaded.headers['content-type'] == 'application/octet-stream'
        assert downloaded.headers['content-disposition'].startswith('attachment;')
        assert downloaded.headers['x-content-type-options'] == 'nosniff'
        assert 'sandbox' in downloaded.headers['content-security-policy']
        result = cli('material', p['child'].id, target['id'], '--project', p['source'])
        assert result['item'] == target and base64.b64decode(result['content_base64']) == downloaded.content
        assert client.get(route + '/' + '0' * 64).status_code == 404
        before = p['store'].budget(p['owner'], p['workflow'].id)
        (p['source'] / 'dirty.bin').write_bytes(b'owner edit')
        assert not TrackerService(p['store']).task(p['owner'], p['child'].id).integration.current
        assert client.get(route + '/' + target['id']).content == b'value = 1\n'
        if os.environ.get('FLYCODE_TASK_MATERIAL_BROWSER'):
            output = tmp_path / 'browser-materials'
            output.mkdir()
            environment = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
                'FLYCODE_RESULT_TASK': p['child'].id, 'FLYCODE_OTHER_TASK': p['parent'].id, 'FLYCODE_SCREENSHOTS': str(output)}
            result = subprocess.run([os.environ.get('FLYCODE_NODE', 'node'),
                str(Path(__file__).parents[2] / 'browser/task-materials.cjs')], env=environment,
                capture_output=True, text=True, timeout=60, check=False)
            (output / 'browser.log').write_text(result.stdout + result.stderr)
            assert result.returncode == 0, result.stdout + result.stderr
        assert p['store'].budget(p['owner'], p['workflow'].id).spent == before.spent


def test_integration_materials_belong_to_the_checked_task_or_integrator(live_parent):  # noqa: F811
    p = live_parent
    finish(p)
    tracker = TrackerService(p['store'])
    child = tracker.materials(p['owner'], p['child'].id)
    reports = [item for item in child.items if item.kind == 'integration']
    assert reports and {item.stage.split(':')[1] for item in reports} == {p['child'].id}, 'A task must not list another task integration reports'
    integrator = tracker.materials(p['owner'], p['integrator'].id)
    assert {item.stage.split(':')[1] for item in integrator.items if item.kind == 'integration'} == {p['child'].id, p['parent'].id, p['integrator'].id}
    reviewer = tracker.materials(p['owner'], p['child_review'].id)
    assert {item.record_id for item in reviewer.items if item.kind == 'patch'} == {item.record_id for item in child.items if item.kind == 'patch'}


def test_download_rejects_tamper_links_fifo_and_oversize_without_spending(live_parent, tmp_path):  # noqa: F811
    import os
    import time

    from flycode.verification.runner import REPORT_LIMIT

    p = live_parent
    finish(p, child_only=True)
    tracker = TrackerService(p['store'])
    page = tracker.materials(p['owner'], p['child'].id)
    target = next(item for item in page.items if item.kind == 'patch' and item.stage == 'target')
    path = p['dispatcher'].root / 'artifacts' / target.record_id / 'target' / target.name
    original = path.read_bytes()
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'private outside bytes')
    before = p['store'].budget(p['owner'], p['workflow'].id)
    for kind in ['tamper', 'symlink', 'hardlink', 'fifo', 'oversize']:
        path.unlink()
        if kind == 'tamper':
            path.write_bytes(b'changed bytes')
        elif kind == 'symlink':
            path.symlink_to(outside)
        elif kind == 'hardlink':
            os.link(outside, path)
        elif kind == 'fifo':
            os.mkfifo(path)
        else:
            with path.open('wb') as stream:
                stream.truncate(REPORT_LIMIT + 1)
        start = time.monotonic()
        try:
            with pytest.raises(StoreError, match='material_changed'):
                tracker.material(p['owner'], p['child'].id, target.id)
            assert time.monotonic() - start < 2
        finally:
            path.unlink()
            path.write_bytes(original)
    parent, saved = path.parent, path.parent.with_name('owned-src')
    parent.rename(saved)
    parent.symlink_to(saved, target_is_directory=True)
    try:
        with pytest.raises(StoreError, match='material_changed'):
            tracker.material(p['owner'], p['child'].id, target.id)
    finally:
        parent.unlink()
        saved.rename(parent)
    assert tracker.material(p['owner'], p['child'].id, target.id)[1] == b'value = 1\n'
    assert p['store'].budget(p['owner'], p['workflow'].id) == before


def test_download_metadata_must_match_its_exact_historical_assignment(live_parent):  # noqa: F811
    from flycode.domain.orchestration import WorkerBinding
    from flycode.domain.results import IntegrationRecord, ReviewExecution
    from flycode.orchestration.store import changed

    p = live_parent
    finish(p)
    store, owner = p['store'], p['owner']
    tracker = TrackerService(store)
    page = tracker.materials(owner, p['child'].id)
    artifact = next(item for item in page.items if item.kind == 'patch')
    attempt = next(item for item in store.attempts(owner, p['workflow'].id) if item.task_id == p['child'].id)
    binding = store._load('worker_bindings', attempt.execution_binding_id, WorkerBinding)
    review_attempt = next(item for item in store.attempts(owner, p['workflow'].id) if item.task_id == p['child_review'].id)
    execution = store._load('review_executions', review_attempt.id, ReviewExecution)
    integration_id = next(item.record_id for item in page.items if item.kind == 'integration')
    integration = store._load('integrations', integration_id, IntegrationRecord)
    review_file = next(item for item in page.items if item.kind == 'review')
    integration_file = next(item for item in page.items if item.kind == 'integration')
    for table, original, invalid, material in [
        ('worker_bindings', binding, changed(binding, semantic_revision=binding.semantic_revision + 1), artifact),
        ('review_executions', execution, changed(execution, patch_hash='0' * 64), review_file),
        ('integrations', integration, changed(integration, attempt_id=attempt.id), integration_file),
    ]:
        identity = original.attempt_id if table == 'review_executions' else original.id
        with store.transaction():
            store.db.execute('UPDATE ' + table + ' SET data=? WHERE id=?', (invalid.model_dump_json(), identity))
        try:
            with pytest.raises(StoreError, match='material_changed'):
                tracker.material(owner, p['child'].id, material.id)
        finally:
            with store.transaction():
                store.db.execute('UPDATE ' + table + ' SET data=? WHERE id=?', (original.model_dump_json(), identity))
    assert tracker.material(owner, p['child'].id, artifact.id)[1] in {b'value = 0\n', b'value = 1\n'}


def test_lost_historical_material_does_not_hide_an_intact_later_result(live_parent):  # noqa: F811
    p = live_parent
    finish(p)
    tracker = TrackerService(p['store'])
    page = tracker.materials(p['owner'], p['parent'].id)
    first = next(item for item in page.items if item.kind == 'patch' and item.name == 'src/note.py')
    later = next(item for item in page.items if item.kind == 'patch' and item.stage == 'target' and item.name == 'src/extra.py')
    (p['dispatcher'].root / 'artifacts' / first.record_id / 'manifest.json').unlink()
    assert tracker.material(p['owner'], p['parent'].id, later.id)[1] == b'value = 4\n'
    current = tracker.materials(p['owner'], p['parent'].id)
    assert first.record_id in current.unavailable
    assert any(item.id == later.id for item in current.items)


def test_review_completion_cannot_block_reading_an_unchanged_author_patch(live_parent, monkeypatch):  # noqa: F811
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.pipeline import Pipelines
    from flycode.tracker.materials import ResultMaterials

    p = live_parent
    approve_parent(p)
    for number in range(8):
        started = p['dispatcher'].tick(p['host'], key=f'material-before-review-{number}')
        wait_handles(p['dispatcher'])
        if started.attempt and started.attempt.task_id == p['child'].id:
            break
    assert started.attempt.task_id == p['child'].id
    artifact = Artifacts(p['store'], p['dispatcher'].root).for_attempt(p['owner'], started.attempt.id)
    tracker = TrackerService(p['store'])
    target = next(item for item in tracker.materials(p['owner'], p['child'].id).items
        if item.kind == 'patch' and item.stage == 'target')
    assert target.record_id == artifact.id and p['store'].task(p['owner'], p['child'].id).completion_id is None
    read, completed = ResultMaterials._read, []
    def finish_real_review(service, root, name, limit):
        raw = read(service, root, name, limit)
        if name.endswith('/target/src/parser.py') and not completed:
            completed.append(True)
            p['dispatcher'].tick(p['host'], key='material-concurrent-review')
            wait_handles(p['dispatcher'])
            Pipelines(p['store'], p['dispatcher'].root).advance(p['host'], p['workflow'].id)
            assert p['store'].task(p['owner'], p['child'].id).completion_id
        return raw
    monkeypatch.setattr(ResultMaterials, '_read', finish_real_review)
    assert tracker.material(p['owner'], p['child'].id, target.id)[1] == b'value = 1\n'
    assert completed
