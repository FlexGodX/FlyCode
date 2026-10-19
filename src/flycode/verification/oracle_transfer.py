"""Capture registered oracle bytes through owned handles before activation."""

import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..orchestration.sources import Sources
from ..orchestration.store import StoreError
from .oracle import ApprovedOracle
from .plan import contract_hash, registered_checks
from .registry import VerificationRegistry
from .runner import REPORT_LIMIT
from .snapshot import MAX_BYTES, MAX_FILES, digest, write_files


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _linked(parent, name, descriptor, expected):
    if (_identity(os.fstat(descriptor)) != expected
            or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected):
        raise ValueError('Oracle directory changed')


@contextmanager
def _directory(parent, name, *, create=False):
    if name in {'', '.', '..'} or '/' in name:
        raise ValueError('Invalid oracle directory')
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    expected = _identity(os.fstat(descriptor))
    try:
        _linked(parent, name, descriptor, expected)
        yield descriptor
        _linked(parent, name, descriptor, expected)
    finally:
        os.close(descriptor)


@contextmanager
def _opened(store, root, parts=(), *, create=False):
    from contextlib import ExitStack

    if Sources(store).prepare(Path(root.path)) != root:
        raise ValueError('Oracle root changed')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with ExitStack() as stack:
            current = descriptor
            for part in Path(root.path).parts[1:]:
                current = stack.enter_context(_directory(current, part))
            if (os.fstat(current).st_dev, os.fstat(current).st_ino) != root.identity:
                raise ValueError('Oracle root changed')
            for part in parts:
                current = stack.enter_context(_directory(current, part, create=create))
            yield current
            if Sources(store).prepare(Path(root.path)) != root:
                raise ValueError('Oracle root changed')
    finally:
        os.close(descriptor)


def _read(parent, name, limit):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('Oracle requires bounded private regular files')
        raw = stream.read(limit + 1)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns')
        for after in (os.fstat(stream.fileno()), os.stat(name, dir_fd=parent, follow_symlinks=False)):
            if any(getattr(before, field) != getattr(after, field) for field in fields):
                raise ValueError('Oracle file changed')
        if len(raw) != before.st_size or len(raw) > limit:
            raise ValueError('Oracle file changed')
        return raw, stat.S_IMODE(before.st_mode)


def _tree(files, modes):
    if set(files) != set(modes):
        raise ValueError('Oracle modes changed')
    result = {}
    for name, sha in files.items():
        path = PurePosixPath(name)
        if (not name or not path.parts or path.is_absolute() or str(path) != name or '..' in path.parts
                or not re.fullmatch('[0-9a-f]{64}', sha) or not 0 <= modes[name] <= 0o7777):
            raise ValueError('Invalid oracle file manifest')
        current = result
        for part in path.parts[:-1]:
            current = current.setdefault(part, {})
            if not isinstance(current, dict):
                raise ValueError('Conflicting oracle paths')  # noqa: TRY004 - parsed path overlap
        if path.name in current:
            raise ValueError('Conflicting oracle paths')
        current[path.name] = sha, modes[name]
    return result


def _entries(descriptor, allowed):
    present = set()
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if entry.name not in allowed:
                raise ValueError('Unexpected oracle entry')
            present.add(entry.name)
    return present


def _files(descriptor, hashes, modes, *, partial=False, limit=MAX_BYTES):
    tree, result, remaining = _tree(hashes, modes), {}, limit

    def visit(parent, expected, prefix=''):
        nonlocal remaining
        present = _entries(parent, expected)
        if not partial and present != set(expected):
            raise ValueError('Oracle files are missing')
        for name in present:
            wanted = expected[name]
            if isinstance(wanted, dict):
                with _directory(parent, name) as child:
                    visit(child, wanted, prefix + name + '/')
            else:
                raw, mode = _read(parent, name, remaining)
                if hashlib.sha256(raw).hexdigest() != wanted[0] or mode != wanted[1]:
                    raise ValueError('Oracle bytes or modes changed')
                remaining -= len(raw)
                result[prefix + name] = raw

    visit(descriptor, tree)
    return result


def _capture(parent, key):
    if not re.fullmatch('[0-9a-f]{64}', key):
        raise ValueError('Invalid oracle approval')
    with _directory(parent, key) as directory:
        if _entries(directory, {'approval.json', 'files'}) != {'approval.json', 'files'}:
            raise ValueError('Unexpected approval entry')
        raw, _ = _read(directory, 'approval.json', REPORT_LIMIT)
        approved = ApprovedOracle.model_validate_json(raw)
        if approved.fingerprint() != key or not 0 < len(approved.files) <= MAX_FILES:
            raise ValueError('Oracle approval changed')
        with _directory(directory, 'files') as inputs:
            files = _files(inputs, approved.files, approved.modes)
        if _read(directory, 'approval.json', REPORT_LIMIT)[0] != raw:
            raise ValueError('Oracle approval changed')
        return approved, files


