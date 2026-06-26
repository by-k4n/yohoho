"""Shared test fixtures (audio arrays + engine stubs) imported by unit test modules."""

import numpy as np


def _const_16k(amp: float, seconds: float = 1.0) -> np.ndarray:
    return np.full(int(16000 * seconds), amp, dtype=np.float32)


def _one_second_loud_16k() -> np.ndarray:
    return _const_16k(0.4, 1.0)  # passes the RMS/silence guard


def _silence_16k() -> np.ndarray:
    return _const_16k(0.0, 1.0)  # below the silence floor (P2)


class _RaisingEngine:
    """Engine stub that raises on recognize() — controller must map to ErrorCode.MODEL."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def load(self): ...
    def is_loaded(self):
        return True

    def unload(self): ...
    def recognize(self, audio, sample_rate):
        raise self._exc
