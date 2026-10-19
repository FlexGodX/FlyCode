"""Authenticated local discovery; attachment never acquires Host authority."""

import hashlib
import hmac
import json
import os
import secrets
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Event, Thread
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.source import SourceRoot
from .host import Host, HostRegistration, HostSession, _file, _read_registration, _root
from .owner_requests import OwnerOutcome, owner_request
from .store import StoreError, host_pin_path
from .wire import receive, send

LIMIT = 8192
OWNER_LIMIT = 1024 * 1024
VERSION: Literal['coordinator-attach-v1'] = 'coordinator-attach-v1'


class AttachmentStatus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    registration_id: str
    session_id: str
    generation: int = Field(ge=1)
    phase: Literal['ready']


class _Descriptor(AttachmentStatus):
    version: Literal['coordinator-attach-v1'] = VERSION
    database_identity: tuple[int, int]
    socket_path: str
    socket_identity: tuple[int, int]
    socket_parent: SourceRoot
    auth_nonce: str = Field(pattern=r'^[0-9a-f]{64}$', repr=False)


class _Message(AttachmentStatus):
    version: Literal['coordinator-attach-v1'] = VERSION
    challenge: str = Field(pattern=r'^[0-9a-f]{64}$')
    proof: str = Field(pattern=r'^[0-9a-f]{64}$')


class _Request(_Message):
    operation: Literal['attach'] = 'attach'


class _Reply(_Message):
    operation: Literal['attached'] = 'attached'


class _OwnerHeader(_Message):
    operation: Literal['owner']
    payload_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class _OwnerReply(_Message):
    operation: Literal['owner_result']
    payload_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    outcome: OwnerOutcome


def _payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _proof(nonce, value, direction):
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hmac.new(bytes.fromhex(nonce), b'flycode-attach-' + direction.encode() + b'-v1\0' + body,
        hashlib.sha256).hexdigest()


def _status(registration, session):
    if session.registration_id != registration.id or session.phase != 'ready':
        raise StoreError('attachment_unavailable')
    return AttachmentStatus(registration_id=registration.id, session_id=session.id,
        generation=session.generation, phase='ready')


def _records(database):
    database = Path(database).expanduser().resolve(strict=True)
    _file(database)
    db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, isolation_level=None, timeout=.5)
    try:
        db.execute('BEGIN')
        row = db.execute('SELECT data FROM host_registry WHERE id=1').fetchone()
        session = db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()
        if row is None or session is None:
            raise StoreError('attachment_unavailable')
        registration = HostRegistration.model_validate_json(row[0])
        current = HostSession.model_validate_json(session[0])
    finally:
        db.close()
    # Inspect existing pins only: this object never enters or starts a Host.
    Host(database)._physical(registration, host_pin_path(database))
    return registration, current


def _inspect(database):
    registration, session = _records(database)
    return registration, _status(registration, session)


def _private(info, mode):
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
        raise StoreError('attachment_identity')


def _endpoint(descriptor):
    path = Path(descriptor.socket_path)
    if (not path.is_absolute() or path.name != 's'
            or _root(path.parent) != descriptor.socket_parent):
        raise StoreError('attachment_identity')
    _private(path.parent.lstat(), 0o700)
    info = path.lstat()
    _private(info, 0o600)
    if not stat.S_ISSOCK(info.st_mode) or (info.st_dev, info.st_ino) != descriptor.socket_identity:
        raise StoreError('attachment_identity')


def _read_descriptor(registration):
    path = Path(registration.runtime_root.path) / 'attachment.json'
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        _private(info, 0o600)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= LIMIT:
            raise StoreError('attachment_identity')
        data = os.read(descriptor, LIMIT + 1)
        after = os.fstat(descriptor)
        stable = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_nlink, item.st_size, item.st_mtime_ns)
        if len(data) != info.st_size or stable(after) != stable(info):
            raise StoreError('attachment_identity')
        value = _Descriptor.model_validate_json(data)
    finally:
        os.close(descriptor)
    if value.database_identity != registration.database_identity or value.registration_id != registration.id:
        raise StoreError('attachment_identity')
    return value


def _descriptor(registration, status):
    value = _read_descriptor(registration)
    if value.model_dump(include=set(AttachmentStatus.model_fields)) != status.model_dump():
        raise StoreError('attachment_identity')
    _endpoint(value)
    return value


