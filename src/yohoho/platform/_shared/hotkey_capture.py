"""Pure hold-to-confirm hotkey capture, plus a pynput driver. The state machine takes
injected timestamps (no real clock, no pynput) so it is fully unit-testable."""
from __future__ import annotations

import queue
import time
from typing import Optional

from yohoho.platform._shared.chords import holds_to_spec
from yohoho.platform._shared.pynput_hotkey import _key_id, _real_listener_factory


class HoldCapture:
    """Press-and-hold a chord steady for `hold_seconds` to capture it. Any change to the
    held set restarts the timer; an interrupted attempt commits nothing. If nothing is
    held for `idle_timeout` seconds from start, poll() reports a timeout (caller -> typed
    fallback). Feed key_down/key_up with monotonic timestamps; call poll(t) on a cadence."""

    def __init__(self, started_at: float, hold_seconds: float = 3.0, idle_timeout: float = 8.0):
        self._start = started_at
        self._hold = hold_seconds
        self._idle = idle_timeout
        self._held: set[str] = set()
        self._hold_started: Optional[float] = None

    def key_down(self, raw_id: str, t: float) -> None:
        if raw_id in self._held:
            return                       # ignore OS auto-repeat: set unchanged
        self._held.add(raw_id)
        self._hold_started = t           # set changed -> (re)start the hold timer

    def key_up(self, raw_id: str, t: float) -> None:
        if raw_id not in self._held:
            return
        self._held.discard(raw_id)
        self._hold_started = t if self._held else None

    def poll(self, t: float):
        """-> ('commit', spec) | ('progress', frac) | ('timeout', None) | ('idle', 0.0)"""
        if self._held and self._hold_started is not None:
            frac = (t - self._hold_started) / self._hold
            if frac >= 1.0:
                return ("commit", holds_to_spec(self._held))
            return ("progress", max(0.0, frac))
        if not self._held and (t - self._start) >= self._idle:
            return ("timeout", None)
        return ("idle", 0.0)


class PynputHotkeyCapturer:
    """Drives HoldCapture from a real (or injected) global key listener. capture() blocks
    until a chord is held to completion (-> spec string) or the idle timeout fires (-> None)."""

    def __init__(self, listener_factory=_real_listener_factory, clock=time.monotonic,
                 poll_interval: float = 0.05):
        self._factory = listener_factory
        self._clock = clock
        self._poll = poll_interval

    def capture(self, seconds: float = 3.0, on_progress=None) -> Optional[str]:
        sm = HoldCapture(self._clock(), hold_seconds=seconds)
        events: "queue.Queue" = queue.Queue()
        try:
            listener = self._factory(
                lambda k: events.put(("down", _key_id(k), self._clock())),
                lambda k: events.put(("up", _key_id(k), self._clock())),
            )
            listener.start()
        except Exception:
            return None                          # backend unavailable -> caller falls back to typed
        try:
            while True:
                while not events.empty():
                    kind, raw, t = events.get()
                    (sm.key_down if kind == "down" else sm.key_up)(raw, t)
                state, val = sm.poll(self._clock())
                if state == "commit":
                    return val
                if state == "timeout":
                    return None
                if state == "progress" and on_progress is not None:
                    on_progress(val)
                if self._poll:
                    time.sleep(self._poll)
        finally:
            listener.stop()
