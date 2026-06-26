"""On/off dictation chimes — synthesised in code (no audio assets in the repo).

The sound is a warm electric-piano ("Rhodes") tine layered with a short, low,
*non-tonal* body transient (the "tuh") for haptic weight.  Recording-start is a
G5 tine, recording-done is a resolved C5 (a descending perfect fourth, so "on"
and "off" are unmistakable).  These exact parameters were dialled in by ear.

Playback goes through ``sounddevice`` (the same library the recorder uses), so it
is non-blocking and cross-platform.  A chime is always best-effort: any audio
failure is swallowed so it can never interrupt a dictation.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

SAMPLE_RATE = 44100

# Locked tine pitches (Hz): G5 on, C5 off.
_START_HZ = 784.0
_END_HZ = 523.25


def _soft_env(n: int, attack: float = 0.009, decay: float = 7.5) -> np.ndarray:
    """Gentle attack + exponential decay + short release — the tine's shape."""
    t = np.linspace(0, 1, n)
    e = np.exp(-decay * t)
    a = max(1, int(attack * SAMPLE_RATE))
    e[:a] *= np.linspace(0, 1, a)
    r = max(1, int(0.04 * SAMPLE_RATE))
    e[-r:] *= np.linspace(1, 0, r) ** 1.4
    return e


def _rhodes(freq: float, dur: float, index: float = 0.7, decay: float = 7.0) -> np.ndarray:
    """A simple FM tine (carrier == modulator) for a warm electric-piano tone."""
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    e = _soft_env(n, decay=decay)
    return np.sin(2 * np.pi * freq * t + index * e * np.sin(2 * np.pi * freq * t)) * e


def _pluck_env(n: int, decay: float = 36.0) -> np.ndarray:
    t = np.linspace(0, 1, n)
    return (1 - np.exp(-(t * SAMPLE_RATE) / 35)) * np.exp(-decay * t)


def _lowpass(x: np.ndarray, cutoff: float = 520.0) -> np.ndarray:
    """One-pole low-pass — rounds the noise burst so the 'tuh' is soft, not clicky."""
    a = np.exp(-2 * np.pi * cutoff / SAMPLE_RATE)
    y = np.zeros_like(x)
    for i in range(1, len(x)):
        y[i] = a * y[i - 1] + (1 - a) * x[i]
    return y


def _tuh(rng: np.random.RandomState, f: float, level: float = 0.38,
         noise: float = 0.45, dur: float = 0.085) -> np.ndarray:
    """A short, low, mostly-untuned body transient — felt as oomph, not a note."""
    n = int(dur * SAMPLE_RATE)
    freq = np.linspace(f * 1.4, f, n)               # tiny downward punch (not a "womp")
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    body = np.sin(phase) * _pluck_env(n, decay=36)
    texture = _lowpass(rng.randn(n)) * _pluck_env(n, decay=55) * noise
    return (body + texture) * level


def _layer(*sigs: np.ndarray) -> np.ndarray:
    n = max(len(s) for s in sigs)
    return sum(np.pad(s, (0, n - len(s))) for s in sigs)


def build_chimes(seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(start, end)`` chime waveforms as float32 mono at ``SAMPLE_RATE``.

    Normalised to full scale (peak 1.0) with a shared factor so their relative
    loudness matches what was tuned; the playback ``volume`` then attenuates from
    there.  Deterministic (the only randomness is the seeded 'tuh' texture).
    """
    rng = np.random.RandomState(seed)
    start = _layer(_rhodes(_START_HZ, 0.24), _tuh(rng, f=82))
    end = _layer(_rhodes(_END_HZ, 0.28), _tuh(rng, f=74))
    peak = max(np.max(np.abs(start)), np.max(np.abs(end))) or 1.0
    scale = 1.0 / peak
    return (start * scale).astype(np.float32), (end * scale).astype(np.float32)


PlayFn = Callable[[np.ndarray, int], None]


class ChimePlayer:
    """Plays the start/end chimes, non-blocking and best-effort.

    Args:
        enabled:  when False, every play is a no-op (the ``sounds.enabled`` config).
        volume:   output level in [0, 1] (the ``sounds.volume`` config); 0.5 = half scale.
        play_fn:  injection seam for tests; defaults to ``sounddevice.play`` resolved
                  lazily so importing this module never requires an audio backend.
    """

    def __init__(
        self, enabled: bool = True, *, volume: float = 0.5, play_fn: Optional[PlayFn] = None
    ) -> None:
        self._enabled = enabled
        self._play_fn = play_fn
        # play_start (hotkey thread) and play_end (controller worker thread) both
        # drive the shared sounddevice global stream — serialise them so they can't
        # race its setup/teardown.
        self._lock = threading.Lock()
        vol = float(min(1.0, max(0.0, volume)))
        start, end = build_chimes()
        self._start = (start * vol).astype(np.float32)
        self._end = (end * vol).astype(np.float32)

    def _play(self, samples: np.ndarray) -> None:
        if not self._enabled:
            return
        with self._lock:
            try:
                fn = self._play_fn
                if fn is None:
                    import sounddevice as sd

                    def fn(s, sr):
                        sd.play(s, sr)  # non-blocking: returns immediately

                    self._play_fn = fn
                fn(samples, SAMPLE_RATE)
            except Exception:
                pass  # a chime must never break dictation

    def play_start(self) -> None:
        self._play(self._start)

    def play_end(self) -> None:
        self._play(self._end)