class CoordinatorClient:
    def __init__(self, database: Path):
        self.database = Path(database)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def status(self) -> AttachmentStatus:
        if self.closed:
            raise StoreError('attachment_closed')
        try:
            registration, expected = _inspect(self.database)
            descriptor = _descriptor(registration, expected)
            body = {**expected.model_dump(), 'version': VERSION, 'operation': 'attach',
                'challenge': secrets.token_hex(32)}
            request = {**body, 'proof': _proof(descriptor.auth_nonce, body, 'request')}
            with socket.socket(socket.AF_UNIX) as channel:
                channel.settimeout(1)
                channel.connect(descriptor.socket_path)
                send(channel, request, max_frame=LIMIT)
                reply = _Reply.model_validate(receive(channel, max_frame=LIMIT, deadline=time.monotonic() + 1))
            contents = reply.model_dump(exclude={'proof'})
            if (reply.challenge != body['challenge']
                    or not hmac.compare_digest(reply.proof, _proof(descriptor.auth_nonce, contents, 'reply'))
                    or reply.model_dump(include=set(AttachmentStatus.model_fields)) != expected.model_dump()
                    or _inspect(self.database) != (registration, expected)):
                raise StoreError('attachment_identity')
            _descriptor(registration, expected)
            return expected
        except StoreError:
            raise
        except (OSError, ValueError, TypeError, EOFError, sqlite3.Error) as error:
            raise StoreError('attachment_identity') from error

    def owner(self, value) -> OwnerOutcome:
        if self.closed:
            raise StoreError('attachment_closed')
        try:
            payload = owner_request(value).model_dump(mode='json')
        except ValueError:
            return OwnerOutcome(error_code='invalid_fields')
        try:
            registration, expected = _inspect(self.database)
            descriptor = _descriptor(registration, expected)
            body = {**expected.model_dump(), 'version': VERSION, 'operation': 'owner',
                'challenge': secrets.token_hex(32), 'payload_hash': _payload_hash(payload)}
            with socket.socket(socket.AF_UNIX) as channel:
                channel.settimeout(1)
                channel.connect(descriptor.socket_path)
                send(channel, {**body, 'proof': _proof(descriptor.auth_nonce, body, 'request')}, max_frame=LIMIT)
                send(channel, payload, max_frame=OWNER_LIMIT)
                channel.settimeout(30)
                reply = _OwnerReply.model_validate(receive(channel, max_frame=OWNER_LIMIT, deadline=time.monotonic() + 30))
            contents = reply.model_dump(exclude={'proof'})
            if (reply.challenge != body['challenge'] or reply.payload_hash != body['payload_hash']
                    or not hmac.compare_digest(reply.proof, _proof(descriptor.auth_nonce, contents, 'reply'))
                    or reply.model_dump(include=set(AttachmentStatus.model_fields)) != expected.model_dump()
                    or _inspect(self.database) != (registration, expected)):
                raise StoreError('attachment_identity')
            _descriptor(registration, expected)
            return reply.outcome
        except StoreError:
            raise
        except (OSError, ValueError, TypeError, EOFError, sqlite3.Error) as error:
            raise StoreError('attachment_identity') from error


def _launch_preflight(database, failure):
    pin = host_pin_path(database)
    if not database.exists():
        if pin.exists():
            raise StoreError('attachment_identity')
        return
    _file(database)
    registration = _read_registration(database)
    if registration is None:
        if pin.exists():
            raise StoreError('attachment_identity')
        return
    registration, session = _records(database)
    path = Path(registration.runtime_root.path) / 'attachment.json'
    if not path.exists() and not path.is_symlink():
        return
    descriptor = _read_descriptor(registration)
    try:
        _endpoint(descriptor)
    except FileNotFoundError:
        return
    if (session.phase == 'ready' and descriptor.session_id == session.id
            and descriptor.generation == session.generation and failure.__cause__ is None):
        # A live-looking authenticated endpoint failed proof, not discovery.
        raise failure


