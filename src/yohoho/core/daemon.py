"""Background daemon: pidfile single-instance lock, live state file, and the
daemon body run_daemon() — the single entry the future signed .app also calls."""
from __future__ import annotations
import os
from collections.abc import Callable
from pathlib import Path

_PID_NAME = "yohoho.pid"


def pid_alive(pid: int) -> bool:
    """True if a process with this pid exists. POSIX impl; Windows overrides via
    WindowsProcessController.is_alive (see platform/windows/process.py)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


class PidFile:
    def __init__(self, data_dir: Path, *, alive: Callable[[int], bool] = pid_alive) -> None:
        self._path = Path(data_dir) / _PID_NAME
        self._alive = alive
        self.crashed_prior_run = False

    def read_pid(self) -> int | None:
        try:
            return int(self._path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def is_running(self) -> bool:
        pid = self.read_pid()
        return pid is not None and self._alive(pid)

    def acquire(self) -> bool:
        """Write our PID iff no live process holds the file. A stale (dead-PID)
        file is reclaimed and flags crashed_prior_run. Returns False if a live
        instance already holds it. Uses an exclusive create (O_CREAT|O_EXCL) so
        the lock is atomic against a concurrent acquirer (no read->check->write
        TOCTOU race)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                pid = self.read_pid()
                if pid is not None and self._alive(pid):
                    return False
                # dead PID present -> prior crash; reclaim and retry once
                self.crashed_prior_run = True
                try:
                    self._path.unlink()
                except FileNotFoundError:
                    pass
                continue
            else:
                with os.fdopen(fd, "w") as f:
                    f.write(str(os.getpid()))
                return True
        return False

    def release(self) -> None:
        try:
            if self.read_pid() == os.getpid():
                self._path.unlink()
        except FileNotFoundError:
            pass