def _heads(directory):
    try:
        raw, _ = _read(directory, 'heads.json', REPORT_LIMIT)
    except FileNotFoundError:
        return {}, None
    heads = json.loads(raw)
    if (not isinstance(heads, dict) or any(not isinstance(name, str)
            or not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key)
            for name, key in heads.items())):
        raise ValueError('Invalid oracle heads')
    return heads, raw


def capture_registered(registry, project, storage, profile):
    with _opened(registry.store, storage, ('oracles', project.id, profile.id)) as directory:
        heads, _ = _heads(directory)
        if heads.get(profile.draft.acceptance.id) != profile.approval_hash:
            raise StoreError('verification_profile_changed')
        approved, files = _capture(directory, profile.approval_hash)
    if (contract_hash(approved.contract) != contract_hash(profile.draft.acceptance)
            or digest([check.model_dump() for check in approved.checks])
            != digest([check.model_dump() for check in profile.draft.checks])):
        raise StoreError('verification_profile_changed')
    return approved, files


class OracleTransfer:
    def __init__(self, store):
        if store.host is None:
            raise StoreError('host_required')
        self.store = store
        self.runtime = store.host.registration.runtime_root

    def install(self, scope, profile):
        self.store._role(scope, ('owner',))
        self.store._host_guard()
        registry = VerificationRegistry(self.store)
        if (registry.profile(scope, profile.id) != profile or profile.approval_hash is None
                or profile.draft.acceptance is None):
            raise StoreError('verification_profile_changed')
        project = registry._project(scope, profile.project_id, owner=True)
        storage = registry._storage(project)
        approved, files = capture_registered(registry, project, storage, profile)
        if registry.profile(scope, profile.id) != profile:
            raise StoreError('verification_profile_changed')
        return self.import_captured(approved, files, expected_hash=profile.approval_hash)

    def import_captured(self, approved, files, *, expected_hash):
        approved = ApprovedOracle.model_validate(approved.model_dump())
        files = dict(files)
        registered_checks(approved.checks, approved.contract)
        key = approved.fingerprint()
        raw = approved.model_dump_json().encode()
        if (key != expected_hash or not 0 < len(files) <= MAX_FILES or len(raw) > REPORT_LIMIT
                or sum(len(value) for value in files.values()) > MAX_BYTES or set(files) != set(approved.files)
                or any(hashlib.sha256(value).hexdigest() != approved.files[name] for name, value in files.items())):
            raise ValueError('Captured oracle differs from the expected approval')
        _tree(approved.files, approved.modes)
        self.store._host_guard()
        with _opened(self.store, self.runtime, ('verification', 'oracles'), create=True) as directory:
            lock = os.open('owner-import.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                    raise ValueError('Invalid oracle import lock')
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._publish(directory, key, approved, files, raw)
            finally:
                os.close(lock)
        return key

    def _publish(self, directory, key, approved, files, raw):
        heads, previous_raw = _heads(directory)
        previous = heads.get(approved.contract.id)
        if previous is not None:
            old, _ = _capture(directory, previous)
            if old.contract.version > approved.contract.version or (old.contract.version == approved.contract.version
                    and contract_hash(old.contract) != contract_hash(approved.contract)):
                raise ValueError('Semantic contract changes require a newer owner version')
        with _directory(directory, key, create=True) as target:
            present = _entries(target, {'approval.json', 'files', '.pending'})
            if not present:
                write_files(target, {'.pending': key.encode()}, {'.pending': 0o600})
                present.add('.pending')
            if '.pending' in present:
                payloads = {**{'files/' + name: value for name, value in files.items()},
                    'approval.json': raw, '.pending': key.encode()}
                modes = {**{'files/' + name: mode for name, mode in approved.modes.items()},
                    'approval.json': 0o600, '.pending': 0o600}
                hashes = {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()}
                existing = _files(target, hashes, modes, partial=True, limit=MAX_BYTES + REPORT_LIMIT + 64)
                missing = {name: value for name, value in payloads.items() if name not in existing}
                write_files(target, missing, modes)
                _files(target, hashes, modes, limit=MAX_BYTES + REPORT_LIMIT + 64)
                os.unlink('.pending', dir_fd=target)
                os.fsync(target)
        saved, _ = _capture(directory, key)
        if saved != approved:
            raise ValueError('Saved oracle changed')
        if _heads(directory)[1] != previous_raw:
            raise ValueError('Oracle head changed concurrently')
        heads[approved.contract.id] = key
        temporary = '.heads-' + uuid4().hex
        content = json.dumps(heads, sort_keys=True).encode()
        write_files(directory, {temporary: content}, {temporary: 0o600})
        self.store._host_guard()
        os.replace(temporary, 'heads.json', src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
