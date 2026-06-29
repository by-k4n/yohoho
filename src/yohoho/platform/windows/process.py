"""Windows ProcessController: spawn a no-console detached daemon, query liveness, stop gracefully.

pywin32 (win32api / win32con / win32process / win32console) is imported **lazily inside each
method** so this module imports cleanly on macOS/Linux (pytest collection, CI).  Only stdlib
modules (subprocess, time, sys, pathlib) and core yohoho modules may appear at module top.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from collections.abc import Sequence

from yohoho.core.config import data_dir

# Windows process-access right constants inlined here so win32con is not needed at module top.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000  # REQUIRED to WaitForSingleObject on the process handle (QUERY_LIMITED alone
                           # opens the handle but the wait then fails with ERROR_ACCESS_DENIED).
_ERROR_ACCESS_DENIED = 5   # OpenProcess fails with this → process exists, we lack rights → alive
_ERROR_INVALID_PARAMETER = 87  # OpenProcess fails with this → no such process → dead


def _pythonw_path() -> str:
    """Absolute path to pythonw.exe beside the current Python interpreter.

    pythonw.exe runs without a console window — suitable for a background daemon on Windows.
    Falls back to sys.executable (launches with a console) rather than silently failing.
    """
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw) if pyw.exists() else sys.executable


class WindowsProcessController:
    """ProcessController for Windows: pythonw detach, CTRL_BREAK → TerminateProcess."""

    def spawn_detached(self, argv: Sequence[str]) -> int:
        """Spawn a fully detached, no-console daemon process and return its pid.

        If argv[0] == "yohoho" the command is rewritten to use pythonw.exe (no console window)
        running ``python -m yohoho`` so the daemon has no visible terminal.  Otherwise argv is
        used as-is.

        ``CREATE_NEW_PROCESS_GROUP`` is required so the child process can be the target of
        ``CTRL_BREAK_EVENT`` in ``terminate()`` without disturbing the parent's process group.
        """
        cmd = list(argv)
        if cmd and cmd[0] == "yohoho":
            cmd = [_pythonw_path(), "-m", "yohoho", *cmd[1:]]

        log_dir = data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        # Popen dups the fd synchronously, so the child keeps its own copy after
        # the parent closes this handle on exiting the with-block.
        with open(log_dir / "daemon.out", "ab") as out:
            p = subprocess.Popen(
                cmd,
                creationflags=(
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
            )
        return p.pid

    def is_alive(self, pid: int) -> bool:
        """Return True if *pid* refers to a currently-running Windows process.

        Uses OpenProcess(QUERY_LIMITED_INFORMATION | SYNCHRONIZE) + WaitForSingleObject(h, 0).
        SYNCHRONIZE is mandatory: without it OpenProcess still succeeds but the wait fails with
        ERROR_ACCESS_DENIED, which previously made is_alive() return False for EVERY pid (incl.
        live ones) — silently breaking status/start/stop.  WaitForSingleObject avoids the classic
        GetExitCodeProcess footgun: a process that legitimately exits with code 259 is
        indistinguishable from STILL_ACTIVE (also 259).  Waiting on the process handle is
        unambiguous: WAIT_TIMEOUT → the handle is unsignaled → still running;
        WAIT_OBJECT_0 → the handle is signaled → the process has exited.

        Edge cases:
        - ERROR_ACCESS_DENIED   → process exists but we lack rights → treat as alive.
        - ERROR_INVALID_PARAMETER → no process with that pid → treat as dead.
        - Any other OpenProcess error → conservatively return False.
        """
        if pid <= 0:
            return False
        try:
            import win32api
            import win32event
        except ImportError:
            # pywin32 not installed (e.g. running on macOS in test collection) — unknown.
            return False
        try:
            h = win32api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
        except win32api.error as exc:
            if exc.winerror == _ERROR_INVALID_PARAMETER:
                return False  # no such process
            if exc.winerror == _ERROR_ACCESS_DENIED:
                return True  # process exists; we just can't open it
            return False
        try:
            return win32event.WaitForSingleObject(h, 0) == win32event.WAIT_TIMEOUT
        except Exception:  # noqa: BLE001
            return False
        finally:
            win32api.CloseHandle(h)

    def terminate(self, pid: int, graceful: bool = True) -> None:
        """Stop the process identified by *pid*.

        Graceful path (default):
          1. Send ``CTRL_BREAK_EVENT`` to the process group.  The child must have been created
             with ``CREATE_NEW_PROCESS_GROUP`` (guaranteed by ``spawn_detached``) so the signal
             targets only the daemon, not the entire parent group.
          2. Poll ``is_alive`` every 100 ms for up to 5 s.
          3. If still alive after 5 s, call ``TerminateProcess`` (hard kill).

        Non-graceful path: ``TerminateProcess`` immediately.

        The method is a no-op if the process is already dead and does not raise if the process
        vanishes between the liveness check and the kill attempt (race-condition safe).

        IMPORTANT — this is the force-escalation fallback, NOT the primary graceful stop.
        Because ``spawn_detached`` creates the daemon with ``DETACHED_PROCESS``, the child has
        NO console attached, so ``GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, ...)`` has nothing
        to deliver the event through — the call effectively no-ops, the ~5 s poll elapses, and
        ``TerminateProcess`` hard-kills the process.  That means the runner's clean-shutdown
        path is SKIPPED here.  The ACTUAL cross-platform graceful path is a **stop-sentinel
        file** that the daemon's run loop polls (written by the ``stop`` command / daemon body);
        the runner notices the sentinel and exits cleanly on its own.  ``terminate()`` exists
        only to force-escalate when that cooperative shutdown does not happen in time.

        # TODO(windows-verify): Confirm on the Windows box that (a) the stop-sentinel file is
        # the path that actually achieves a clean shutdown, and (b) CTRL_BREAK_EVENT does NOT
        # reach the consoleless DETACHED_PROCESS daemon (so the 5 s poll + TerminateProcess
        # fallback is what stops it).  If a graceful Win32 signal is ever needed directly, the
        # deferred option is a named event the runner polls (Task 7).
        """
        if not self.is_alive(pid):
            return
        try:
            import win32api
            import win32con
            import win32console
        except ImportError:
            return

        if not graceful:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                try:
                    win32api.TerminateProcess(h, 1)
                finally:
                    win32api.CloseHandle(h)
            except win32api.error:
                pass  # vanished-process race → termination already succeeded
            return

        # Graceful: best-effort CTRL_BREAK first (expected to no-op for a consoleless
        # DETACHED_PROCESS daemon — see the docstring; clean shutdown is driven by the
        # stop-sentinel file, not this signal).  Then wait up to 5 s before forcing termination.
        try:
            win32console.GenerateConsoleCtrlEvent(win32con.CTRL_BREAK_EVENT, pid)
        except Exception:  # noqa: BLE001 — race: process may have exited already
            pass

        for _ in range(50):
            if not self.is_alive(pid):
                return
            time.sleep(0.1)

        # Still alive after 5 s — force kill.
        try:
            h = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
            try:
                win32api.TerminateProcess(h, 1)
            finally:
                win32api.CloseHandle(h)
        except win32api.error:
            pass  # vanished-process race → termination already succeeded
