"""Bounded JSON frames over host-created local sockets, without pickle."""

import json
import struct
import time

MAX_FRAME = 4 * 1024 * 1024


def send(channel, value: dict, *, max_frame=MAX_FRAME):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(data) > max_frame:
        raise ValueError('Worker frame exceeds the limit')
    channel.sendall(struct.pack('!I', len(data)) + data)


def _read(channel, length, deadline=None):
    data = bytearray()
    while len(data) < length:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Frame deadline expired')
            channel.settimeout(remaining)
        part = channel.recv(length - len(data))
        if not part:
            raise EOFError('Worker channel closed')
        data.extend(part)
    return bytes(data)


def receive(channel, *, max_frame=MAX_FRAME, deadline=None) -> dict:
    length = struct.unpack('!I', _read(channel, 4, deadline))[0]
    if not 0 < length <= max_frame:
        raise ValueError('Invalid worker frame length')
    value = json.loads(_read(channel, length, deadline))
    if not isinstance(value, dict):
        raise TypeError('Expected a worker object')
    return value
