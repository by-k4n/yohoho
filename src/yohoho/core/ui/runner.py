"""PanelRunner — the Tk run-loop that makes the status panel live.

Architecture (load-bearing, see M2 rationale):
  * Tk's mainloop OWNS the main thread.  Off-main threads (a synthetic producer
    today, the record→transcribe pipeline in Task 10, the PortAudio callback)
    must NEVER touch Tk — they only ``queue.put(event_dict)``.
  * The main thread drains the queue on a ``root.after(40, _drain)`` loop and is
    the ONLY place the model/canvas is mutated from queued events.
  * A separate ``root.after(55, _tick)`` advances the animation each frame.

The runner also owns the panel lifecycle: show on the first event that makes the
panel visible (a transition into a visible state, or any terminal event), finish
(hold then hide) on a terminal event, and exit cleanly on a ``quit`` sentinel or
Ctrl+C.
"""

from __future__ import annotations

import math
import queue
import signal
import tkinter
from pathlib import Path
from typing import Callable, Iterator, Optional

from yohoho.core.events import ErrorCode, State, Terminal
from yohoho.core.ui.events import apply_event
from yohoho.core.ui.main_thread import MainThreadExecutor
from yohoho.core.ui.panel import StatusPanel
from yohoho.core.ui.panel_model import PanelModel
from yohoho.core.platform_api import WindowChrome, NullWindowChrome

# After-loop intervals (ms).
_DRAIN_MS = 40
_TICK_MS = 55
_SIGNAL_POLL_MS = 200

# Terminal hold durations (ms) — how long the finished panel lingers before it
# hides.  CANCELLED holds ~1.2s so the "cancelled" acknowledgement is actually
# seen before the panel goes away (a silent vanish reads as a glitch).
_HOLD_MS = {
    Terminal.DONE: 1500,      # finish(3) + Drop & Clack(8) frames + ~0.85s settle hold
    Terminal.ERROR: 3000,     # centered short message held readable (no marquee crawl)
    Terminal.CANCELLED: 1300, # the two-blink 'cancelled' schedule + margin
}

# States that make the panel visible (i.e. a session has begun / is finishing).
_VISIBLE_STATES = frozenset({State.RECORDING, State.TRANSCRIBING, State.INSERTING, State.STARTING})


