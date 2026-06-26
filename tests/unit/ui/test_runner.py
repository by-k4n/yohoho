"""Tests for the PanelRunner run-loop and the panel-demo producer sequence.

Two layers:
  1. Pure ``demo_events`` generator tests — no Tk, no sleeping.
  2. A gui-marked ``_drain`` smoke that exercises the drain path WITHOUT entering
     mainloop (which would block pytest).
"""

from __future__ import annotations

import queue as _queue

import pytest

from yohoho.core.events import ErrorCode, State, Terminal
from yohoho.core.ui.runner import PanelRunner, demo_events


# ---------------------------------------------------------------------------
# Pure producer-sequence tests (no Tk)
# ---------------------------------------------------------------------------


def test_default_sequence_records_then_done_then_quit():
    evs = list(demo_events(None, seconds=1))
    # Starts by recording (reveals the panel).
    assert evs[0] == {"t": "state", "state": State.RECORDING}
    # Drives through TRANSCRIBING.
    assert {"t": "state", "state": State.TRANSCRIBING} in evs
    # Ends with a DONE terminal followed by the quit sentinel.
    terminals = [e for e in evs if e.get("t") == "terminal"]
    assert terminals == [{"t": "terminal", "kind": Terminal.DONE, "code": None}]
    assert evs[-1] == {"t": "quit"}


def test_default_sequence_emits_normalised_amp_frames():
    evs = list(demo_events(None, seconds=1))
    amps = [e for e in evs if e.get("t") == "amp"]
    assert amps, "expected synthetic amplitude frames during recording"
    for e in amps:
        assert 0.0 <= e["level"] <= 1.0


def test_cycle_omits_quit_sentinel():
    evs = list(demo_events(None, seconds=1, cycle=True))
    assert all(e.get("t") != "quit" for e in evs)
    assert evs[0] == {"t": "state", "state": State.RECORDING}


def test_error_state_yields_error_terminal():
    evs = list(demo_events("error", seconds=1))
    # Begins with RECORDING so the panel reveals before the failure.
    assert evs[0] == {"t": "state", "state": State.RECORDING}
    assert {"t": "state", "state": State.TRANSCRIBING} in evs
    terminals = [e for e in evs if e.get("t") == "terminal"]
    assert terminals == [{"t": "terminal", "kind": Terminal.ERROR, "code": ErrorCode.MIC}]
    assert evs[-1] == {"t": "quit"}


def test_cancelled_state_yields_cancelled_terminal():
    evs = list(demo_events("cancelled", seconds=1))
    assert evs[0] == {"t": "state", "state": State.RECORDING}
    terminals = [e for e in evs if e.get("t") == "terminal"]
    assert terminals == [{"t": "terminal", "kind": Terminal.CANCELLED, "code": None}]
    assert evs[-1] == {"t": "quit"}


def test_recording_hold_state_has_no_terminal():
    evs = list(demo_events("recording", seconds=1))
    assert evs[0] == {"t": "state", "state": State.RECORDING}
    assert all(e.get("t") != "terminal" for e in evs)
    # No premature TRANSCRIBING transition when holding RECORDING.
    assert {"t": "state", "state": State.TRANSCRIBING} not in evs
    assert evs[-1] == {"t": "quit"}


def test_transcribing_hold_state_has_no_terminal():
    evs = list(demo_events("transcribing", seconds=1))
    assert {"t": "state", "state": State.TRANSCRIBING} in evs
    assert all(e.get("t") != "terminal" for e in evs)
    assert evs[-1] == {"t": "quit"}


def test_demo_events_does_not_sleep_or_touch_tk():
    # Materialising the whole sequence must be instant (no real sleeps) and must
    # not require Tk — proven simply by this completing without import/timeout.
    list(demo_events(None, seconds=10))
    list(demo_events("error"))
    list(demo_events("done"))


# ---------------------------------------------------------------------------
# gui-marked _drain smoke (no mainloop — that would block pytest)
# ---------------------------------------------------------------------------


