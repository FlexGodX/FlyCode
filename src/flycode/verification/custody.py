"""An inherited lock and private channel retain custody across coordinator death."""

import hashlib
import json
import os
import select
import socket
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..domain.verification import ProcessReceipt, ProcessRef
from ..orchestration import wire


def supported():
    return os.name == 'posix'


def _write(receipt):
    path = Path(receipt.ref.directory) / 'process.json'
    temporary = path.with_suffix('.tmp')
    with temporary.open('x') as stream:
        stream.write(receipt.model_dump_json())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity(descriptor, ref):
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (ref.device, ref.inode)):
        raise ValueError('Process ownership object changed')


@contextmanager
def locked(path: Path, *, identity=None, create=False):
    import fcntl

    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (identity is not None and (info.st_dev, info.st_ino) != identity)):
            raise ValueError('Coordinator ownership object changed')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def stopped(ref: ProcessRef):
    """Hold exclusive custody while reconciling; a PID never authorizes signals."""
    import fcntl

    from .evidence import read_regular

    path = Path(ref.directory) / 'process.lock'
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _identity(descriptor, ref)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = ProcessReceipt.model_validate_json(read_regular(Path(ref.directory) / 'process.json', limit=64 * 1024))
        if receipt.ref != ref or receipt.state != 'stopped' or not receipt.stop_confirmed:
            raise ValueError('Process group stop remains unconfirmed')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_dev, info.st_ino) != (ref.device, ref.inode):
            raise ValueError('Process ownership object changed')
        yield receipt
    finally:
        os.close(descriptor)


class Launch:
    """Owned guardian control; only finish() returns independently validated custody."""
    def __init__(self, parent, descriptor, supervisor, ref, pid):
        self.parent, self.descriptor, self.supervisor, self.ref = parent, descriptor, supervisor, ref
        self.pid, self.receipt, self.cancelling = pid, None, False

    def cancel(self):
        if not self.cancelling and self.receipt is None:
            try:
                self.parent.sendall(b'S')
            except OSError:
                pass
            self.cancelling = True

    def finish(self):
        if self.receipt is not None:
            return self.receipt
        self.parent.close()
        try:
            self.supervisor.wait(timeout=20)
        finally:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None
        deadline = time.monotonic() + 5
        while True:
            try:
                with stopped(self.ref) as receipt:
                    self.receipt = receipt
                    return receipt
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)


