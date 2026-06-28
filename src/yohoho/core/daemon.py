"""Background daemon: pidfile single-instance lock, live state file, and the
daemon body run_daemon() — the single entry the future signed .app also calls."""
from __future__ import annotations
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

_PID_NAME = "yohoho.pid"

# Exit codes for run_daemon's return value (used by the _run-daemon CLI subcommand).
EXIT_ALREADY_RUNNING = 1


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


_STATE_NAME = "state.json"


class StateWriter:
    def __init__(self, data_dir, *, hotkey: str, started_at: str) -> None:
        self._path = Path(data_dir) / _STATE_NAME
        self._base = {"pid": os.getpid(), "hotkey": hotkey, "started_at": started_at}

    def set(self, state: str) -> None:
        payload = {**self._base, "state": state}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Timestamp helper (mirrors observability._utc_now_iso for consistent format)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Daemon body
# ---------------------------------------------------------------------------


def run_daemon(data_dir, *, now: "Callable[[], str] | None" = None) -> int:
    """Acquire the pidfile, run the main loop, and clean up on exit.

    This is the single entry point the hidden ``_run-daemon`` CLI subcommand
    (T8) and the future signed ``.app`` both call.

    Returns:
        0              — clean run and exit.
        EXIT_ALREADY_RUNNING — a live instance already holds the pidfile;
                         the loop is NOT called and no markers are touched.
    """
    if now is None:
        now = _utc_now_iso

    data_dir = Path(data_dir)
    pidfile = PidFile(data_dir)
    if not pidfile.acquire():
        # Do NOT enter the try/finally — never release someone else's pidfile.
        return EXIT_ALREADY_RUNNING

    # Lazy imports keep daemon.py module-level imports cheap (no Tk, no heavy deps).
    from yohoho.core.observability import (  # noqa: PLC0415
        mark_running,
        mark_clean_shutdown,
        record_error as _record_error,
    )
    from yohoho.core.config import load_config  # noqa: PLC0415

    state: StateWriter | None = None
    try:
        mark_running(data_dir)
        cfg = load_config(data_dir / "config.yaml")
        state = StateWriter(data_dir, hotkey=cfg.hotkey, started_at=now())
        # Build a bound record_error callback for the loop's error hooks.
        def rec(code: str, message: str) -> None:
            _record_error(data_dir, code=code, message=message)

        from yohoho.core.run_loop import run_start_loop  # noqa: PLC0415
        run_start_loop(data_dir, state_writer=state, record_error=rec)
    finally:
        mark_clean_shutdown(data_dir)
        if state is not None:
            try:
                state.clear()
            except OSError:
                pass
        pidfile.release()

    return 0
