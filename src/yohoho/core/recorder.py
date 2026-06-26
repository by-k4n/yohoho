"""Per-session microphone recorder.

Design (resilience P8):
  The audio stream is opened at the start of each recording and closed (in a
  ``finally``) when recording stops.  It is NEVER kept resident between sessions,
  so it cannot block other apps or go stale across sleep / device-switch.

Public API:
  Recorder(device_index, ...)   — construct; does NOT open the mic yet
  .start() -> Optional[error]   — open stream; returns RecorderError on failure, None on success
  .stop()  -> Optional[ndarray] — stop stream, return 16 kHz float32 clip (or None if empty)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from yohoho.core.audio import resample_to_16k, rms

# TODO(M4): webrtcvad auto-stop seam — call VAD per block and trigger stop when
# silence exceeds threshold.  Do NOT implement now; wire in after M4 lands.

_DEFAULT_SAMPLE_RATE = 48000


@dataclass
class RecorderError:
    """Sentinel returned by Recorder.start() on PortAudio / device failures.

    The controller maps this to ErrorCode.MIC — never raise from start().
    """

    message: str
    cause: Exception


class Recorder:
    """Captures mic audio in blocks, resamples to 16 kHz on stop."""

    def __init__(
        self,
        device_index: Optional[int],
        on_status: Optional[Callable[[str], None]] = None,
        on_amplitude: Optional[Callable[[float], None]] = None,
        on_duration: Optional[Callable[[float], None]] = None,
        blocksize: int = 1024,
    ) -> None:
        self._device_index = device_index
        self._on_status = on_status or (lambda _: None)
        self._on_amplitude = on_amplitude or (lambda _: None)
        self._on_duration = on_duration or (lambda _: None)
        self._blocksize = blocksize

        self._stream: Optional[sd.InputStream] = None
        self._blocks: list[np.ndarray] = []
        self._stream_samplerate: int = _DEFAULT_SAMPLE_RATE
        self._start_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> Optional[RecorderError]:
        """Open the microphone stream.

        Returns None on success, RecorderError on PortAudio failure.
        Never raises; the caller (controller) maps errors to ErrorCode.MIC.
        """
        self._blocks = []
        self._start_time = None

        # Determine the device's native sample rate (fall back to 48 kHz).
        native_sr = _DEFAULT_SAMPLE_RATE
        try:
            if self._device_index is not None:
                info = sd.query_devices(self._device_index)
                native_sr = int(info.get("default_samplerate", _DEFAULT_SAMPLE_RATE))
        except Exception:
            pass  # non-fatal — use fallback rate

        self._stream_samplerate = native_sr

        try:
            stream = sd.InputStream(
                samplerate=native_sr,
                channels=1,
                dtype="float32",
                device=self._device_index,
                blocksize=self._blocksize,
                callback=self._callback,
            )
            stream.start()
            self._stream = stream
            self._start_time = time.monotonic()
            return None
        except sd.PortAudioError as exc:
            return RecorderError(message=str(exc), cause=exc)

    def stop(self) -> Optional[np.ndarray]:
        """Stop the stream and return the 16 kHz float32 clip, or None if empty.

        Idempotent and exception-safe: the native stream is ALWAYS closed and the
        captured blocks are consumed exactly once (a second stop returns None rather
        than replaying stale audio — P8).
        """
        elapsed: Optional[float] = None
        stream = self._stream
        self._stream = None  # drop the reference first so a raising stop()/close() can't strand it
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()  # MUST run even if stop() raised — else the mic stays open
            except Exception:
                pass
            if self._start_time is not None:
                elapsed = time.monotonic() - self._start_time

        if elapsed is not None:
            self._on_duration(elapsed)

        blocks = self._blocks
        self._blocks = []  # consume once — a re-entrant stop must not replay this audio
        return self._finish_from_blocks(blocks, self._stream_samplerate)

    # ------------------------------------------------------------------
    # Internal helpers (exercised directly by unit tests — no device needed)
    # ------------------------------------------------------------------

    def _finish_from_blocks(self, blocks: list[np.ndarray], sr: int) -> Optional[np.ndarray]:
        """Concatenate *blocks* and resample to 16 kHz.

        Returns None when *blocks* is empty or the concatenated array has length 0.
        This pure helper is what the unit tests exercise (no device required).
        """
        if not blocks:
            return None
        audio = np.concatenate(blocks)
        if audio.size == 0:
            return None
        return resample_to_16k(audio, sr).astype(np.float32, copy=False)

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """sounddevice stream callback — runs on the audio thread."""
        chunk = indata.copy().reshape(-1)
        self._blocks.append(chunk)
        self._on_amplitude(rms(indata))
