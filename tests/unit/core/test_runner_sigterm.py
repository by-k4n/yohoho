"""Unit tests: SIGTERM handler and stop-sentinel graceful-stop paths (T6).

Tests the two cross-platform graceful-stop mechanisms added to PanelRunner:
  1. SIGTERM signal — so launchd ``bootout`` stops the daemon cleanly on macOS.
  2. Stop-sentinel file — so ``yohoho stop`` can reach a Windows DETACHED_PROCESS
     child that has no console and cannot receive signals.
"""
from __future__ import annotations

import queue
import signal

from yohoho.core.ui.runner import PanelRunner


class _FakeRoot:
    """Minimal Tk-root stand-in; exposes ``after`` for scheduling assertions."""

    def __init__(self):
        self.cancelled: list = []
        self._n = 0

    def after(self, ms, fn=None):
        self._n += 1
        return f"after-{self._n}"

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def mainloop(self):
        pass

    def quit(self):
        pass

    def destroy(self):
        pass


def _make_runner(**kwargs) -> PanelRunner:
    """Construct a PanelRunner wired to a fake root; kwargs forwarded to __init__."""
    return PanelRunner(_FakeRoot(), object(), object(), queue.Queue(), **kwargs)


# ---------------------------------------------------------------------------
# SIGTERM → graceful stop
# ---------------------------------------------------------------------------


def test_sigterm_handler_installed_by_run_and_triggers_stop():
    """run() installs a SIGTERM handler; invoking it + driving the poll stops the runner."""
    old_sigint = signal.getsignal(signal.SIGINT)
    old_sigterm = signal.getsignal(signal.SIGTERM)
    runner = _make_runner()
    try:
        runner.run()  # no-op mainloop; returns immediately; handlers installed

        # Retrieve the SIGTERM handler the runner installed.
        installed = signal.getsignal(signal.SIGTERM)
        assert callable(installed), "runner.run() must install a callable SIGTERM handler"

        # Simulate the OS delivering SIGTERM (e.g. launchd bootout).
        installed(signal.SIGTERM, None)

        # The poll loop sees the flag and stops the runner cleanly.
        runner._poll_signal()
        assert runner._stopped is True
    finally:
        # Restore original handlers in case the runner didn't (test failure path).
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)


def test_stop_restores_sigterm_handler():
    """stop() restores the prior SIGTERM handler (no leaked flag-setter)."""
    old_sigterm = signal.getsignal(signal.SIGTERM)
    old_sigint = signal.getsignal(signal.SIGINT)
    runner = _make_runner()
    try:
        runner.run()
        assert signal.getsignal(signal.SIGTERM) is not old_sigterm, (
            "run() must replace the SIGTERM handler"
        )
        runner.stop()
        assert signal.getsignal(signal.SIGTERM) is old_sigterm, (
            "stop() must restore the original SIGTERM handler"
        )
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)


# ---------------------------------------------------------------------------
# Stop-sentinel → graceful stop (the Windows cross-platform path)
# ---------------------------------------------------------------------------


def test_stop_sentinel_triggers_graceful_stop(tmp_path):
    """Presence of the stop-sentinel file triggers stop() and the file is removed."""
    sentinel = tmp_path / "stop"
    runner = _make_runner(stop_sentinel=sentinel)

    # Create the sentinel (simulates `yohoho stop` writing it).
    sentinel.write_text("")

    # Drive the poll loop once — it should detect and remove the sentinel, then stop.
    runner._poll_signal()

    assert runner._stopped is True
    assert not sentinel.exists(), "sentinel must be removed after triggering stop"


def test_stop_sentinel_none_poll_continues():
    """When stop_sentinel=None (default), _poll_signal reschedules normally."""
    runner = _make_runner()  # no sentinel — the default
    runner._poll_signal()
    assert runner._stopped is False
    assert runner._signal_id is not None  # rescheduled via after()


def test_stop_sentinel_absent_poll_continues(tmp_path):
    """When stop_sentinel is set but the file does not exist, polling continues."""
    sentinel = tmp_path / "stop"
    runner = _make_runner(stop_sentinel=sentinel)
    # Sentinel NOT created — runner should keep polling.
    runner._poll_signal()
    assert runner._stopped is False
    assert runner._signal_id is not None
