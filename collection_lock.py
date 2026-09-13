"""One cooperating CLI per local collection directory, including after crashes."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
from typing import Iterator

LOCK_NAME = '.pokemoncards.lock'


class CollectionBusyError(OSError):
    """A different process currently owns this collection."""


@contextmanager
def collection_lock(directory: Path) -> Iterator[None]:
    """Keep the lock file: removing it could let processes lock different inodes."""
    path = directory / LOCK_NAME
    if path.is_symlink():
        raise OSError('Collection lock must not be a symbolic link')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        actual = os.fstat(fd)
        named = path.lstat()
        if not stat.S_ISREG(actual.st_mode) or not stat.S_ISREG(named.st_mode) \
                or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino):
            raise OSError('Collection lock must be a stable regular file')
        os.set_inheritable(fd, False)
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                # Windows supports locking one byte beyond an empty file's end.
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise CollectionBusyError('Collection already in use') from error
            raise
        yield
    finally:
        # Closing the descriptor releases the OS lock, including process exit.
        os.close(fd)