class PanelRunner:
    """Drive a :class:`StatusPanel` from a thread-safe event queue.

    The producer (any off-main thread) pushes event dicts:
      ``{"t": "state", "state": State}``     — transition; first RECORDING shows
      ``{"t": "amp", "level": float}``       — already-normalised [0,1] level
      ``{"t": "terminal", "kind": Terminal, "code": ErrorCode | None}``
      ``{"t": "quit"}``                       — stop the run-loop and exit

    ``on_done`` (optional) is called after the panel hides following a terminal
    event, letting the demo / dictate loop decide whether to keep running.
    """

    def __init__(
        self,
        root: tkinter.Tk,
        panel: StatusPanel,
        model: PanelModel,
        q: "queue.Queue[dict]",
        *,
        on_done: Optional[Callable[[], None]] = None,
        executor: Optional[MainThreadExecutor] = None,
        window_chrome: Optional[WindowChrome] = None,
        stop_sentinel: Optional[Path] = None,
    ) -> None:
        self.root = root
        self.panel = panel
        self.model = model
        self.q = q
        self._on_done = on_done
        # Optional main-thread executor: worker threads marshal native side
        # effects (paste / clipboard / focus) here; the drain loop runs them on
        # this (main) thread so they never race the panel's render. See M3 crash.
        self._executor = executor
        self._window_chrome = window_chrome or NullWindowChrome()
        # Optional stop-sentinel file path: the primary cross-platform graceful-stop
        # trigger.  `yohoho stop` writes this file; `_poll_signal` detects and
        # removes it, then calls stop().  Works even when signals can't reach the
        # process (e.g. Windows DETACHED_PROCESS children).
        self._stop_sentinel = stop_sentinel

        self._drain_id: Optional[str] = None
        self._tick_id: Optional[str] = None
        self._signal_id: Optional[str] = None
        self._hold_id: Optional[str] = None
        self._shown = False
        self._stopped = False
        self._sigint = False
        self._old_sigint = None
        self._old_sigterm = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the Tk run-loop on the CURRENT (main) thread.

        Must be called from the main thread.  Applies the platform window policy
        via the window_chrome seam ONCE (so the panel never steals focus), starts
        the drain + tick + signal after-loops, then blocks in ``mainloop()`` until
        ``stop()``.
        """
        self._window_chrome.set_app_policy()

        # Ctrl+C / SIGTERM: Tk's mainloop doesn't reliably deliver signals on
        # macOS, so the handlers only flip a flag and a polled after-loop performs
        # the actual (Tk-thread-safe) shutdown.  SIGTERM handles launchd bootout.
        # Both signals share the same flag and handler.
        self._old_sigint = signal.signal(signal.SIGINT, self._on_sigint)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._on_sigint)

        self._drain_id = self.root.after(_DRAIN_MS, self._drain)
        self._tick_id = self.root.after(_TICK_MS, self._tick)
        self._signal_id = self.root.after(_SIGNAL_POLL_MS, self._poll_signal)

        self.root.mainloop()

    def stop(self) -> None:
        """Cancel the after-loops, leave mainloop, and destroy the root.

        Idempotent: safe to call from a terminal handler, the quit sentinel, the
        SIGINT poller, or twice in a row.
        """
        if self._stopped:
            return
        self._stopped = True

        # Release any worker still blocked in executor.submit() so it can't hang
        # the teardown (pump() will no longer run after the drain loop is cancelled).
        if self._executor is not None:
            self._executor.shutdown()

        for attr in ("_drain_id", "_tick_id", "_signal_id", "_hold_id"):
            after_id = getattr(self, attr)
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tkinter.TclError:
                    pass
                setattr(self, attr, None)

        # Restore the previous SIGINT and SIGTERM handlers so we don't leak our flag-setter.
        if self._old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, self._old_sigint)
            except (TypeError, ValueError):
                pass
            self._old_sigint = None
        if self._old_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm)
            except (TypeError, ValueError):
                pass
            self._old_sigterm = None

        try:
            self.root.quit()
        except tkinter.TclError:
            pass
        try:
            self.root.destroy()
        except tkinter.TclError:
            # Already destroyed — guard against double-destroy.
            pass

    # ------------------------------------------------------------------
    # After-loops (Tk main thread only)
    # ------------------------------------------------------------------

    def _drain(self) -> None:
        """Drain ALL currently-queued events, applying + handling control."""
        # Run any main-thread jobs a worker submitted (native paste/clipboard/focus)
        # FIRST, on this thread, serialized with the render tick — never concurrently.
        if self._executor is not None:
            self._executor.pump()

        while True:
            try:
                ev = self.q.get_nowait()
            except queue.Empty:
                break

            kind = ev.get("t")
            if kind == "quit":
                self.stop()
                return

            apply_event(self.model, ev)

            # A new visible session cancels a previous terminal's pending hide,
            # so the old hold can't blank the new session mid-recording (--cycle /
            # back-to-back dictations).
            if (
                kind == "state"
                and ev.get("state") in _VISIBLE_STATES
                and self._hold_id is not None
            ):
                try:
                    self.root.after_cancel(self._hold_id)
                except tkinter.TclError:
                    pass
                self._hold_id = None

            # Reveal the panel on the FIRST event that makes it visible: a state
            # transition into a visible state, OR a terminal event (so error /
            # cancelled flows that never recorded still surface).
            if not self._shown and (
                (kind == "state" and ev.get("state") in _VISIBLE_STATES) or kind == "terminal"
            ):
                self._shown = True
                self.panel.show()

            if kind == "terminal":
                self._finish(ev.get("kind"))

        self._drain_id = self.root.after(_DRAIN_MS, self._drain)

    def _tick(self) -> None:
        """Advance one animation frame and repaint."""
        self.model.tick()
        self.panel.render()
        self._tick_id = self.root.after(_TICK_MS, self._tick)

    # ------------------------------------------------------------------
    # Terminal finish: hold, then hide (+ optional on_done)
    # ------------------------------------------------------------------

    def _finish(self, kind: Optional[Terminal]) -> None:
        """Schedule the post-terminal hold, then hide the panel."""
        hold = _HOLD_MS.get(kind, 0)
        if hold > 0:
            self._hold_id = self.root.after(hold, self._after_hold)
        else:
            self._after_hold()

    def _after_hold(self) -> None:
        """Hide the panel after the terminal hold, then notify ``on_done``."""
        self._hold_id = None
        if self._stopped:
            return
        self.panel.hide()
        # The panel is reusable: clear the shown-latch so a subsequent RECORDING
        # (e.g. a --cycle demo loop or a second dictation) re-reveals it.
        self._shown = False
        if self._on_done is not None:
            self._on_done()

    # ------------------------------------------------------------------
    # Ctrl+C handling (polled flag — see run())
    # ------------------------------------------------------------------

    def _on_sigint(self, signum, frame) -> None:  # noqa: ANN001
        """SIGINT handler: only flip a flag; the poller does the real shutdown."""
        self._sigint = True

    def _poll_signal(self) -> None:
        """Polled from the Tk thread; stop cleanly once SIGINT/SIGTERM was seen
        or the stop-sentinel file appears (primary cross-platform stop trigger).
        """
        if self._sigint:
            self.stop()
            return
        if self._stop_sentinel is not None and self._stop_sentinel.exists():
            # Remove the sentinel before stopping so a second poll can't re-fire.
            self._stop_sentinel.unlink()
            self.stop()
            return
        self._signal_id = self.root.after(_SIGNAL_POLL_MS, self._poll_signal)


# ---------------------------------------------------------------------------
# Synthetic demo event sequence (pure — no Tk, no sleeping)
# ---------------------------------------------------------------------------

# Production animation frame interval (s); the demo amplitude cadence matches it.
TICK_S = _TICK_MS / 1000.0

# Brief recording dwell used by the error/cancelled demos so the panel reveals
# and the flow reads as realistic before the terminal event (~1.5 s).
_REVEAL_SECONDS = 1.5


def demo_events(
    state: Optional[str] = None,
    seconds: int = 4,
    *,
    cycle: bool = False,
) -> Iterator[dict]:
    """Yield the synthetic event sequence that ``panel-demo`` feeds the runner.

    Pure and Tk-free: it does NOT sleep and does NOT touch Tk, so it is fully
    unit-testable.  The CLI's producer thread wraps this and inserts the real
    ``time.sleep`` cadence between yields.

    Levels are already-normalised [0, 1] (the reducer pushes ``level`` as-is to
    ``push_amplitude_level``).

      * ``state is None`` (default): a full record→transcribe→done pass.  A
        ``{"t": "quit"}`` sentinel is appended at the end UNLESS ``cycle`` is set
        (the caller loops forever instead).
      * ``state == "error"``: RECORD briefly (so the panel reveals), transition
        to TRANSCRIBING, then emit an ERROR terminal (code MIC) and hold; trailing
        ``quit`` unless ``cycle``.
      * ``state == "cancelled"``: RECORD briefly (so the panel reveals), then emit
        a CANCELLED terminal (which hides immediately); trailing ``quit`` unless
        ``cycle``.
      * ``state in {recording, transcribing, done}``: drive to that single
        state/terminal and hold; trailing ``quit`` unless ``cycle``.
    """
    if state in (None, "recording", "transcribing", "done"):
        # Always begin recording (reveals the panel + starts the timer/blink).
        yield {"t": "state", "state": State.RECORDING}

        if state == "recording":
            # Hold in RECORDING with a steady synthetic waveform.
            for i in range(_amp_frames(seconds)):
                yield {"t": "amp", "level": _sine_level(i)}
        else:
            # Speak for `seconds`, then transcribe (and optionally finish).
            for i in range(_amp_frames(seconds)):
                yield {"t": "amp", "level": _sine_level(i)}

            if state in (None, "transcribing", "done"):
                yield {"t": "state", "state": State.TRANSCRIBING}
                # ~1.5 s of nothing — the progress bar eases toward 0.90.
                if state == "transcribing":
                    pass  # hold here; caller loops/quits
                if state in (None, "done"):
                    yield {"t": "terminal", "kind": Terminal.DONE, "code": None}

    elif state == "error":
        # Record briefly (reveals the panel), transcribe, then fail with MIC.
        yield {"t": "state", "state": State.RECORDING}
        for i in range(_amp_frames(_REVEAL_SECONDS)):
            yield {"t": "amp", "level": _sine_level(i)}
        yield {"t": "state", "state": State.TRANSCRIBING}
        yield {"t": "terminal", "kind": Terminal.ERROR, "code": ErrorCode.MIC}

    elif state == "cancelled":
        # Record briefly (reveals the panel), then cancel (hides immediately).
        yield {"t": "state", "state": State.RECORDING}
        for i in range(_amp_frames(_REVEAL_SECONDS)):
            yield {"t": "amp", "level": _sine_level(i)}
        yield {"t": "terminal", "kind": Terminal.CANCELLED, "code": None}

    if not cycle:
        yield {"t": "quit"}


def _amp_frames(seconds: float) -> int:
    """Number of amplitude frames for `seconds` of recording at the tick rate."""
    return max(0, int(seconds / TICK_S))


def _sine_level(i: int) -> float:
    """A gentle synthetic waveform level in [0, 1] for amplitude frame `i`."""
    # Two overlaid sines give the bars a lively, non-uniform bounce.
    base = 0.5 + 0.4 * math.sin(i * 0.30)
    flutter = 0.1 * math.sin(i * 0.91)
    return min(1.0, max(0.0, base + flutter))
