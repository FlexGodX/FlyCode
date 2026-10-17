"""Persisted probe evidence stays usable inside canonical ownership decisions."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

import flycode
from flycode.llm.catalogue import CatalogueBusy, ModelCatalogue
from flycode.orchestration.store import Store


@pytest.fixture
def admitted(tmp_path, model_server):
    database = tmp_path / 'catalogue.sqlite'
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url, database=database)
    ready = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    return database, catalogue, ready


def test_durable_resolution_does_not_take_a_second_writer_lock(tmp_path, model_server):
    database = tmp_path / 'catalogue.sqlite'
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url, database=database)
    entry = catalogue.discover()[0]
    ready = catalogue.probe(entry.model_ref, 'primary')
    store = Store(database, catalogue=catalogue)
    try:
        with store.transaction():
            resolved = catalogue.resolve(ready.model_ref, 'primary', ready.capability_version)
            assert resolved is not None
            assert resolved.loaded_instance_id == ready.loaded_instance_id
    finally:
        store.close()
    assert len(model_server.requests) == 1


def test_changed_probe_owner_invalidates_saved_admission(admitted, model_server):
    database, _, ready = admitted
    store = Store(database)
    try:
        row = store.db.execute("SELECT id,data FROM model_calls WHERE state='completed'").fetchone()
        data = json.loads(row['data'])
        data['owner_id'] = 'replacement-owner'
        store.db.execute('UPDATE model_calls SET data=? WHERE id=?', (json.dumps(data), row['id']))
    finally:
        store.close()
    restored = ModelCatalogue(model_server.url, database=database)
    assert restored.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    assert len(model_server.requests) == 1


def test_fresh_process_restores_exact_admission_without_http(admitted, model_server):
    database, _, ready = admitted
    code = '''
import json,sys
from pathlib import Path
from flycode.llm.catalogue import ModelCatalogue
catalogue=ModelCatalogue(sys.argv[1],database=Path(sys.argv[2]))
entry=catalogue.resolve(sys.argv[3],'primary',sys.argv[4])
assert entry is not None
print(entry.model_dump_json())
'''
    result = subprocess.run([sys.executable, '-c', code, model_server.url, str(database), ready.model_ref,
        ready.capability_version], env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)},
        capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ready.model_dump(mode='json')
    assert len(model_server.requests) == 1


def test_discovery_is_not_an_admission_after_restart(tmp_path, model_server):
    database = tmp_path / 'catalogue.sqlite'
    catalogue = ModelCatalogue(model_server.url, database=database)
    entry = catalogue.discover()[0]
    restored = ModelCatalogue(model_server.url, database=database)
    assert restored.resolve(entry.model_ref, 'primary', entry.capability_version) is None
    assert restored.resolve(entry.model_ref, 'scorer', entry.capability_version) is None
    assert not model_server.requests


def test_probe_does_not_hold_database_transaction_during_http(tmp_path, model_server):
    database = tmp_path / 'catalogue.sqlite'
    catalogue = ModelCatalogue(model_server.url, database=database)
    entry = catalogue.discover()[0]
    def respond(request):
        store = Store(database)
        try:
            with store.transaction():
                assert store.db.execute("SELECT COUNT(*) FROM model_calls WHERE state='running'").fetchone()[0] == 1
        finally:
            store.close()
        return model_server.completion(json.dumps({'candidates': [{'tool': 'list_files'}]}))
    model_server.respond = respond
    assert catalogue.probe(entry.model_ref, 'primary').roles == ['primary']
    assert len(model_server.requests) == 1


def test_independent_catalogues_preserve_other_role_and_revoke_failed_reprobe(admitted, model_server):
    database, first, ready = admitted
    second = ModelCatalogue(model_server.url, database=database)
    second.discover()
    model_server.respond = lambda request: model_server.completion('A', [('A', -.1), ('B', -2.)])
    paired = second.probe(ready.model_ref, 'scorer')
    assert set(paired.roles) == {'primary', 'scorer'}
    assert paired.probe_calls == 2 and paired.revision > ready.revision
    model_server.respond = lambda request: model_server.completion('invalid protocol')
    rejected = first.probe(ready.model_ref, 'primary')
    assert rejected.roles == ['scorer'] and rejected.probe_calls == 3
    assert second.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    assert second.resolve(ready.model_ref, 'scorer', ready.capability_version) is not None
    assert len(model_server.requests) == 3


def test_runtime_change_and_disappearance_revoke_across_catalogues(admitted, model_server):
    database, first, ready = admitted
    second = ModelCatalogue(model_server.url, database=database)
    assert second.resolve(ready.model_ref, 'primary', ready.capability_version) is not None
    model_server.instance_ids[ready.model_id] = 'reloaded-instance'
    changed = first.refresh_runtime(ready.model_ref)
    assert not changed.roles and changed.revision > ready.revision
    assert second.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    model_server.models = []
    assert all(item.status == 'unavailable' for item in second.discover())
    assert len(model_server.requests) == 1


def test_stale_saved_snapshot_cannot_restore_revoked_capability(admitted, model_server):
    from flycode.orchestration.model_catalogue import ModelAdmissions
    from flycode.orchestration.store import StoreError

    database, catalogue, ready = admitted
    admissions = ModelAdmissions(database)
    old = admissions.load(catalogue.endpoint_ref)
    model_server.models = []
    catalogue.discover()
    with pytest.raises(StoreError, match='catalogue_conflict'):
        admissions.save({ref: row.descriptor for ref, row in old.items()}, old)
    restored = ModelCatalogue(model_server.url, database=database)
    assert restored.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    assert len(model_server.requests) == 1


def test_inflight_probe_cannot_overwrite_another_process_revocation(admitted, model_server):
    database, first, ready = admitted
    second = ModelCatalogue(model_server.url, database=database)
    entered, release = Event(), Event()
    def respond(request):
        entered.set()
        assert release.wait(5)
        return model_server.completion('A', [('A', -.1), ('B', -2.)])
    model_server.respond = respond
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(second.probe, ready.model_ref, 'scorer')
        try:
            assert entered.wait(5)
            model_server.models = []
            first.discover()
        finally:
            release.set()
        with pytest.raises(CatalogueBusy, match='catalogue_conflict'):
            pending.result(timeout=5)
    assert first.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    assert second.resolve(ready.model_ref, 'scorer', ready.capability_version) is None
    assert len(model_server.requests) == 2
    store = Store(database)
    try:
        assert store.db.execute("SELECT COUNT(*) FROM model_calls WHERE state='completed'").fetchone()[0] == 2
    finally:
        store.close()


@pytest.mark.parametrize('change', ['missing', 'uncertain', 'late', 'legacy', 'model', 'role', 'sql_endpoint'])
def test_invalid_exact_probe_receipt_refuses_readiness(admitted, model_server, change):
    database, _, ready = admitted
    store = Store(database)
    try:
        row = store.db.execute("SELECT id,data FROM model_calls WHERE state='completed'").fetchone()
        data = json.loads(row['data'])
        if change == 'missing':
            store.db.execute('DELETE FROM model_calls WHERE id=?', (row['id'],))
        elif change == 'uncertain':
            store.db.execute("UPDATE model_calls SET state='uncertain' WHERE id=?", (row['id'],))
        elif change == 'sql_endpoint':
            store.db.execute("UPDATE model_calls SET endpoint_ref='other' WHERE id=?", (row['id'],))
        else:
            name, value = {'late': ('late', True), 'legacy': ('owner_kind', 'legacy'),
                           'model': ('model_id', 'other'), 'role': ('role', 'scorer')}[change]
            data[name] = value
            store.db.execute('UPDATE model_calls SET data=? WHERE id=?', (json.dumps(data), row['id']))
    finally:
        store.close()
    restored = ModelCatalogue(model_server.url, database=database)
    assert restored.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    assert len(model_server.requests) == 1


def test_other_endpoint_cannot_restore_capabilities(admitted, model_server):
    database, _, ready = admitted
    restored = ModelCatalogue('http://127.0.0.1:1/v1', database=database)
    assert restored.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    assert len(model_server.requests) == 1


@pytest.mark.parametrize('field,value', [('context_limit', 999999), ('load_fingerprint', 'other-load'),
                                        ('capability_version', 'unprobed-protocol-v2')])
def test_changed_admission_configuration_is_not_covered_by_old_probe(admitted, model_server, field, value):
    database, _, ready = admitted
    store = Store(database)
    try:
        row = store.db.execute('SELECT data FROM model_admissions WHERE model_ref=?', (ready.model_ref,)).fetchone()
        data = json.loads(row['data'])
        data['descriptor'][field] = value
        store.db.execute('UPDATE model_admissions SET data=? WHERE model_ref=?', (json.dumps(data), ready.model_ref))
    finally:
        store.close()
    restored = ModelCatalogue(model_server.url, database=database)
    version = value if field == 'capability_version' else ready.capability_version
    assert restored.resolve(ready.model_ref, 'primary', version) is None
    assert len(model_server.requests) == 1


def test_schema17_failure_rolls_back_without_losing_host_calls(admitted, monkeypatch):
    database, _, _ = admitted
    store = Store(database)
    before = [tuple(row) for row in store.db.execute('SELECT * FROM model_calls')]
    store.db.execute('DROP TABLE model_admissions')
    store.db.execute('PRAGMA user_version=16')
    store.close()
    connect = sqlite3.connect
    def deny(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '17' else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', deny)
        with pytest.raises(sqlite3.DatabaseError):
            Store(database)
    connection = connect(database)
    try:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 16
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='model_admissions'").fetchone()
        assert list(connection.execute('SELECT * FROM model_calls')) == before
    finally:
        connection.close()
    store = Store(database)
    try:
        assert store.db.execute('PRAGMA user_version').fetchone()[0] == 19
        assert [tuple(row) for row in store.db.execute('SELECT * FROM model_calls')] == before
    finally:
        store.close()