def attach_coordinator(database: Path, *, state_root: Path | None = None, timeout: float = 10) -> CoordinatorClient:
    """Explicit trusted launch/attach; only the child Host may elect or recover."""
    database = Path(database).expanduser().resolve()
    client = CoordinatorClient(database)
    try:
        client.status()
        return client
    except StoreError as error:
        if error.code not in {'attachment_identity', 'attachment_unavailable'}:
            raise
        try:
            _launch_preflight(database, error)
        except StoreError:
            raise
        except (OSError, ValueError, sqlite3.Error) as problem:
            raise StoreError('attachment_identity') from problem
    args = [sys.executable, '-m', 'flycode.orchestration.coordinator', '--database', str(database)]
    if state_root is not None:
        args.extend(('--state-root', str(state_root)))
    environment = dict(os.environ)
    package = str(Path(__file__).resolve().parents[2])
    environment['PYTHONPATH'] = package + (os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    ready = Event()
    process = None
    def reap():
        ready.wait()
        if process is not None:
            process.wait()
    try:
        # Establish child ownership before spawning: thread exhaustion must not
        # leave a successfully started detached child without its reaper.
        Thread(target=reap, name='coordinator-reaper', daemon=True).start()
        # A regular owned file outlives the caller's pipes. Closing the client
        # never signals this process or changes its approved canonical work.
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(args, env=environment, stdin=subprocess.DEVNULL,
                stdout=output, stderr=output, start_new_session=True)
    except (OSError, RuntimeError) as error:
        raise StoreError('attachment_unavailable') from error
    finally:
        ready.set()
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.status()
            return client
        except StoreError as error:
            if time.monotonic() >= deadline:
                client.close()
                raise StoreError('attachment_unavailable') from error
        time.sleep(min(.05, max(0, deadline - time.monotonic())))


class AttachmentServer:
    def __init__(self, host: Host):
        self.host = host
        self.stop = Event()
        self.slots = BoundedSemaphore(8)
        self.owner_slot = BoundedSemaphore(1)
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='attach-peer')
        self.channel = None
        self.thread = None
        self.descriptor = None
        self.descriptor_identity = None
        self.parent = None
        self.parent_root = None
        self.socket_identity = None

    def __enter__(self):
        try:
            self.host.check()
            status = _status(self.host.registration, self.host.session)
            self.parent = Path(tempfile.mkdtemp(prefix='flyc-', dir='/tmp')).resolve()
            self.parent_root = _root(self.parent)
            path = self.parent / 's'
            self.channel = socket.socket(socket.AF_UNIX)
            self.channel.bind(str(path))
            info = path.lstat()
            self.socket_identity = info.st_dev, info.st_ino
            os.chmod(path, 0o600)
            self.channel.listen(16)
            self.channel.settimeout(.1)
            self.descriptor = _Descriptor(**status.model_dump(),
                database_identity=self.host.registration.database_identity, socket_path=str(path),
                socket_identity=self.socket_identity, socket_parent=self.parent_root, auth_nonce=secrets.token_hex(32))
            self.path = Path(self.host.registration.runtime_root.path) / 'attachment.json'
            fd, temporary = tempfile.mkstemp(prefix='.attachment-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(self.descriptor.model_dump_json())
                    stream.flush()
                    os.fsync(stream.fileno())
                self.host.check()
                store = self.host.connect()
                try:
                    self.host.validate_sql(store)
                finally:
                    store.close()
                os.replace(temporary, self.path)
                self.descriptor_identity = _file(self.path)
                parent_fd = os.open(self.path.parent, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            finally:
                Path(temporary).unlink(missing_ok=True)
            self.thread = Thread(target=self._accept, name='attach-listener', daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *exc):
        self.close()

    def _accept(self):
        while not self.stop.is_set():
            try:
                channel, _ = self.channel.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not self.slots.acquire(blocking=False):
                channel.close()
                continue
            self.pool.submit(self._handle, channel)

    def _handle(self, channel):
        try:
            with channel:
                channel.settimeout(1)
                frame = receive(channel, max_frame=LIMIT, deadline=time.monotonic() + 1)
                request = (_OwnerHeader if frame.get('operation') == 'owner' else _Request).model_validate(frame)
                contents = request.model_dump(exclude={'proof'})
                if (request.model_dump(include=set(AttachmentStatus.model_fields))
                        != self.descriptor.model_dump(include=set(AttachmentStatus.model_fields))
                        or not hmac.compare_digest(request.proof, _proof(self.descriptor.auth_nonce, contents, 'request'))):
                    return
                self.host.check()
                _endpoint(self.descriptor)
                if isinstance(request, _OwnerHeader):
                    self._owner(channel, request, contents)
                    return
                store = self.host.connect()
                try:
                    self.host.validate_sql(store)
                finally:
                    store.close()
                reply = {**contents, 'operation': 'attached'}
                send(channel, {**reply, 'proof': _proof(self.descriptor.auth_nonce, reply, 'reply')}, max_frame=LIMIT)
        except (OSError, ValueError, TypeError, EOFError, sqlite3.Error):
            pass
        finally:
            self.slots.release()

    def _owner(self, channel, request, contents):
        if not self.owner_slot.acquire(blocking=False):
            outcome = OwnerOutcome(error_code='owner_busy')
        else:
            try:
                payload = receive(channel, max_frame=OWNER_LIMIT, deadline=time.monotonic() + 1)
                if not hmac.compare_digest(_payload_hash(payload), request.payload_hash):
                    return
                from ..llm.catalogue import ModelCatalogue
                from .owner_commands import OwnerCommands

                store = self.host.connect(catalogue=ModelCatalogue(database=self.host.database))
                try:
                    self.host.validate_sql(store)
                    outcome = OwnerCommands(store).execute(payload)
                finally:
                    store.close()
            finally:
                self.owner_slot.release()
        reply = {**contents, 'operation': 'owner_result', 'outcome': outcome.model_dump(mode='json')}
        channel.settimeout(1)
        send(channel, {**reply, 'proof': _proof(self.descriptor.auth_nonce, reply, 'reply')}, max_frame=OWNER_LIMIT)

    def close(self):
        self.stop.set()
        if self.channel is not None:
            self.channel.close()
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join()
        self.pool.shutdown(wait=True)
        if self.descriptor_identity is not None:
            try:
                if (_file(self.path) == self.descriptor_identity
                        and self.path.read_text() == self.descriptor.model_dump_json()):
                    self.path.unlink()
            except (OSError, ValueError):
                pass
        if self.socket_identity is not None:
            try:
                path = self.parent / 's'
                info = path.lstat()
                if (_root(self.parent) == self.parent_root and stat.S_ISSOCK(info.st_mode)
                        and (info.st_dev, info.st_ino) == self.socket_identity):
                    path.unlink()
            except (OSError, ValueError):
                pass
        if self.parent is not None:
            try:
                if self.parent_root is not None and _root(self.parent) == self.parent_root:
                    self.parent.rmdir()
            except OSError:
                pass
