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

    descriptor = os.open(Path(ref.directory) / 'process.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        _identity(descriptor, ref)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = Path(ref.directory) / 'process.json'
        if path.is_symlink():
            raise ValueError('Process receipt link is unsupported')
        receipt = ProcessReceipt.model_validate_json(path.read_text())
        if receipt.ref != ref or receipt.state != 'stopped' or not receipt.stop_confirmed:
            raise ValueError('Process group stop remains unconfirmed')
        yield receipt
    finally:
        os.close(descriptor)


def run(argv, sandbox, environment, stdout, stderr, output, timeout, should_stop, on_state, on_admit):
    import fcntl

    descriptor = os.open(output / 'process.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    parent, child = socket.socketpair()
    supervisor = None
    admitted = False
    receipt = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request = {'argv': argv, 'sandbox': str(sandbox), 'environment': environment, 'timeout': timeout}
        info = os.fstat(descriptor)
        ref = ProcessRef(id=uuid4().hex, directory=str(output.resolve()), device=info.st_dev, inode=info.st_ino,
            request_hash=hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest())
        receipt = ProcessReceipt(ref=ref)
        _write(receipt)
        launch = output / 'process-launch.json'
        launch.write_text(json.dumps({'request': request, 'ref': ref.model_dump(mode='json')}))
        environment = {**environment, 'PYTHONPATH': str(Path(__file__).resolve().parents[2]), 'PYTHONNOUSERSITE': '1'}
        supervisor = subprocess.Popen([sys.executable, '-m', 'flycode.verification.custody', str(launch),
            str(child.fileno()), str(descriptor), str(stdout.fileno()), str(stderr.fileno())],
            cwd=Path(__file__).parent, env=environment, start_new_session=True,
            pass_fds=(child.fileno(), descriptor, stdout.fileno(), stderr.fileno()),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child.close()
        parent.settimeout(5)
        if wire.receive(parent) != {'ready': ref.id}:
            raise ValueError('Unexpected process supervisor acknowledgement')
        if should_stop():
            wire.send(parent, {'cancel': ref.id})
        else:
            _write(receipt.model_copy(update={'state': 'admitted'}))
            on_admit(ref)
            wire.send(parent, {'go': ref.id})
            admitted = True
            if wire.receive(parent) != {'running': ref.id}:
                raise ValueError('Unexpected process start acknowledgement')
            on_state('running')
            stopping = False
            while supervisor.poll() is None:
                if should_stop() and not stopping:
                    try:
                        parent.sendall(b'S')
                    except BrokenPipeError:
                        break
                    stopping = True
                time.sleep(.05)
    finally:
        parent.close()
        child.close()
        try:
            if supervisor is not None:
                supervisor.wait(timeout=20)
            elif not admitted and receipt is not None:
                _write(receipt.model_copy(update={'state': 'stopped', 'stop_confirmed': True, 'reason': 'never_started'}))
        finally:
            # LOCK_UN would also unlock the supervisor's inherited description.
            os.close(descriptor)
    with stopped(ref) as receipt:
        return receipt


def supervise(launch, channel, descriptor, stdout, stderr):
    from .runner import OUTPUT_LIMIT, REPORT_LIMIT, _terminate

    config = json.loads(Path(launch).read_text())
    ref = ProcessRef.model_validate(config['ref'])
    request = config['request']
    _identity(descriptor, ref)
    if hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest() != ref.request_hash:
        raise ValueError('Process launch changed')
    receipt = ProcessReceipt(ref=ref)
    process = None
    reason = 'coordinator_lost'
    try:
        wire.send(channel, {'ready': ref.id})
        command = wire.receive(channel)
        if command == {'cancel': ref.id}:
            reason = 'cancelled'
            return
        if command != {'go': ref.id}:
            raise ValueError('Process start was not admitted')
        current = ProcessReceipt.model_validate_json((Path(ref.directory) / 'process.json').read_text())
        if current.ref != ref or current.state != 'admitted':
            raise ValueError('Durable process admission is missing')
        process = subprocess.Popen(request['argv'], cwd=request['sandbox'], env=request['environment'],
            stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True)
        receipt = receipt.model_copy(update={'state': 'admitted', 'started': True})
        _write(receipt)
        wire.send(channel, {'running': ref.id})
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
        channel.close()
        os.close(descriptor)


if __name__ == '__main__':
    supervise(sys.argv[1], socket.socket(fileno=int(sys.argv[2])), *map(int, sys.argv[3:]))
