"""Pure DSP helpers — no device I/O, fully testable in isolation.

Functions:
  resample_to_16k  — downsample arbitrary-rate mono audio to 16 kHz (soxr)
  rms              — root-mean-square amplitude of a float32 array
  is_silent        — resilience P2 silence guard: rms(x) < floor
"""

import numpy as np
import soxr

_TARGET_SR = 16000
_SILENCE_FLOOR = 0.003


def resample_to_16k(x: np.ndarray, sr: int) -> np.ndarray:
    """Return mono float32 audio resampled to 16 kHz.

    If *sr* is already 16000 the array is returned unchanged (zero-copy passthrough).
    Built-in / Bluetooth mics are typically 44.1 or 48 kHz; skipping this step feeds
    the model confident garbage that passes the silence guard.
    """
    if sr == _TARGET_SR:
        return x
    out = soxr.resample(x, sr, _TARGET_SR, quality="HQ")
    return out.astype(np.float32, copy=False)


def rms(x: np.ndarray) -> float:
    """Root-mean-square of *x*.  Returns 0.0 for empty input."""
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def is_silent(x: np.ndarray, floor: float = _SILENCE_FLOOR) -> bool:
    """Return True when the clip is below *floor* RMS (i.e. silence / no speech).

    Used by the controller before calling recognize() so an empty or background-noise
    clip never reaches the model (resilience primitive P2).
    """
    return rms(x) < floor
