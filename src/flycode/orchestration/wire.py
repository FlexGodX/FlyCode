"""Bounded JSON frames over host-created local sockets, without pickle."""

import json
import struct

MAX_FRAME = 4 * 1024 * 1024


def send(channel, value: dict):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(data) > MAX_FRAME:
        raise ValueError('Worker frame exceeds the limit')
    channel.sendall(struct.pack('!I', len(data)) + data)


def _read(channel, length):
    data = bytearray()
    while len(data) < length:
        part = channel.recv(length - len(data))
        if not part:
            raise EOFError('Worker channel closed')
        data.extend(part)
    return bytes(data)


def receive(channel) -> dict:
    length = struct.unpack('!I', _read(channel, 4))[0]
    if not 0 < length <= MAX_FRAME:
        raise ValueError('Invalid worker frame length')
    value = json.loads(_read(channel, length))
    if not isinstance(value, dict):
        raise TypeError('Expected a worker object')
    return value
