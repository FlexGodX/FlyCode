"""An explicit launcher competes through Host custody, never diagnostics."""

import json
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Thread

import pytest

from flycode.orchestration.host import Host
from flycode.orchestration.store import Store, StoreError


def test_racing_native_launchers_attach_one_generation(tmp_path, monkeypatch, coordinator_processes):
    from flycode.orchestration.attach import attach_coordinator

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    database, root = tmp_path / 'queue.sqlite', tmp_path / 'hosts'
    barrier = Barrier(2)
    def launch():
        barrier.wait(timeout=5)
        return attach_coordinator(database, state_root=root)
    with ThreadPoolExecutor(max_workers=2) as pool:
        clients = list(pool.map(lambda _: launch(), range(2)))
    try:
        statuses = [client.status() for client in clients]
        assert statuses[0] == statuses[1] and statuses[0].generation == 1
        clients[0].close()
        assert clients[1].status() == statuses[0]
        assert 1 <= len(coordinator_processes) <= 2
        assert sum(process.poll() is None for process in coordinator_processes) >= 1
        store = Store(database)
        try:
            for table in ('tasks', 'attempts', 'events', 'model_calls', 'tool_calls'):
                assert store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        finally:
            store.close()
    finally:
        for client in clients:
            client.close()


def test_live_owner_without_ipc_is_not_fenced_by_launch(tmp_path, monkeypatch, coordinator_processes):
    from flycode.orchestration.attach import attach_coordinator

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'hosts') as host:
        store = host.connect()
        before = store.db.execute('SELECT data FROM host_sessions').fetchone()[0]
        try:
            start = time.monotonic()
            with pytest.raises(StoreError, match='attachment_unavailable'):
                attach_coordinator(host.database, timeout=.5)
            assert time.monotonic() - start < 2
            assert store.db.execute('SELECT data FROM host_sessions').fetchone()[0] == before
            assert len(coordinator_processes) == 1
            host.check()
        finally:
            store.close()


def test_invalid_private_descriptor_blocks_launch(tmp_path, monkeypatch, coordinator_processes):
    from flycode.orchestration.attach import attach_coordinator

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'hosts') as host:
        path = Path(host.registration.runtime_root.path) / 'attachment.json'
        path.write_text(json.dumps({'socket_path': '/untrusted', 'auth_nonce': '00' * 32}))
        path.chmod(0o600)
        with pytest.raises(StoreError, match='attachment_identity'):
            attach_coordinator(host.database, timeout=.5)
        assert not coordinator_processes
        host.check()


def test_reaper_start_failure_prevents_detached_spawn(tmp_path, monkeypatch, coordinator_processes):
    from flycode.orchestration.attach import attach_coordinator

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    original = Thread.start
    def fail_reaper(thread):
        if thread.name == 'coordinator-reaper':
            raise RuntimeError("can't start new thread")
        return original(thread)
    monkeypatch.setattr(Thread, 'start', fail_reaper)
    with pytest.raises(StoreError, match='attachment_unavailable'):
        attach_coordinator(tmp_path / 'queue.sqlite', state_root=tmp_path / 'hosts')
    assert not coordinator_processes


def test_abrupt_owned_host_exit_restarts_and_rejects_old_authentication(tmp_path, monkeypatch, coordinator_processes):
    from flycode.orchestration.attach import (
        VERSION,
        CoordinatorClient,
        _Descriptor,
        _endpoint,
        _proof,
        attach_coordinator,
    )
    from flycode.orchestration.wire import receive, send

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    database, root = tmp_path / 'queue.sqlite', tmp_path / 'hosts'
    client = attach_coordinator(database, state_root=root)
    before = client.status()
    store = Store(database)
    try:
        registration = json.loads(store.db.execute('SELECT data FROM host_registry').fetchone()[0])
    finally:
        store.close()
    path = Path(registration['runtime_root']['path']) / 'attachment.json'
    previous = _Descriptor.model_validate_json(path.read_text())
    try:
        # Signal only the exact Popen retained by our own launch fixture.
        process = coordinator_processes[0]
        process.kill()
        process.wait(timeout=10)
        with pytest.raises(StoreError):
            CoordinatorClient(database).status()
        with attach_coordinator(database, state_root=root) as replacement:
            current = replacement.status()
            assert current.registration_id == before.registration_id
            assert current.generation == before.generation + 1 and current.session_id != before.session_id
            descriptor = _Descriptor.model_validate_json(path.read_text())
            body = {**before.model_dump(), 'version': VERSION, 'operation': 'attach', 'challenge': secrets.token_hex(32)}
            with socket.socket(socket.AF_UNIX) as channel:
                channel.settimeout(2)
                channel.connect(descriptor.socket_path)
                send(channel, {**body, 'proof': _proof(previous.auth_nonce, body, 'request')})
                with pytest.raises(EOFError):
                    receive(channel)
            assert replacement.status() == current
    finally:
        client.close()
        # SIGKILL cannot clean its socket. Remove only its captured exact objects.
        _endpoint(previous)
        Path(previous.socket_path).unlink()
        Path(previous.socket_path).parent.rmdir()
