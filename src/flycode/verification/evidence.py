"""Atomic manifests, bounded redacted observations and checksum validation."""

import hashlib
import html
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from .runner import OUTPUT_LIMIT, REPORT_LIMIT
from .snapshot import MAX_BYTES, secret_values


def atomic_json(path: Path, content: str) -> None:
    temporary = path.with_suffix('.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(content)
        stream.flush()
    temporary.replace(path)


@contextmanager
def _regular(path: Path, *, writable=False):
    descriptor = os.open(path, (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r+b' if writable else 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Evidence must be a private regular file')
        yield stream


def read_regular(path: Path, *, limit=MAX_BYTES) -> bytes:
    with _regular(path) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > limit:
            raise ValueError('Evidence exceeds its read limit')
        data = stream.read(limit + 1)
        for after in (os.fstat(stream.fileno()), path.lstat()):
            if any(getattr(before, field) != getattr(after, field) for field in (
                    'st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns')):
                raise ValueError('Evidence changed during reading')
        if len(data) > limit:
            raise ValueError('Evidence exceeds its read limit')
        return data


def metadata_matches(directory: Path, expected: dict[str, str]) -> bool:
    return all(read_regular(directory / name, limit=len(content.encode('utf-8'))) == content.encode('utf-8')
        for name, content in expected.items())


def sanitize_outputs(output: Path) -> bool:
    changed = False
    for name in ('stdout.txt', 'stderr.txt', 'junit.xml', 'cases.jsonl'):
        path = output / name
        if not path.exists() and not path.is_symlink():
            continue
        limit = OUTPUT_LIMIT if name.endswith('.txt') else REPORT_LIMIT
        with _regular(path, writable=True) as stream:
            data = stream.read(limit)
            original = data
            for value in secret_values():
                data = data.replace(value, b'[REDACTED]').replace(html.escape(value.decode()).encode(), b'[REDACTED]')
            data = re.sub(rb'''(?i)((?:api[_-]?key|password|token|secret)\s*[:=]\s*["']?)[^\s<>"']+''',
                          rb'\1[REDACTED]', data)
            changed |= data != original
            stream.seek(0)
            stream.write(data)
            stream.truncate()
    return changed


def artifact_hashes(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob('*')) if path.is_file() and not path.is_symlink()}
