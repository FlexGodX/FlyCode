"""Fingerprint the implementation that a scoped experiment actually exercises."""

import hashlib
from pathlib import Path


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    files = [root / 'agent.py', root / 'cli.py']
    for package in ('brain', 'cognition', 'domain', 'policy', 'safety', 'llm', 'environment', 'tools', 'research'):
        files.extend((root / package).rglob('*.py'))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()
