"""Engine interface, FakeEngine (for tests/dev), and ParakeetEngine (real onnx-asr model).

The import of ``onnx_asr`` (and transitively ``onnxruntime``) is **lazy** — it only
happens inside ``ParakeetEngine.load()``.  Importing this module in unit-test
collection therefore never pulls the native onnxruntime shared library.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np


# ---------------------------------------------------------------------------
# Shared exception types
# ---------------------------------------------------------------------------


class EngineLoadError(Exception):
    """Raised when the engine fails to initialise its model."""


class TranscribeTimeout(Exception):
    """Raised by the controller when ``recognize()`` exceeds its watchdog ceiling.

    Defined here (rather than in the controller module) so every component that
    needs to handle it can import from a single well-known place.
    """


# ---------------------------------------------------------------------------
# Engine protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Engine(Protocol):
    """Minimal interface expected by the controller and CLI."""

    def load(self) -> None:
        """Load / warm-up the model.  Raises ``EngineLoadError`` on failure."""
        ...

    def recognize(self, audio: bytes | np.ndarray, sample_rate: int) -> str:
        """Transcribe *audio* and return the recognised text.

        ``sample_rate`` MUST be 16 000 Hz; resampling is the recorder's job.
        """
        ...

    def unload(self) -> None:
        """Release model resources."""
        ...

    def is_loaded(self) -> bool:
        """Return ``True`` if the model is currently resident and ready."""
        ...


# ---------------------------------------------------------------------------
# Watchdog ceiling helper
# ---------------------------------------------------------------------------


def watchdog_ceiling(duration_s: float, k: float = 1.5, floor: float = 8.0) -> float:
    """Return the maximum seconds the controller should wait for ``recognize()``.

    The result is ``max(floor, duration_s * k)`` — a floor so that even very
    short clips are given at least *floor* seconds, plus a proportional
    multiplier *k* so long clips aren't unfairly killed.

    Args:
        duration_s: Duration of the audio clip in seconds.
        k:          Multiplier applied to *duration_s*.  Defaults to 1.5.
        floor:      Minimum ceiling in seconds.  Defaults to 8.0.

    Returns:
        Ceiling in seconds (float).
    """
    return max(floor, duration_s * k)


# ---------------------------------------------------------------------------
# FakeEngine — deterministic stub for unit/integration controller tests
# ---------------------------------------------------------------------------


class FakeEngine:
    """Scripted engine that returns a fixed string without touching any model.

    Parameters:
        result:        Text returned by every ``recognize()`` call.
        delay_s:       Seconds to sleep inside ``recognize()`` to simulate
                       latency.  Default 0.0 (instant).
        raise_on_load: If ``True``, ``load()`` raises ``EngineLoadError``
                       instead of succeeding — useful for testing error paths.
    """

    def __init__(
        self,
        result: str,
        delay_s: float = 0.0,
        raise_on_load: bool = False,
    ) -> None:
        self._result = result
        self._delay_s = delay_s
        self._raise_on_load = raise_on_load
        self._loaded = False

    def load(self) -> None:
        if self._raise_on_load:
            raise EngineLoadError("FakeEngine configured to fail on load")
        self._loaded = True

    def recognize(self, audio: bytes | np.ndarray, sample_rate: int) -> str:
        if self._delay_s > 0.0:
            time.sleep(self._delay_s)
        return self._result

    def unload(self) -> None:
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded


# ---------------------------------------------------------------------------
# ParakeetEngine — wraps onnx-asr / NVIDIA Parakeet TDT 0.6 b v2 (int8 ONNX)
# ---------------------------------------------------------------------------


class ParakeetEngine:
    """Production engine backed by ``onnx-asr`` with the Parakeet TDT model.

    The import of ``onnx_asr`` is deferred to ``load()`` so that importing
    this module never triggers ``onnxruntime`` initialisation — keeping unit
    test collection fast and dependency-free.

    Parameters:
        model_name: HuggingFace model ID understood by ``onnx_asr``.
                    Defaults to ``"nemo-parakeet-tdt-0.6b-v2"``.
        data_dir:   Optional :class:`~pathlib.Path` pointing at yohoho's
                    local data directory.  When provided:

                    * ``HF_HOME`` is set to ``<data_dir>/hf`` so the cache
                      lives inside the yohoho data folder.
                    * If ``<data_dir>/model_ready`` exists the engine sets
                      ``HF_HUB_OFFLINE=1`` to avoid revision-check network
                      round-trips.
    """

    def __init__(
        self,
        model_name: str = "nemo-parakeet-tdt-0.6b-v2",
        data_dir: Path | str | None = None,
    ) -> None:
        self._model_name = model_name
        self._data_dir: Path | None = Path(data_dir) if data_dir is not None else None
        self._model = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load the Parakeet model.

        Sets ``HF_HOME`` / ``HF_HUB_OFFLINE`` as appropriate, then calls
        ``onnx_asr.load_model()``.  On any failure raises ``EngineLoadError``.
        """
        import os

        if self._data_dir is not None:
            hf_home = self._data_dir / "hf"
            os.environ["HF_HOME"] = str(hf_home)

            marker = self._data_dir / "model_ready"
            if marker.exists():
                os.environ["HF_HUB_OFFLINE"] = "1"

        try:
            import onnx_asr  # lazy — keeps unit import free of onnxruntime

            self._model = onnx_asr.load_model(self._model_name, quantization="int8")
        except Exception as exc:
            raise EngineLoadError(
                f"Failed to load Parakeet model '{self._model_name}': {exc}"
            ) from exc

        self._loaded = True

    def recognize(self, audio: bytes | np.ndarray, sample_rate: int) -> str:
        """Transcribe *audio* and return the recognised text.

        Args:
            audio:       Either a ``bytes`` object (raw int16 PCM) or a
                         ``numpy.ndarray`` (float32 in [-1, 1]).
            sample_rate: MUST be 16 000 Hz — resampling is the recorder's job.

        Returns:
            Transcribed string (may be empty for silence).

        Raises:
            RuntimeError:  If ``load()`` has not been called successfully.
            ValueError:    If ``sample_rate`` is not 16 000.
        """
        if not self._loaded:
            raise RuntimeError("ParakeetEngine.recognize() called before load()")
        if sample_rate != 16000:
            raise ValueError(f"ParakeetEngine requires sample_rate=16000, got {sample_rate}")

        arr = self._to_float32(audio)
        return self._model.recognize(arr)

    def warmup(self) -> None:
        """Run one silent ``recognize()`` call to warm JIT/kernel caches.

        IMPORTANT: callers must invoke this OUTSIDE the controller pipeline —
        it must never touch the clipboard, the status panel, or the history
        store.  Call it once, immediately after ``load()``, before the
        daemon enters its hotkey-listen loop.
        """
        silence = np.zeros(16000, dtype=np.float32)
        self.recognize(silence, sample_rate=16000)

    def unload(self) -> None:
        """Release the model and free memory."""
        self._model = None
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_float32(audio: bytes | np.ndarray) -> np.ndarray:
        """Convert *audio* to a float32 numpy array in [-1, 1].

        Accepts:
        * ``bytes`` — interpreted as int16 PCM, normalised to [-1, 1].
        * ``numpy.ndarray`` with dtype int16 — same normalisation.
        * ``numpy.ndarray`` with dtype float32 (or other float) — returned as-is
          (cast to float32 if necessary).
        """
        if isinstance(audio, (bytes, bytearray)):
            arr = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            return arr
        if isinstance(audio, np.ndarray):
            if audio.dtype == np.int16:
                return audio.astype(np.float32) / 32768.0
            return audio.astype(np.float32)
        raise TypeError(f"Unsupported audio type: {type(audio)}")
