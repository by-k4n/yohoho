"""The controller — the resilience heart of yohoho.

Wires recorder/engine/injector/clipboard/history/focus together behind a small
state machine.  The single load-bearing invariant is the **generation-id**
(``self._gen``): one integer stamped at the start of every session and re-read
before *every* side effect.  ``cancel()`` (and any superseding ``toggle()``)
bumps the gen-id; an in-flight transcription worker, on return from the blocking
``recognize()`` call, notices its gen is stale and drops its result instead of
pasting it.  We never kill the recognize thread — we drop its output (P1).

Resilience primitives enforced here (DESIGN §5/§9/§10):
  P1  generation-id gates every side effect; stale results route to the
      DISCARDED recovery bucket, never to paste / clipboard / main history.
  P2  silence and empty transcripts are suppressed — no clipboard/paste/history.
  P3  the clipboard set -> paste is one controlled critical section.
  P5  paste is best-effort; on focus-change or paste failure we leave the text
      on the clipboard and record it as ``copied`` — never paste into the wrong
      app, and never lose the transcript.

Privacy (P9 review contract): this controller never logs transcript text.  It
logs only metadata / outcomes.  If that ever changes, raw text MUST be wrapped
in ``yohoho.core.observability.TranscriptText`` before reaching any logger.

Threading model
---------------
* ``toggle`` / ``cancel`` run on the caller (main / hotkey) thread and only
  mutate state + gen-id under ``self._lock``.
* ``feed_audio_result`` snapshots focus, sizes the watchdog, spawns a *daemon*
  worker thread running ``_transcribe`` and returns immediately so that
  ``cancel()`` can bump the gen-id while ``recognize()`` is still blocking.
* ``_transcribe`` re-reads the live ``self._gen`` (under the lock) before every
  side effect.  The worker is ``daemon=True`` so a wedged ``recognize()`` can
  never block process exit.
* ``wait_idle`` (a test seam) joins the current worker, then polls until the
  state settles to IDLE so assertions observe a fully-quiesced controller.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from yohoho.core.audio import is_silent
from yohoho.core.engine import Engine, TranscribeTimeout, watchdog_ceiling
from yohoho.core.events import ErrorCode, Outcome, State, Terminal, TerminalEvent
from yohoho.core.history import HistoryStore
from yohoho.core.platform_api import FocusToken, PlatformBundle

_SAMPLE_RATE = 16000

TerminalCallback = Callable[[TerminalEvent], None]
StatusCallback = Callable[[State], None]


class Controller:
    """State machine coordinating one dictation session at a time.

    Args:
        engine:            A loaded :class:`~yohoho.core.engine.Engine`.
        bundle:            The active :class:`PlatformBundle` (clipboard /
                           injector / focus / ...).
        history:           Transcript :class:`HistoryStore`.
        on_terminal:       Callback invoked with a :class:`TerminalEvent` at the
                           end of every session (DONE / ERROR / CANCELLED).
        clipboard_restore: M1 default ``False`` — do NOT restore the prior
                           clipboard; the transcript is intentionally left on it
                           so it is never lost (P4/P5).  Reserved for M4.
        data_dir:          Optional data directory (reserved; not used in M1).
        debounce_s:        Minimum seconds between accepted ``toggle()`` calls.
    """

    def __init__(
        self,
        engine: Engine,
        bundle: PlatformBundle,
        history: HistoryStore,
        on_terminal: TerminalCallback,
        on_status: StatusCallback | None = None,
        clipboard_restore: bool = False,
        data_dir: Path | str | None = None,
        debounce_s: float = 0.25,
    ) -> None:
        self.engine = engine
        self.bundle = bundle
        self.history = history
        self._on_terminal = on_terminal
        self._on_status = on_status
        self._clipboard_restore = clipboard_restore
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._debounce_s = debounce_s

        self.state: State = State.IDLE
        self._gen: int = 0  # per-session generation-id (P1)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._last_toggle: float = 0.0  # monotonic time of last accepted toggle

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_state(self, state: State) -> None:
        """Set ``self.state`` under the lock and fire the status callback."""
        with self._lock:
            self.state = state
        if self._on_status is not None:
            self._on_status(state)  # fires on caller/worker thread — callbacks must only queue.put

    # ------------------------------------------------------------------
    # Public control surface (caller / hotkey thread)
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        """Start a session, or (while RECORDING) request stop.

        Debounced against rapid re-fire: a toggle within ``debounce_s`` of the
        previous accepted toggle is ignored.  From IDLE this bumps the gen-id
        and enters RECORDING (in production this starts the recorder; in M1 the
        clip is delivered via :meth:`feed_audio_result`).  While RECORDING a
        toggle is a stop-request — a no-op in M1 because the CLI/recorder
        delivers audio through ``feed_audio_result``; M4 wires the real recorder
        so toggle-to-stop calls the same ``_transcribe`` path.
        """
        now = time.monotonic()
        started = False
        with self._lock:
            if now - self._last_toggle < self._debounce_s:
                return  # debounce: ignore rapid re-fire
            self._last_toggle = now

            if self.state is State.IDLE:
                self._gen += 1  # new session (P1)
                self.state = State.RECORDING
                started = True
            # else: RECORDING stop-request — M1 no-op
        if started and self._on_status is not None:
            self._on_status(State.RECORDING)

    def feed_audio_result(self, audio: np.ndarray) -> None:
        """Recorder-finished entry point (record-STOP).

        Snapshots the focus token NOW (P5 — the token is the record-stop
        snapshot), sizes the watchdog from the clip duration, enters
        TRANSCRIBING and spawns the daemon worker, then returns immediately so
        ``cancel()`` can run while ``recognize()`` is still blocking.
        """
        # Contract guard: a None/empty clip (e.g. an instant start->stop before the
        # mic captured a block) is a silent session.  Handle it HERE, at the boundary,
        # so we never spawn a worker that would crash on is_silent(None) and wedge the
        # state machine at TRANSCRIBING with the panel stuck open (P2 silence).
        if audio is None or getattr(audio, "size", 0) == 0:
            self._set_state(State.IDLE)
            self._emit(TerminalEvent(Terminal.DONE))
            return

        with self._lock:
            gen = self._gen
        token = self.bundle.focus.snapshot()  # P5: record-stop focus snapshot
        self._set_state(State.TRANSCRIBING)

        worker = threading.Thread(
            target=self._transcribe,
            args=(gen, audio, token),
            daemon=True,  # a wedged recognize() must never block process exit
        )
        with self._lock:
            self._worker = worker
        worker.start()

    def cancel(self) -> None:
        """Cancel the current session (idempotent).

        Bumps the gen-id so any in-flight worker becomes stale, transitions
        CANCELLING -> IDLE, and emits CANCELLED.  The worker, on ``recognize()``
        return, sees the stale gen and routes its text to the DISCARDED bucket
        (``_transcribe`` step 3) without pasting or emitting.
        """
        with self._lock:
            if self.state is State.IDLE:
                return  # nothing to cancel — avoid a spurious CANCELLED terminal (M2 UI subscribes)
            self._gen += 1  # invalidate any in-flight worker (P1)
            self.state = State.CANCELLING
        self._set_state(State.IDLE)
        self._emit(TerminalEvent(Terminal.CANCELLED))

    # ------------------------------------------------------------------
    # Worker (daemon thread)
    # ------------------------------------------------------------------

    def _transcribe(self, gen: int, audio: np.ndarray, token: FocusToken) -> None:
        """Transcribe *audio* and apply gated side effects.  Runs on the worker."""
        # 1. P2 silence guard — no clipboard / paste / history.  `audio is None`
        #    (an empty/too-fast capture) is treated as silence so any caller is safe
        #    and the worker can never crash on is_silent(None), wedging the controller.
        if audio is None or is_silent(audio):
            self._set_state(State.IDLE)
            self._emit(TerminalEvent(Terminal.DONE))
            return

        duration = float(len(audio)) / _SAMPLE_RATE
        ceiling = watchdog_ceiling(duration)

        # 2. Watchdog: a hung recognize() is abandoned by bumping the gen-id
        #    (so its eventual result is dropped) and emitting ERROR(TIMEOUT).
        watchdog = threading.Timer(ceiling, self._on_timeout, args=(gen,))
        watchdog.daemon = True
        watchdog.start()
        try:
            text = self.engine.recognize(audio, _SAMPLE_RATE)
        except TranscribeTimeout:
            watchdog.cancel()
            self._set_state(State.IDLE)
            self._emit(TerminalEvent(Terminal.ERROR, ErrorCode.TIMEOUT))
            return
        except Exception:
            # Includes EngineLoadError and any model/runtime failure.
            watchdog.cancel()
            self._set_state(State.IDLE)
            self._emit(TerminalEvent(Terminal.ERROR, ErrorCode.MODEL))
            return
        finally:
            watchdog.cancel()

        # 3. P1 generation gate (the crux): session was cancelled / superseded.
        #    Route the text to the DISCARDED recovery bucket; do NOT paste, do
        #    NOT touch the clipboard, do NOT emit a terminal (cancel already
        #    emitted CANCELLED).
        if gen != self._current_gen():
            if text.strip():
                self.history.add(text, outcome=Outcome.DISCARDED, dur_s=duration)
            return

        # 4. P2 empty gate — no side effects.
        if not text.strip():
            self._set_state(State.IDLE)
            self._emit(TerminalEvent(Terminal.DONE))
            return

        # 5. Insert (P3 clipboard critical section + P5 focus).
        self._set_state(State.INSERTING)
        try:
            # Always set the clipboard so the transcript is recoverable even if
            # paste is skipped (P3/P5 — never lose the text).
            self.bundle.clipboard.set_text(text)
            if self.bundle.focus.unchanged(token) and self.bundle.injector.paste(token):
                outcome = Outcome.PASTED
            else:
                # Focus changed OR paste failed: leave on clipboard, record as
                # copied — never paste into the wrong app (P5).
                outcome = Outcome.COPIED
        finally:
            self.bundle.injector.release_modifiers()
        # M1: clipboard_restore is False — leave the transcript on the clipboard
        # (P4).  M4 will restore the prior clipboard when enabled.

        # 6. Re-check the gen gate before the history write + terminal.  If a
        #    cancel landed during the insert, route to the recovery bucket and
        #    suppress the DONE terminal.
        if gen != self._current_gen():
            if text.strip():
                self.history.add(text, outcome=Outcome.DISCARDED, dur_s=duration)
            return

        self.history.add(text, outcome=outcome, dur_s=duration)
        self._set_state(State.IDLE)
        self._emit(TerminalEvent(Terminal.DONE))

    def _on_timeout(self, gen: int) -> None:
        """Watchdog fire: abandon a hung recognize() of generation *gen*.

        Bumps the gen-id (so the eventual recognize() result is dropped by the
        gen gate) and emits ERROR(TIMEOUT) — but only if *gen* is still current,
        so a late timer for a session that already finished/cancelled is inert.
        """
        with self._lock:
            if gen != self._gen:
                return  # session already moved on; stale timer
            self._gen += 1
            self.state = State.IDLE
        self._emit(TerminalEvent(Terminal.ERROR, ErrorCode.TIMEOUT))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _current_gen(self) -> int:
        with self._lock:
            return self._gen

    def _emit(self, event: TerminalEvent) -> None:
        self._on_terminal(event)

    # ------------------------------------------------------------------
    # Test seams
    # ------------------------------------------------------------------

    def wait_idle(self, timeout: float = 5.0) -> None:
        """Join the current worker (if any), then wait until state == IDLE.

        Deterministic test seam: ensures any in-flight transcription has fully
        completed — including the DISCARDED write on the cancel path — before
        assertions run.
        """
        with self._lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.state is State.IDLE:
                    return
            time.sleep(0.005)
