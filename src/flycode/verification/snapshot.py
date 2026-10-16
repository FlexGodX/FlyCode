"""Copy allowed input bytes without following links or copying credentials."""

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import subprocess
from pathlib import Path

from ..domain.verification import SubjectRef, SubjectSnapshot
from ..research.identity import implementation_fingerprint

EXCLUDED_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache',
                 '.ruff_cache', '.aws', '.ssh', '.home'}
MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 10000


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def environment_identity() -> dict:
    packages = sorted((dist.metadata['Name'], dist.version) for dist in importlib.metadata.distributions()
                      if dist.metadata['Name'])
    return {'python': platform.python_version(), 'platform': platform.platform(), 'packages': packages,
            'implementation_fingerprint': implementation_fingerprint()}


def secret_values() -> list[bytes]:
    return [value.encode() for name, value in os.environ.items() if len(value) >= 8
            and any(part in name.upper() for part in ('PASSWORD', 'SECRET', 'TOKEN', 'API_KEY'))]


def input_files(root: Path) -> tuple[dict[str, bytes], list[str]]:
    files: dict[str, bytes] = {}
    excluded: list[str] = []
    total = 0
    secrets = secret_values()
    for directory, dirs, names in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in list(dirs):
            path = current / name
            if path.is_symlink():
                raise ValueError(f'symlink input is unsupported: {path.relative_to(root)}')
            if name in EXCLUDED_DIRS:
                excluded.append(str(path.relative_to(root)))
                dirs.remove(name)
        for name in sorted(names):
            path = current / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'symlink or special input is unsupported: {relative}')
            if (name in {'.git', '.env'} or name.startswith('.env.') or name.endswith(('.pem', '.key', '.pyc'))
                    or name in {'id_rsa', 'id_ed25519', 'credentials.json'}):
                excluded.append(relative)
                continue
            if path.stat().st_size > MAX_BYTES - total or len(files) >= MAX_FILES:
                raise ValueError('Snapshot exceeds the 512MiB/10000-file limit')
            with path.open('rb') as stream:
                data = stream.read(MAX_BYTES - total + 1)
            if len(data) > MAX_BYTES - total:
                raise ValueError('Snapshot exceeds the 512MiB/10000-file limit')
            total += len(data)
            pem = re.search(rb'-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----(?:\r?\n|\\n)[A-Za-z0-9+/=]{32,}', data)
            if any(value in data for value in secrets) or pem is not None:
                raise ValueError(f'Credential or oversized input excluded from verification: {relative}')
            files[relative] = data
    return files, sorted(excluded)


def capture(root: Path, destination: Path | None = None, *, ref: SubjectRef | None = None) -> SubjectSnapshot:
    root = root.resolve(strict=True)
    if destination is not None and destination.resolve().is_relative_to(root):
        raise ValueError('Snapshot storage must be outside the source project')
    files, excluded = input_files(root)
    try:
        base_result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                     check=False, capture_output=True, timeout=5)
        base = base_result.stdout.decode().strip() if base_result.returncode == 0 else None
        prefix_result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-prefix'],
                                       check=False, capture_output=True, timeout=5)
        prefix = prefix_result.stdout.decode(errors='replace').removesuffix('\n')
        status = subprocess.run(['git', '--no-optional-locks', '-C', str(root), 'status', '--porcelain=v1', '-z',
                                 '--untracked-files=all', '--', '.'],
                                check=False, capture_output=True, timeout=5)
        inventory = []
        if status.returncode == 0 and prefix_result.returncode == 0:
            records = iter(status.stdout.decode(errors='replace').split('\0'))
            for record in records:
                if not record:
                    continue
                code, name = record[:2], record[3:]
                origin = next(records) if 'R' in code or 'C' in code else None
                if name.startswith(prefix):
                    relative = name[len(prefix):]
                    if origin is not None and not origin.startswith(prefix):
                        inventory.append('A  ' + relative)
                    else:
                        inventory.append(code + ' ' + relative)
                        if origin is not None:
                            inventory.append(origin[len(prefix):])
                elif origin is not None and origin.startswith(prefix):
                    inventory.append('D  ' + origin[len(prefix):])
        else:
            inventory = list(files)
    except (OSError, subprocess.TimeoutExpired):
        base, inventory = None, list(files)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    modes = {name: stat.S_IMODE((root / name).stat().st_mode) for name in files}
    environment = environment_identity()
    identity = {'files': hashes, 'modes': modes, 'base': base, 'environment': environment}
    if ref is not None:
        identity['ref'] = ref.model_dump()
    snapshot = SubjectSnapshot(subject_hash=digest(identity), files=hashes, modes=modes,
                               excluded=excluded, base=base, inventory=inventory, environment=environment, ref=ref)
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=False)
        for name, data in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(modes[name])
    return snapshot
