"""Preserve actual displaced objects; never replace/unlink a user's file blindly."""

import hashlib
import os
import stat
from pathlib import Path

from ..domain.results import (
    ApplicationJournal,
    ApplicationOperation,
    FileChange,
    FileVersion,
)
from ..verification.evidence import atomic_json
from .rename import Rename, directory
from .store import StoreError, changed
from .tools import _path


def version(path: Path) -> FileVersion | None:
    if not path.exists() and not path.is_symlink():
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StoreError('integration_conflict', 'Linked or special output path')
        digest = hashlib.sha256()
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
        after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_mode) != (after.st_size, after.st_mtime_ns, after.st_mode):
            raise StoreError('integration_conflict', 'Output changed while reading')
        return FileVersion(sha256=digest.hexdigest(), mode=stat.S_IMODE(info.st_mode))


def _identity(path: Path):
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IMODE(info.st_mode)


class Application:
    def __init__(self, directory: Path, source: Path, baseline: Path, candidate: Path):
        self.directory, self.source, self.baseline, self.candidate = directory, source, baseline, candidate
        self.path = directory / 'application.json'
        self.recovery = (directory / 'recovery' if directory.stat().st_dev == source.stat().st_dev
            else source.parent / ('.flycode-recovery-' + directory.name))
        if self.recovery.is_relative_to(source):
            raise StoreError('integration_scope', 'Recovery must be outside the subject')
        self.rename = Rename()

    def _save(self, journal):
        atomic_json(self.path, journal.model_dump_json())
        with self.path.open('rb') as stream:
            os.fsync(stream.fileno())
        with directory(self.directory) as descriptor:
            os.fsync(descriptor)

    def _journal(self, expected):
        journal = ApplicationJournal.model_validate_json(self.path.read_text())
        if (journal.changes != expected or len(journal.operations) > len(expected)
                or tuple(item.path for item in journal.operations) != tuple(item.path for item in expected[:len(journal.operations)])):
            raise StoreError('integration_integrity', 'Application journal changed')
        return journal

    def apply(self, changes: tuple[FileChange, ...], guard):
        if self.path.exists():
            raise StoreError('reconcile_required', 'An application journal cannot be replayed')
        if len({item.path for item in changes}) != len(changes):
            raise StoreError('integration_integrity')
        self.recovery.mkdir(mode=0o700)
        with directory(self.recovery.parent) as descriptor:
            os.fsync(descriptor)
        journal = ApplicationJournal(state='applying', changes=changes)
        self._save(journal)
        for index, change in enumerate(changes):
            guard()
            _path(self.source, change.path)
            target, slot = self.source / change.path, self.recovery / str(index)
            if version(target) != change.before:
                raise StoreError('integration_conflict', 'Source changed before installation')
            original = _identity(target) if change.before is not None else None
            installed = None
            if change.after is not None:
                data = (self.candidate / change.path).read_bytes()
                if hashlib.sha256(data).hexdigest() != change.after.sha256:
                    raise StoreError('integration_integrity')
                with slot.open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchmod(stream.fileno(), change.after.mode)
                    os.fsync(stream.fileno())
                installed = _identity(slot)
                with directory(target.parent, create=True):
                    pass
            operation = ApplicationOperation(path=change.path, before_identity=original, identity=installed)
            journal = changed(journal, operations=(*journal.operations, operation))
            self._save(journal)
            guard()
            _path(self.source, change.path)
            if version(target) != change.before:
                raise StoreError('integration_conflict', 'Source changed during preparation')
            if change.after is None:
                self.rename.move(target, slot)
            else:
                self.rename.move(slot, target, exchange=change.before is not None)
            if change.before is not None and (version(slot) != change.before or _identity(slot) != original):
                raise StoreError('integration_conflict', 'The displaced source object changed')
        self._save(changed(journal, state='applied'))
        if not self.current(changes):
            raise StoreError('integration_conflict', 'Source or retained original changed')

    def current(self, expected: tuple[FileChange, ...]) -> bool:
        try:
            journal = self._journal(expected)
            if journal.state != 'applied' or len(journal.operations) != len(expected):
                return False
            for index, (change, operation) in enumerate(zip(expected, journal.operations)):
                target, slot = self.source / change.path, self.recovery / str(index)
                _path(self.source, change.path)
                if version(target) != change.after or version(slot) != change.before:
                    return False
                if change.after is not None and _identity(target) != operation.identity:
                    return False
                if change.before is not None and _identity(slot) != operation.before_identity:
                    return False
            return True
        except (OSError, ValueError):
            return False

    def rollback(self, expected: tuple[FileChange, ...]) -> bool:
        if not self.path.exists():
            return True
        journal = self._journal(expected)
        clean = True
        for index in reversed(range(len(journal.operations))):
            operation, change = journal.operations[index], expected[index]
            target, slot = self.source / change.path, self.recovery / str(index)
            try:
                _path(self.source, change.path)
                current, saved = version(target), version(slot)
                # An intent may be durable before the native rename happens.
                if change.before is not None and current == change.before and _identity(target) == operation.before_identity:
                    continue
                if change.before is None and current is None:
                    continue
                if change.after is None:
                    if current is not None or saved is None:
                        clean = False
                        continue
                    self.rename.move(slot, target)
                elif current == change.after and _identity(target) == operation.identity:
                    if change.before is None:
                        self.rename.move(target, slot)
                        if version(slot) != change.after or _identity(slot) != operation.identity:
                            self.rename.move(slot, target)
                            clean = False
                    elif saved is not None:
                        restored = _identity(slot)
                        self.rename.move(slot, target, exchange=True)
                        if version(slot) != change.after or _identity(slot) != operation.identity:
                            # Preserve the object captured during rollback too.
                            if _identity(target) == restored:
                                self.rename.move(slot, target, exchange=True)
                            clean = False
                    else:
                        clean = False
                else:
                    clean = False
            except (OSError, ValueError):
                clean = False
        self._save(changed(journal, state='rolled_back' if clean else 'reconciling'))
        # Recovery objects are intentionally retained: old open descriptors may
        # still receive user writes after a rename, including after rollback.
        return clean
