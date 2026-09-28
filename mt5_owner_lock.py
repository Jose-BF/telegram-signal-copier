"""OS-held ownership survives parent death and is released on worker death."""

from __future__ import annotations

import errno
import os
from pathlib import Path


class OwnerBusyError(RuntimeError):
    pass


def native_owner_lock_path() -> Path:
    # Shared across checkouts and Windows logon sessions for this OS user.
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "TelegramSignalCopier" / "native-owner.lock"


class OwnerLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fd = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise RuntimeError("owner lock already held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise OwnerBusyError("MT5 owner is still active") from exc
            raise
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)
        # Never unlink: a second inode would permit a second owner.
