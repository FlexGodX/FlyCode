"""Session journal exports use the same durable events as browser replay."""

import asyncio
import json
from threading import Thread

from fastapi.testclient import TestClient

from flycode.web.app import create_app
from flycode.web.runs import RunRecord, RunRequest


def test_journal_export_is_complete_ordered_and_survives_restart(tmp_path):
    data = tmp_path / 'history'
    app = create_app(tmp_path, data)
    manager = app.state.runs
    record = RunRecord(id='journal-test', request=RunRequest(task='inspect'), status='completed')
    manager.records[record.id], manager.events[record.id] = record, []
    manager._save(record)
    manager.emit(record.id, 'model_start', {'model': 'local', 'role': 'reasoner'})
    manager.emit(record.id, 'custom_event', {'text': '<script>unsafe</script>\nПривет'})
    expected = [event.model_dump(mode='json') for event in manager.events[record.id]]
    for instance in (app, create_app(tmp_path, data)):
        with TestClient(instance, base_url='http://localhost') as client:
            result = client.get('/api/runs/journal-test/journal')
            assert result.status_code == 200
            assert result.headers['content-type'].startswith('application/x-ndjson')
            assert result.headers['content-disposition'] == 'attachment; filename="flycode-journal-test.jsonl"'
            assert [json.loads(line) for line in result.text.splitlines()] == expected
            assert result.text.endswith('\n')
            assert client.get('/api/runs/missing/journal').status_code == 404
            assert client.get('/api/runs/journal-test/journal', headers={
                'Origin': 'https://foreign.example',
            }).status_code == 403


def test_export_freezes_prefix_without_holding_writer_lock(tmp_path):
    app = create_app(tmp_path, tmp_path / 'data')
    manager = app.state.runs
    manager.events['live'] = []
    manager.records['live'] = RunRecord(id='live', request=RunRequest(task='inspect'))
    manager._save(manager.records['live'])
    manager.emit('live', 'status', {'phase': 'running'})
    route = next(route for route in app.routes if getattr(route, 'path', '') == '/api/runs/{run_id}/journal')
    response = route.endpoint('live')
    writer = Thread(target=manager.emit, args=('live', 'status', {'phase': 'stopping'}))
    writer.start()
    writer.join(timeout=2)
    assert not writer.is_alive()

    async def read():
        return [json.loads(line) async for line in response.body_iterator]

    assert [event['id'] for event in asyncio.run(read())] == [1]
    assert len(manager.events['live']) == 2
