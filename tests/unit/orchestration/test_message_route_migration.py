"""Route authority installs atomically without consuming global migrations."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.orchestration.store import Store, StoreError

TABLES = ('message_route_grants', 'message_route_heads', 'message_route_proofs')


def legacy(path):
    store = Store(path)
    try:
        for name in reversed(TABLES):
            store.db.execute('DROP TABLE IF EXISTS ' + name)
        store.db.execute("DELETE FROM counters WHERE name='message_routes_schema'")
        store.db.execute("UPDATE counters SET value=7 WHERE name='task_number'")
        store.db.execute("INSERT INTO commands VALUES ('owner','retained',NULL,'hash','{\"ok\":true}')")
        return store.db.execute('PRAGMA user_version').fetchone()[0]
    finally:
        store.close()


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_upgrade_preserves_global_version_and_existing_records(tmp_path):
    path = tmp_path / 'queue.sqlite'
    global_version = legacy(path)
    store = Store(path)
    try:
        assert set(TABLES) <= tables(store.db)
        assert store._counter('message_routes_schema') == 1
        assert store._counter('task_number') == 7
        assert store.db.execute('PRAGMA user_version').fetchone()[0] == global_version
        assert tuple(store.db.execute("SELECT * FROM commands WHERE key='retained'").fetchone()) == (
            'owner', 'retained', None, 'hash', '{"ok":true}')
        assert store.db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize('failure', ['ddl', 'marker'])
def test_failed_install_rolls_back_all_sidecars_and_reopens(tmp_path, monkeypatch, failure):
    path = tmp_path / 'queue.sqlite'
    global_version = legacy(path)
    if failure == 'marker':
        with sqlite3.connect(path) as db:
            db.execute("CREATE TRIGGER deny_routes BEFORE INSERT ON counters "
                "WHEN NEW.name='message_routes_schema' BEGIN SELECT RAISE(ABORT,'route marker denied'); END")
    connect, opened = sqlite3.connect, []

    def guarded(*args, **kwargs):
        db = connect(*args, **kwargs)
        if failure == 'ddl':
            db.set_authorizer(lambda action, arg, *_: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_TABLE and arg == 'message_route_heads' else sqlite3.SQLITE_OK)
        opened.append(db)
        return db

    with monkeypatch.context() as patch:
        patch.setattr('flycode.orchestration.store.sqlite3.connect', guarded)
        with pytest.raises(sqlite3.DatabaseError):
            Store(path)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('SELECT 1')
    with connect(path) as db:
        assert not set(TABLES) & tables(db)
        assert db.execute("SELECT value FROM counters WHERE name='message_routes_schema'").fetchone() is None
        assert db.execute('PRAGMA user_version').fetchone()[0] == global_version
        if failure == 'marker':
            db.execute('DROP TRIGGER deny_routes')
    store = Store(path)
    try:
        assert set(TABLES) <= tables(store.db)
    finally:
        store.close()


def test_concurrent_install_commits_one_component_schema(tmp_path):
    path = tmp_path / 'queue.sqlite'
    legacy(path)
    barrier = Barrier(2)

    def open_store():
        barrier.wait(timeout=5)
        store = Store(path)
        try:
            return store._counter('message_routes_schema'), set(TABLES) <= tables(store.db)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(open_store) for _ in range(2)]
        assert [future.result(timeout=15) for future in futures] == [(1, True), (1, True)]


def test_reopen_keeps_counters_and_does_not_emit_events(tmp_path):
    path = tmp_path / 'queue.sqlite'
    store = Store(path)
    try:
        assert set(TABLES) <= tables(store.db)
        before = store.db.execute('SELECT count(*) FROM events').fetchone()[0]
        store.db.execute("UPDATE counters SET value=9 WHERE name='task_number'")
    finally:
        store.close()
    reopened = Store(path)
    try:
        assert reopened._counter('task_number') == 9
        assert reopened._counter('message_routes_schema') == 1
        assert reopened.db.execute('SELECT count(*) FROM events').fetchone()[0] == before
    finally:
        reopened.close()


def test_future_component_schema_is_rejected_without_reset(tmp_path):
    path = tmp_path / 'queue.sqlite'
    legacy(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO counters VALUES ('message_routes_schema',2)")
    with pytest.raises(StoreError, match='schema_version'):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM counters WHERE name='message_routes_schema'").fetchone()[0] == 2
        assert not set(TABLES) & tables(db)


def test_unversioned_partial_schema_is_not_adopted(tmp_path):
    path = tmp_path / 'queue.sqlite'
    legacy(path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE message_route_grants (id TEXT PRIMARY KEY, data TEXT)')
    with pytest.raises(StoreError, match='schema_version'):
        Store(path)
    with sqlite3.connect(path) as db:
        assert set(TABLES) & tables(db) == {'message_route_grants'}
        assert db.execute("SELECT value FROM counters WHERE name='message_routes_schema'").fetchone() is None


def test_component_namespace_does_not_admit_unknown_global_schema(tmp_path):
    path = tmp_path / 'queue.sqlite'
    legacy(path)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=999')
    with pytest.raises(StoreError, match='schema_version'):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 999
        assert not set(TABLES) & tables(db)
