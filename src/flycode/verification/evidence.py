"""Atomic manifests, bounded redacted observations and checksum validation."""

import hashlib
import html
import re
from pathlib import Path

from .runner import OUTPUT_LIMIT, REPORT_LIMIT
from .snapshot import secret_values


def atomic_json(path: Path, content: str) -> None:
    temporary = path.with_suffix('.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(content)
        stream.flush()
    temporary.replace(path)


def sanitize_outputs(output: Path) -> bool:
    changed = False
    for name in ('stdout.txt', 'stderr.txt', 'junit.xml', 'cases.jsonl'):
        path = output / name
        if not path.exists():
            continue
        limit = OUTPUT_LIMIT if name.endswith('.txt') else REPORT_LIMIT
        with path.open('rb') as stream:
            data = stream.read(limit)
        original = data
        for value in secret_values():
            data = data.replace(value, b'[REDACTED]').replace(html.escape(value.decode()).encode(), b'[REDACTED]')
        data = re.sub(rb'''(?i)((?:api[_-]?key|password|token|secret)\s*[:=]\s*["']?)[^\s<>"']+''',
                      rb'\1[REDACTED]', data)
        changed |= data != original
        path.write_bytes(data)
    return changed


def artifact_hashes(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob('*')) if path.is_file() and not path.is_symlink()}