def launch(argv, sandbox, environment, stdout, stderr, output, timeout, should_stop, on_state, on_admit,
           *, on_prepare=lambda ref: None, version='pytest-custody-v1', pass_fds=()):
    import fcntl

    descriptor = os.open(output / 'process.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    parent = child = None
    supervisor = None
    admitted = False
    receipt = None
    try:
        parent, child = socket.socketpair()
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request = {'argv': argv, 'sandbox': str(sandbox), 'environment': environment, 'timeout': timeout}
        if pass_fds:
            if version != 'worker-custody-v1' or len(pass_fds) != 2 or len(set(pass_fds)) != 2:
                raise ValueError('Worker custody requires exactly two distinct socket descriptors')
            request['pass_fds'] = {}
            for fd in pass_fds:
                info = os.fstat(fd)
                if not stat.S_ISSOCK(info.st_mode):
                    raise ValueError('Worker descriptor is not a socket')
                request['pass_fds'][str(fd)] = [info.st_dev, info.st_ino, info.st_mode]
        info = os.fstat(descriptor)
        ref = ProcessRef(version=version, id=uuid4().hex, directory=str(output.resolve()), device=info.st_dev, inode=info.st_ino,
            request_hash=hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest())
        receipt = ProcessReceipt(ref=ref)
        _write(receipt)
        launch_path = output / 'process-launch.json'
        launch_path.write_text(json.dumps({'request': request, 'ref': ref.model_dump(mode='json')}))
        on_prepare(ref)
        environment = {**environment, 'PYTHONPATH': str(Path(__file__).resolve().parents[2]), 'PYTHONNOUSERSITE': '1'}
        supervisor = subprocess.Popen([sys.executable, '-m', 'flycode.verification.custody', str(launch_path),
            str(child.fileno()), str(descriptor), str(stdout.fileno()), str(stderr.fileno())],
            cwd=Path(__file__).parent, env=environment, start_new_session=True,
            pass_fds=(child.fileno(), descriptor, stdout.fileno(), stderr.fileno(), *pass_fds),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child.close()
        parent.settimeout(5)
        if wire.receive(parent) != {'ready': ref.id}:
            raise ValueError('Unexpected process supervisor acknowledgement')
        pid = None
        if should_stop():
            wire.send(parent, {'cancel': ref.id})
        else:
            _write(receipt.model_copy(update={'state': 'admitted'}))
            on_admit(ref)
            wire.send(parent, {'go': ref.id})
            admitted = True
            message = wire.receive(parent)
            if version == 'worker-custody-v1':
                if (set(message) != {'running', 'pid'} or message['running'] != ref.id
                        or type(message['pid']) is not int or message['pid'] <= 0):
                    raise ValueError('Unexpected worker start acknowledgement')
                pid = message['pid']
            elif message != {'running': ref.id}:
                raise ValueError('Unexpected process start acknowledgement')
            on_state('running')
        return Launch(parent, descriptor, supervisor, ref, pid)
    except BaseException:
        if parent is not None:
            parent.close()
        try:
            if supervisor is not None:
                supervisor.wait(timeout=20)
            elif not admitted and receipt is not None:
                _write(receipt.model_copy(update={'state': 'stopped', 'stop_confirmed': True, 'reason': 'never_started'}))
        finally:
            os.close(descriptor)
        raise
    finally:
        if child is not None:
            child.close()


def run(argv, sandbox, environment, stdout, stderr, output, timeout, should_stop, on_state, on_admit,
        *, on_prepare=lambda ref: None, version='pytest-custody-v1'):
    owned = launch(argv, sandbox, environment, stdout, stderr, output, timeout, should_stop, on_state, on_admit,
        on_prepare=on_prepare, version=version)
    try:
        while owned.supervisor.poll() is None:
            if should_stop():
                owned.cancel()
            time.sleep(.05)
    finally:
        receipt = owned.finish()
    return receipt


def supervise(launch, channel, descriptor, stdout, stderr):
    from .evidence import read_regular
    from .runner import OUTPUT_LIMIT, REPORT_LIMIT, _terminate

    config = json.loads(read_regular(Path(launch), limit=REPORT_LIMIT))
    ref = ProcessRef.model_validate(config['ref'])
    request = config['request']
    _identity(descriptor, ref)
    if hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest() != ref.request_hash:
        raise ValueError('Process launch changed')
    receipt = ProcessReceipt(ref=ref)
    process = None
    forwarded = tuple(int(fd) for fd in request.get('pass_fds', {}))
    reason = 'coordinator_lost'
    try:
        wire.send(channel, {'ready': ref.id})
        command = wire.receive(channel)
        if command == {'cancel': ref.id}:
            reason = 'cancelled'
            return
        if command != {'go': ref.id}:
            raise ValueError('Process start was not admitted')
        current = ProcessReceipt.model_validate_json(read_regular(Path(ref.directory) / 'process.json', limit=64 * 1024))
        if current.ref != ref or current.state != 'admitted':
            raise ValueError('Durable process admission is missing')
        for fd in forwarded:
            info = os.fstat(fd)
            if [info.st_dev, info.st_ino, info.st_mode] != request['pass_fds'][str(fd)]:
                raise ValueError('Forwarded worker descriptor changed')
        process = subprocess.Popen(request['argv'], cwd=request['sandbox'], env=request['environment'],
            stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True, pass_fds=forwarded)
        for fd in forwarded:
            os.close(fd)
        forwarded = ()
        receipt = receipt.model_copy(update={'state': 'admitted', 'started': True,
            **({'pid': process.pid} if ref.version == 'worker-custody-v1' else {})})
        _write(receipt)
        wire.send(channel, {'running': ref.id, **({'pid': process.pid} if ref.version == 'worker-custody-v1' else {})})
        started = time.monotonic()
        while process.poll() is None:
            if select.select([channel], [], [], .05)[0]:
                reason = 'cancelled' if channel.recv(1) == b'S' else 'coordinator_lost'
                break
            if time.monotonic() - started >= request['timeout']:
                reason = 'timeout'
                break
            if any((Path(ref.directory) / name).exists() and (Path(ref.directory) / name).stat().st_size > limit
                    for name, limit in [('stdout.txt', OUTPUT_LIMIT), ('stderr.txt', OUTPUT_LIMIT),
                                        ('junit.xml', REPORT_LIMIT), ('cases.jsonl', REPORT_LIMIT)]):
                reason = 'output_limit'
                break
        else:
            reason = 'completed'
    except (EOFError, BrokenPipeError, ConnectionResetError):
        reason = 'coordinator_lost'
    except (OSError, ValueError):
        reason = 'launch_failed'
    finally:
        try:
            if process is not None:
                terminated = _terminate(process)
                if reason == 'completed' and terminated:
                    reason = 'descendants_terminated'
            receipt = receipt.model_copy(update={'state': 'stopped', 'stop_confirmed': True,
                'exit_code': process.returncode if process is not None else None, 'reason': reason})
        except (OSError, subprocess.SubprocessError):
            receipt = receipt.model_copy(update={'state': 'uncertain', 'reason': 'cleanup'})
        _write(receipt)
        for fd in forwarded:
            os.close(fd)
        channel.close()
        os.close(descriptor)


if __name__ == '__main__':
    supervise(sys.argv[1], socket.socket(fileno=int(sys.argv[2])), *map(int, sys.argv[3:]))