@pytest.fixture
def _runner_env():
    """Real Tk root + panel + model + queue + runner (NO mainloop).

    Skips when no windowing server is available so the suite stays headless-safe.
    Tears down by destroying the root (stop() guards double-destroy).
    """
    import queue
    import tkinter

    import yohoho.core.ui  # noqa: F401  — Tcl env shim
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel
    from yohoho.core.ui.runner import PanelRunner

    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()
    model = PanelModel()
    panel = StatusPanel(root, model)
    q: "queue.Queue[dict]" = queue.Queue()
    runner = PanelRunner(root, panel, model, q)
    try:
        yield runner, model, panel, q
    finally:
        try:
            root.destroy()
        except tkinter.TclError:
            pass


@pytest.mark.gui
def test_drain_applies_queued_events(_runner_env):
    """One _drain pass applies state/amp/terminal events to the model + panel."""
    runner, model, panel, q = _runner_env

    q.put({"t": "state", "state": State.RECORDING})
    q.put({"t": "amp", "level": 0.8})
    q.put({"t": "terminal", "kind": Terminal.DONE, "code": None})

    # Drain once WITHOUT mainloop. This applies all three events, shows the panel
    # on the first RECORDING, and schedules a _finish for the terminal.
    runner._drain()

    # The model received the events: DONE terminal → "done" style label.
    assert model.style().label == "done"
    # The peak-held amplitude landed in the model.
    assert model.current_level == 0.8
    # The first RECORDING revealed the panel (drain set the latch).
    assert runner._shown is True

    # Render once more to prove the panel doesn't raise after the events.
    model.tick()
    panel.render()


@pytest.mark.gui
def test_quit_sentinel_stops_runner(_runner_env):
    """A {quit} event makes _drain stop the runner idempotently."""
    runner, _model, _panel, q = _runner_env

    q.put({"t": "quit"})
    runner._drain()
    assert runner._stopped is True
    # stop() is idempotent — a second call is a no-op.
    runner.stop()
    assert runner._stopped is True


def test_terminal_holds_cover_their_animations():
    from yohoho.core.ui.runner import _HOLD_MS
    from yohoho.core.events import Terminal
    from yohoho.core.ui.panel_model import _FINISH_FRAMES
    # DONE must cover finish(_FINISH_FRAMES) + Drop & Clack(8) frames + a settle hold, at ~55ms/frame.
    assert _HOLD_MS[Terminal.DONE] >= (_FINISH_FRAMES + 8) * 55 + 600
    # ERROR must be held long enough to read; CANCELLED the two-blink (~20 frames).
    assert _HOLD_MS[Terminal.ERROR] >= 2800
    assert _HOLD_MS[Terminal.CANCELLED] >= 20 * 55


# ---------------------------------------------------------------------------
# Runner <-> MainThreadExecutor lifecycle — the SIGTRAP-fix integration, tested
# WITHOUT a real Tk root (the gui-marked tests above are skipped by default).
# ---------------------------------------------------------------------------


class _FakeRoot:
    def __init__(self):
        self.cancelled = []
        self.quit_calls = 0
        self.destroy_calls = 0
        self._n = 0

    def after(self, ms, fn=None):
        self._n += 1
        return f"after-{self._n}"

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def quit(self):
        self.quit_calls += 1

    def destroy(self):
        self.destroy_calls += 1


class _FakeExecutor:
    def __init__(self):
        self.pumped = 0
        self.shutdowns = 0

    def pump(self):
        self.pumped += 1

    def shutdown(self):
        self.shutdowns += 1


def _fake_runner():
    ex = _FakeExecutor()
    runner = PanelRunner(_FakeRoot(), object(), object(), _queue.Queue(), executor=ex)
    return runner, ex


def test_drain_pumps_the_executor_before_handling_events():
    runner, ex = _fake_runner()
    runner._drain()                         # empty queue -> just pump + reschedule
    assert ex.pumped == 1


def test_stop_shuts_down_executor_and_cancels_all_after_loops():
    runner, ex = _fake_runner()
    # Pretend run() had scheduled the four after-loops.
    runner._drain_id, runner._tick_id, runner._signal_id, runner._hold_id = "d", "t", "s", "h"
    runner.stop()
    assert ex.shutdowns == 1                                   # workers released, no hang
    assert set(runner.root.cancelled) == {"d", "t", "s", "h"}  # every loop cancelled once
    assert runner.root.quit_calls == 1 and runner.root.destroy_calls == 1


def test_stop_is_idempotent():
    runner, ex = _fake_runner()
    runner.stop()
    runner.stop()
    assert ex.shutdowns == 1                                   # second stop is a no-op
