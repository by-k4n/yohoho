"""Background daemon: pidfile single-instance lock, live state file, and the
daemon body run_daemon() — the single entry the future signed .app also calls."""
from __future__ import annotations
import os
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
    def __init__(self, data_dir: Path, *, alive=pid_alive) -> None:
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
        instance already holds it."""
        pid = self.read_pid()
        if pid is not None and self._alive(pid):
            return False
        if pid is not None:               # dead PID present -> prior crash
            self.crashed_prior_run = True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(str(os.getpid()))
        return True

    def release(self) -> None:
        try:
            if self.read_pid() == os.getpid():
                self._path.unlink()
        except FileNotFoundError:
            pass
