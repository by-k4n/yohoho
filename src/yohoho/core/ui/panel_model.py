"""Pure, Tk-free model for the status panel animation state.

This module contains ALL animation math — no tkinter, no platform code.
The Tk view reads from this model each frame; tests call tick() directly.

Audio half (Task 2):
  - level_from_raw / column_height helpers
  - PanelModel: amplitude peak-hold + decay, scrolling waveform deque

State/progress half (Task 3):
  - ease_wait / rec_on / mmss helpers
  - Style frozen dataclass
  - PanelModel: frame counter, progress easing, state→style mapping

Terminal/close animation helpers:
  - close_step: Drop & Clack wordmark-reveal table
  - cancelled_blink: two-blink cancellation acknowledgement
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

from yohoho.core.events import ErrorCode, State, Terminal
from yohoho.core.ui.theme import (
    CANCELLED_FG,
    CYAN,
    ERROR_AMBER,
    MUTED,
    REC_RED,
    TRANSCRIBING,
)

# Human-readable error messages for the terminal banner (the code alone is cryptic).
_ERROR_MESSAGES = {
    ErrorCode.PERM: "no permission",
    ErrorCode.PASTE: "paste failed",
    ErrorCode.MODEL: "engine error",
    ErrorCode.MIC: "no mic",
    ErrorCode.TIMEOUT: "timed out",
}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def ease_wait(p: float) -> float:
    """Ease progress asymptotically toward 0.90 while transcribing (waiting)."""
    return p + (0.90 - p) * 0.10


def rec_on(frame: int, on: int = 9, off: int = 9) -> bool:
    """Return True when the REC dot should be lit for the given frame index."""
    return (frame % (on + off)) < on


def mmss(seconds: float) -> str:
    """Format seconds as MM:SS string (negative inputs clamp to 0)."""
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


# Drop & Clack: the closing wordmark reveal. Vertical offsets (px, relative to the
# wordmark's resting center) advanced one entry per ~55ms tick — integer steps so it
# reads as mechanical, never an eased glide. `impact` frames get a one-frame flash.
_FINISH_FRAMES = 6  # the full progress bar shows this many frames before the bang
_CLOSE_STEPS: list[tuple[int, bool]] = [
    (-26, False),  # 0: above the pill (clipped by the canvas top)
    (-13, False),  # 1
    (-5, False),   # 2
    (0, False),    # 3: first contact
    (4, True),     # 4: overshoot BELOW the line — CLACK
    (0, False),    # 5
    (-2, False),   # 6: recoil kick
    (0, True),     # 7: settle flush — CLACK
]


def close_step(i: int) -> tuple[int, bool]:
    """Return (dy_px, impact) for close frame ``i``; clamps outside the table."""
    if i < 0:
        return _CLOSE_STEPS[0]
    if i >= len(_CLOSE_STEPS):
        return (0, False)
    return _CLOSE_STEPS[i]


# 'cancelled' acknowledgement: visible → blink → blink → hold → gone, keyed on frames
# since the terminal. Returns (visible, done); when done the panel hides.
_BLINK_SEQ: list[tuple[bool, int]] = [
    (True, 5), (False, 3), (True, 3), (False, 3), (True, 6),
]


def cancelled_blink(frames: int) -> tuple[bool, bool]:
    """Return (visible, done) for the 'cancelled' two-blink acknowledgement."""
    t = 0
    for visible, dur in _BLINK_SEQ:
        if frames < t + dur:
            return (visible, False)
        t += dur
    return (False, True)


@dataclass(frozen=True)
class Style:
    """Render hints produced by PanelModel.style() for the Tk view."""

    label: str
    accent: str
    text_color: str


# ---------------------------------------------------------------------------
# Audio helpers (Task 2)
# ---------------------------------------------------------------------------


def level_from_raw(raw: float) -> float:
    """Convert a raw RMS amplitude value to a normalised [0.0, 1.0] level.

    Formula: (raw - 0.003) * 30, clamped to [0.0, 1.0].
    Calibrated for Parakeet's typical microphone range.
    """
    return min(1.0, max(0.0, (raw - 0.003) * 30))


def column_height(level: float, rows: int = 8) -> int:
    """Map a normalised level [0.0, 1.0] to a dot column height in [0, rows].

    Returns an integer suitable for lighting dots bottom-up in the waveform.
    """
    return round(level * rows)


class PanelModel:
    """Pure animation model for the status panel.

    Owns all frame-by-frame state.  The Tk view calls tick() on a timer
    (~55 ms / ~18 fps in production) and reads waveform_heights() to render.

    Audio state (Task 2):
      current_level  — peak-held level, decays ×0.5 each tick
      _levels        — scrolling deque of per-tick levels (length == columns)

    Task 3 adds: frame counter + progress easing inside tick().
    """

    def __init__(self, columns: int = 30, rows: int = 8) -> None:
        self._columns = columns
        self._rows = rows
        self.current_level: float = 0.0
        self._levels: collections.deque[float] = collections.deque(maxlen=columns)
        # Task 3: frame counter, progress bar, and state/terminal tracking
        self.frame: int = 0
        self.progress: float = 0.0
        self._state: State = State.IDLE
        self._terminal: Terminal | None = None
        self._code: ErrorCode | None = None
        self._terminal_frame: int = 0
        self._progress_at_done: float = 0.0

    # ------------------------------------------------------------------
    # Audio input
    # ------------------------------------------------------------------

    def push_amplitude(self, raw: float) -> None:
        """Accept a raw RMS value from the recorder and peak-hold."""
        self.push_amplitude_level(level_from_raw(raw))

    def push_amplitude_level(self, level: float) -> None:
        """Accept an already-normalised level and peak-hold (take the louder).

        Defensive: a malfunctioning audio device can emit NaN or out-of-range RMS;
        sanitise to keep ``current_level`` within [0, 1] so the waveform never
        produces a height outside the grid (or crashes ``round(nan)``).
        """
        if level != level:  # NaN
            level = 0.0
        level = min(1.0, max(0.0, level))
        self.current_level = max(level, self.current_level)

    # ------------------------------------------------------------------
    # Frame advance
    # ------------------------------------------------------------------

    def set_state(self, state: State) -> None:
        """Transition to a new state and clear any terminal result."""
        self._terminal = None
        self._code = None
        # Entering RECORDING starts a new dictation session: zero the progress bar
        # and blink phase so a reused model never animates backward from a prior run.
        if state is State.RECORDING:
            self.progress = 0.0
            self.frame = 0
        self._state = state

    def set_terminal(self, kind: Terminal, code: ErrorCode | None = None) -> None:
        """Record a terminal outcome (DONE / ERROR / CANCELLED)."""
        self._terminal = kind
        self._code = code
        self._terminal_frame = self.frame
        if kind is Terminal.DONE:
            self._progress_at_done = self.progress

    def tick(self) -> None:
        """Advance one animation frame (~55 ms in production).

        Audio: append current_level to the waveform deque, then apply ×0.5 decay
        so the column falls smoothly when audio goes quiet.

        Task 3: increment frame counter and ease the progress bar.
        Only DONE terminals advance progress deterministically to 1.0 across the
        finish window; TRANSCRIBING (waiting) eases asymptotically toward 0.90.
        ERROR and CANCELLED never touch progress.
        """
        # Audio append + decay (Task 2, unchanged)
        self._levels.append(self.current_level)
        self.current_level *= 0.5
        # Frame counter (Task 3)
        self.frame += 1
        # Progress easing (Task 3)
        if self._terminal is Terminal.DONE:
            # Deterministically ramp the bar from where transcribing left off up to
            # 100% across the finish window, so it visibly COMPLETES before the
            # close bang (regardless of the transcribe-end value), then holds full.
            span = max(1, _FINISH_FRAMES - 1)
            t = min(1.0, self.frames_since_terminal / span)
            self.progress = self._progress_at_done + (1.0 - self._progress_at_done) * t
        elif self._state is State.TRANSCRIBING and self._terminal is None:
            self.progress = ease_wait(self.progress)

    def style(self) -> Style:
        """Return render hints for the current state/terminal combination."""
        if self._terminal is Terminal.ERROR:
            label = self._code.value if self._code else "ERR"
            return Style(label, ERROR_AMBER, ERROR_AMBER)
        if self._terminal is Terminal.DONE:
            return Style("done", CYAN, CYAN)
        if self._terminal is Terminal.CANCELLED:
            return Style("", MUTED, MUTED)
        if self._state is State.RECORDING:
            return Style("REC", REC_RED, MUTED)
        # INSERTING (the clipboard→paste moment) renders as a continuation of
        # transcribing: the progress bar holds, rather than flashing the waveform
        # back on for the brief instant before the DONE close animation.
        if self._state in (State.TRANSCRIBING, State.INSERTING):
            return Style("transcribing…", TRANSCRIBING, TRANSCRIBING)
        # IDLE / STARTING / CANCELLING
        return Style("", MUTED, MUTED)

    def banner(self) -> tuple[str, str] | None:
        """A readable terminal acknowledgement to show across the panel, or None.

        ERROR → a human message in amber (the bare code is cryptic); CANCELLED →
        a 'cancelled' acknowledgement (a brief visible toast before the panel
        hides). Returns ``(message, color)``.
        """
        if self._terminal is Terminal.ERROR:
            return (_ERROR_MESSAGES.get(self._code, "error"), ERROR_AMBER)
        if self._terminal is Terminal.CANCELLED:
            return ("cancelled", CANCELLED_FG)
        return None

    @property
    def terminal(self) -> Terminal | None:
        """The current terminal outcome (or None) — for the view's branch."""
        return self._terminal

    @property
    def error_code(self) -> str | None:
        """The raw error code label (e.g. 'MIC'), or None when not an error terminal."""
        if self._terminal is Terminal.ERROR and self._code is not None:
            return self._code.value
        return None

    @property
    def frames_since_terminal(self) -> int:
        """Animation frames elapsed since the terminal event (0 before any terminal)."""
        if self._terminal is None:
            return 0
        return max(0, self.frame - self._terminal_frame)

    @property
    def close_index(self) -> int:
        """Drop & Clack frame index: <0 during the finish window, >=0 drives the drop."""
        return self.frames_since_terminal - _FINISH_FRAMES

    @property
    def progress_pct(self) -> int:
        """Progress as an integer percentage 0–100."""
        return round(self.progress * 100)

    # ------------------------------------------------------------------
    # Read-only accessors for the Tk view
    # ------------------------------------------------------------------

    def waveform_levels(self) -> list[float]:
        """Return a list of normalised levels, oldest-left, newest-right."""
        return list(self._levels)

    def waveform_heights(self) -> list[int]:
        """Return per-column dot heights (0..rows) for the Tk view."""
        return [column_height(lv, self._rows) for lv in self._levels]
