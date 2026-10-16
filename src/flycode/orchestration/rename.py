"""Native atomic exchange/exclusive rename with anchored, no-follow directories."""

import ctypes
import errno
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .store import StoreError


@contextmanager
def directory(path: Path, *, create=False):
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


class Rename:
    def __init__(self):
        library = ctypes.CDLL(None, use_errno=True)
        if sys.platform == 'darwin':
            name, self.exchange, self.exclusive = 'renameatx_np', 2, 4
        elif sys.platform.startswith('linux'):
            name, self.exchange, self.exclusive = 'renameat2', 2, 1
        else:
            raise StoreError('unsupported_atomic_apply')
        try:
            self.function = getattr(library, name)
        except AttributeError as error:
            raise StoreError('unsupported_atomic_apply') from error
        self.function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        self.function.restype = ctypes.c_int

    def move(self, source: Path, target: Path, *, exchange=False):
        with directory(source.parent) as old, directory(target.parent) as new:
            flags = self.exchange if exchange else self.exclusive
            if self.function(old, os.fsencode(source.name), new, os.fsencode(target.name), flags):
                code = ctypes.get_errno()
                if code in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EXDEV}:
                    raise StoreError('unsupported_atomic_apply', os.strerror(code))
                raise OSError(code, os.strerror(code), str(target))
            os.fsync(old)
            os.fsync(new)
