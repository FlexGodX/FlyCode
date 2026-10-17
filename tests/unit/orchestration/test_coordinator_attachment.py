"""Client attachment never owns Host recovery, execution authority or lifetime."""

import json
import time
from pathlib import Path

from test_managed_coordinator import running_coordinator

from flycode.orchestration.store import Store


def test_native_ready_coordinator_publishes_private_attachment(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    database, root = tmp_path / 'queue.sqlite', tmp_path / 'hosts'
    store = Store(database)
    store.close()
    with running_coordinator(database, root, 'http://127.0.0.1:1/v1') as (process, session):
        store = Store(database)
        try:
            registration = json.loads(store.db.execute('SELECT data FROM host_registry').fetchone()[0])
            descriptor = Path(registration['runtime_root']['path']) / 'attachment.json'
            deadline = time.monotonic() + 2
            while not descriptor.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert descriptor.is_file(), 'Ready coordinator has no application attachment endpoint'
            data = json.loads(descriptor.read_text())
            assert data['session_id'] == session['id']
            assert process.poll() is None
        finally:
            store.close()


def test_independent_clients_detach_without_closing_host(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    import flycode
    from flycode.orchestration.host import Host

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    from flycode.orchestration.attach import AttachmentServer, CoordinatorClient

    database = tmp_path / 'queue.sqlite'
    script = ('import json,sys; from pathlib import Path; '
        'from flycode.orchestration.attach import CoordinatorClient; '
        'c=CoordinatorClient(Path(sys.argv[1])); print(c.status().model_dump_json(),flush=True); '
        'sys.stdin.readline(); c.close()')
    env = {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)}
    with Host(database, tmp_path / 'host') as host, AttachmentServer(host):
        before = database.read_bytes()
        processes = [subprocess.Popen([sys.executable, '-c', script, str(database)], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            import select

            for process in processes:
                assert select.select([process.stdout], [], [], 5)[0]
                status = json.loads(process.stdout.readline())
                assert status['session_id'] == host.session.id and status['phase'] == 'ready'
                assert set(status) == {'registration_id', 'session_id', 'generation', 'phase'}
            processes[0].communicate(input='close\n', timeout=5)
            assert processes[0].returncode == 0
            with CoordinatorClient(database) as client:
                assert client.status().session_id == host.session.id
            assert processes[1].poll() is None
            host.check()
            assert database.read_bytes() == before
        finally:
            for process in processes:
                if process.poll() is None:
                    _stdout, stderr = process.communicate(input='close\n', timeout=5)
                else:
                    _stdout, stderr = process.communicate(timeout=5)
                assert process.returncode == 0 and not stderr, stderr


def test_client_rejects_replaced_descriptor_and_socket_parent(tmp_path, monkeypatch):
    import os

    import pytest

    from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import StoreError

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host, AttachmentServer(host):
        path = Path(host.registration.runtime_root.path) / 'attachment.json'
        original = path.read_bytes()
        path.unlink()
        other = tmp_path / 'descriptor-target'
        other.write_bytes(original)
        other.chmod(0o600)
        path.symlink_to(other)
        with pytest.raises(StoreError, match='attachment_identity'):
            CoordinatorClient(host.database).status()
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o644)
        with pytest.raises(StoreError, match='attachment_identity'):
            CoordinatorClient(host.database).status()
        path.chmod(0o600)
        descriptor = json.loads(original)
        parent = Path(descriptor['socket_path']).parent
        os.chmod(parent, 0o755)
        with pytest.raises(StoreError, match='attachment_identity'):
            CoordinatorClient(host.database).status()
        os.chmod(parent, 0o700)
        assert CoordinatorClient(host.database).status().session_id == host.session.id


def test_invalid_and_slow_peers_do_not_block_authenticated_attach(tmp_path, monkeypatch):
    import hashlib
    import hmac
    import socket
    import struct

    from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
    from flycode.orchestration.host import Host
    from flycode.orchestration.wire import receive, send

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host, AttachmentServer(host):
        path = Path(host.registration.runtime_root.path) / 'attachment.json'
        descriptor = json.loads(path.read_text())
        def request(*, generation=None, nonce=None, extra=False):
            payload = {'version': 'coordinator-attach-v1', 'operation': 'attach',
                'registration_id': host.registration.id, 'session_id': host.session.id,
                'generation': generation or host.session.generation, 'phase': 'ready', 'challenge': 'ab' * 32}
            body = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            payload['proof'] = hmac.new(bytes.fromhex(nonce or descriptor['auth_nonce']),
                b'flycode-attach-request-v1\0' + body, hashlib.sha256).hexdigest()
            if extra:
                payload['database'] = '/untrusted'
            return payload
        for payload in (request(nonce='00' * 32), request(generation=host.session.generation + 1), request(extra=True)):
            with socket.socket(socket.AF_UNIX) as peer:
                peer.settimeout(2)
                peer.connect(descriptor['socket_path'])
                send(peer, payload)
                try:
                    receive(peer)
                except (EOFError, OSError):
                    pass
                else:
                    raise AssertionError('Unauthenticated peer received a response')
        slow = []
        try:
            for _ in range(7):
                peer = socket.socket(socket.AF_UNIX)
                peer.connect(descriptor['socket_path'])
                peer.sendall(struct.pack('!I', 2**31))
                slow.append(peer)
            start = time.monotonic()
            assert CoordinatorClient(host.database).status().phase == 'ready'
            assert time.monotonic() - start < 2
            host.check()
        finally:
            for peer in slow:
                peer.close()


def test_endpoint_cleanup_preserves_substituted_objects(tmp_path, monkeypatch):
    from flycode.orchestration.attach import AttachmentServer
    from flycode.orchestration.host import Host

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host:
        with AttachmentServer(host):
            path = Path(host.registration.runtime_root.path) / 'attachment.json'
            descriptor = json.loads(path.read_text())
            sock = Path(descriptor['socket_path'])
            path.unlink()
            path.write_text('owner replacement')
            sock.unlink()
            sock.write_text('socket replacement')
        assert path.read_text() == 'owner replacement'
        assert sock.read_text() == 'socket replacement'
        sock.unlink()
        sock.parent.rmdir()


def test_descriptor_access_time_change_does_not_revoke_authentic_session(tmp_path, monkeypatch):
    import os

    from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
    from flycode.orchestration.host import Host

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host, AttachmentServer(host):
        path = Path(host.registration.runtime_root.path) / 'attachment.json'
        os.utime(path, ns=(1, path.stat().st_mtime_ns))
        assert CoordinatorClient(host.database).status().session_id == host.session.id


def test_authenticated_reply_with_wrong_challenge_is_rejected(tmp_path, monkeypatch):
    import hashlib
    import hmac
    import os
    import socket
    from threading import Thread

    import pytest

    from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.wire import receive, send

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host, AttachmentServer(host):
        path = Path(host.registration.runtime_root.path) / 'attachment.json'
        descriptor = json.loads(path.read_text())
        sock = Path(descriptor['socket_path'])
        sock.unlink()
        with socket.socket(socket.AF_UNIX) as fake:
            fake.bind(str(sock))
            os.chmod(sock, 0o600)
            fake.listen(1)
            fake.settimeout(2)
            info = sock.stat()
            descriptor['socket_identity'] = [info.st_dev, info.st_ino]
            path.write_text(json.dumps(descriptor))
            def respond():
                channel, _ = fake.accept()
                with channel:
                    channel.settimeout(2)
                    received = receive(channel)
                    reply = {**received, 'operation': 'attached', 'challenge': '00' * 32}
                    reply.pop('proof')
                    body = json.dumps(reply, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                    proof = hmac.new(bytes.fromhex(descriptor['auth_nonce']),
                        b'flycode-attach-reply-v1\0' + body, hashlib.sha256).hexdigest()
                    send(channel, {**reply, 'proof': proof})
            peer = Thread(target=respond)
            peer.start()
            try:
                with pytest.raises(StoreError, match='attachment_identity'):
                    CoordinatorClient(host.database).status()
            finally:
                peer.join(timeout=3)
                assert not peer.is_alive()
        sock.unlink()


def test_slow_partial_request_has_total_deadline_and_does_not_block_status(tmp_path, monkeypatch):
    import socket
    import struct
    from threading import Event, Thread

    from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
    from flycode.orchestration.host import Host

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host, AttachmentServer(host):
        descriptor = json.loads((Path(host.registration.runtime_root.path) / 'attachment.json').read_text())
        with socket.socket(socket.AF_UNIX) as slow:
            slow.connect(descriptor['socket_path'])
            slow.sendall(struct.pack('!I', 100))
            stop = Event()
            def drip():
                while not stop.wait(.1):
                    try:
                        slow.sendall(b' ')
                    except OSError:
                        return
            peer = Thread(target=drip)
            peer.start()
            try:
                start = time.monotonic()
                assert CoordinatorClient(host.database).status().phase == 'ready'
                assert time.monotonic() - start < .8
                slow.settimeout(1.5)
                assert slow.recv(1) == b''
                assert time.monotonic() - start < 1.5
            finally:
                stop.set()
                peer.join(timeout=2)
                assert not peer.is_alive()


def test_unready_host_does_not_leave_private_socket_objects(tmp_path, monkeypatch):
    import tempfile

    import pytest

    from flycode.orchestration.attach import AttachmentServer
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import StoreError

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    original = tempfile.mkdtemp
    created = []
    def record(*args, **kwargs):
        name = original(*args, **kwargs)
        created.append(Path(name).resolve())
        return name
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host:
        monkeypatch.setattr(tempfile, 'mkdtemp', record)
        host.drain()
        with pytest.raises(StoreError, match='attachment_unavailable'), AttachmentServer(host):
            pytest.fail('Stopped Host advertised readiness')
        try:
            assert not [path for path in created if path.exists()]
        finally:
            for path in created:
                if (path / 's').is_socket():
                    (path / 's').unlink()
                if path.exists():
                    path.rmdir()


def test_listener_start_failure_preserves_original_error_and_cleans_owned_objects(tmp_path, monkeypatch):
    from threading import Thread

    import pytest

    from flycode.orchestration.attach import AttachmentServer
    from flycode.orchestration.host import Host

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    start = Thread.start
    published = []
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host:
        path = Path(host.registration.runtime_root.path) / 'attachment.json'
        def fail(listener):
            if listener.name == 'attach-listener':
                published.append(json.loads(path.read_text()))
                raise RuntimeError('injected listener start failure')
            return start(listener)
        monkeypatch.setattr(Thread, 'start', fail)
        try:
            with pytest.raises(RuntimeError, match='^injected listener start failure$'), AttachmentServer(host):
                pytest.fail('Listener failed to start')
            assert len(published) == 1
            sock = Path(published[0]['socket_path'])
            assert not path.exists() and not sock.exists() and not sock.parent.exists()
            host.check()
        finally:
            path.unlink(missing_ok=True)
            for descriptor in published:
                sock = Path(descriptor['socket_path'])
                sock.unlink(missing_ok=True)
                if sock.parent.exists():
                    sock.parent.rmdir()


def test_old_descriptor_refuses_after_real_host_generation_change(tmp_path, monkeypatch):
    import pytest

    from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import StoreError

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    database, root = tmp_path / 'queue.sqlite', tmp_path / 'host'
    with Host(database, root) as first, AttachmentServer(first):
        path = Path(first.registration.runtime_root.path) / 'attachment.json'
        old = path.read_bytes()
        generation = CoordinatorClient(database).status().generation
    with Host(database, root) as second, AttachmentServer(second):
        current = path.read_bytes()
        assert CoordinatorClient(database).status().generation == generation + 1
        path.write_bytes(old)
        try:
            with pytest.raises(StoreError, match='attachment_identity'):
                CoordinatorClient(database).status()
            second.check()
        finally:
            path.write_bytes(current)
        assert CoordinatorClient(database).status().session_id == second.session.id


def test_live_host_without_ipc_is_not_recovered_or_fenced_by_client(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from flycode.orchestration.attach import CoordinatorClient
    from flycode.orchestration.host import Host
    from flycode.orchestration.store import StoreError

    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    with Host(tmp_path / 'queue.sqlite', tmp_path / 'host') as host:
        with sqlite3.connect(host.database.as_uri() + '?mode=ro', uri=True) as reader:
            before = reader.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0]
        with pytest.raises(StoreError, match='attachment_identity'):
            CoordinatorClient(host.database).status()
        with sqlite3.connect(host.database.as_uri() + '?mode=ro', uri=True) as reader:
            assert reader.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()[0] == before
            for table in ('tasks', 'attempts', 'events', 'model_calls', 'tool_calls'):
                assert reader.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        host.check()
