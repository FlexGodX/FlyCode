"""Historical Stop authority proves a prior physical resolution, never new work."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.domain.orchestration import (
    AccessScope,
    TaskCommand,
    TaskDraft,
    WorkflowDraft,
)
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store, StoreError


def test_two_connections_issue_one_immutable_prior_resolution_grant(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    store = Store(tmp_path / 'state.sqlite')
    try:
        project = Projects(store).register(root, actor_id='owner-a')
        barrier = Barrier(2)
        def resolve(_):
            connection = Store(store.path)
            try:
                barrier.wait(timeout=5)
                return Projects(connection).resolve(root, actor_id='owner-b')
            finally:
                connection.close()
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(resolve, range(2))) == [project, project]
        rows = store.db.execute('SELECT actor_id,project_id,data FROM project_grants ORDER BY actor_id').fetchall()
        assert [(row[0], row[1]) for row in rows] == [('owner-a', project.id), ('owner-b', project.id)]
        before = [tuple(row) for row in rows]
        assert Projects(store).resolve(root, actor_id='owner-b') == project
        assert [tuple(row) for row in store.db.execute('SELECT actor_id,project_id,data FROM project_grants ORDER BY actor_id')] == before
        root.rename(tmp_path / 'displaced')
        root.mkdir()
        with pytest.raises(StoreError, match='project_changed'):
            Projects(store).resolve(root, actor_id='never-resolved')
        assert not store.db.execute("SELECT 1 FROM project_grants WHERE actor_id='never-resolved'").fetchone()
    finally:
        store.close()


def test_tombstone_keeps_stop_grant_without_fresh_scope(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    store = Store(tmp_path / 'state.sqlite')
    try:
        project = Projects(store).register(root, actor_id='owner-a')
        Projects(store).resolve(root, actor_id='owner-b')
        owner = AccessScope(actor_id='owner-a', project_ids={project.id}, role='owner')
        workflow = store.create_workflow(owner, WorkflowDraft(project_id=project.id, task=TaskDraft(title='Private draft')), key='draft')
        task = store.task(owner, workflow.root_task_id)
        Projects(store).revoke(owner, project.id, expected_revision=1)
        with pytest.raises(StoreError, match='project_revoked'):
            Projects(store).resolve(root, actor_id='owner-b')
        command = TaskCommand(operation='cancel', expected_revision=task.revision, idempotency_key='stop')
        stopped = Projects(store).stop_command('owner-b', task.id, command)
        assert stopped.state == 'cancelled'
        assert Projects(store).stop_command('owner-b', task.id, command) == stopped
        with pytest.raises(StoreError, match='scope'):
            Projects(store).stop_command('never-resolved', task.id, command)
        with pytest.raises(StoreError, match='permission'):
            Projects(store).stop_command('owner-b', task.id, TaskCommand(operation='resume',
                expected_revision=stopped.revision, idempotency_key='not-stop'))
    finally:
        store.close()


def test_schema19_grant_failure_preserves_schema18_rows(tmp_path, monkeypatch):
    path = tmp_path / 'state.sqlite'
    store = Store(path)
    root = tmp_path / 'source'
    root.mkdir()
    project = Projects(store).register(root, actor_id='old-owner')
    owner = AccessScope(actor_id='old-owner', project_ids={project.id}, role='owner')
    store.create_workflow(owner, WorkflowDraft(project_id=project.id,
        task=TaskDraft(title='Existing history must survive')), key='existing-draft')
    tables = ('canonical_projects', 'workflows', 'tasks', 'commands', 'events', 'counters')
    before = {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
        for table in tables}
    store.close()
    raw = sqlite3.connect(path)
    raw.execute('DROP TABLE IF EXISTS project_grants')
    raw.execute('PRAGMA user_version=18')
    raw.commit()
    raw.close()
    connect = sqlite3.connect
    def deny(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'project_grants' else sqlite3.SQLITE_OK)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', deny)
    with pytest.raises(sqlite3.DatabaseError):
        Store(path)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    with connect(path) as reader:
        assert reader.execute('PRAGMA user_version').fetchone()[0] == 18
        assert {table: reader.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
            for table in tables} == before
        assert not reader.execute("SELECT 1 FROM sqlite_master WHERE name='project_grants'").fetchone()
    reopened = Store(path)
    try:
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0] == 19
        assert reopened.db.execute('SELECT COUNT(*) FROM project_grants').fetchone()[0] == 0
        assert {table: [tuple(row) for row in reopened.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
            for table in tables} == before
    finally:
        reopened.close()
