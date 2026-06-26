import numpy as np

from yohoho.core.audio import is_silent, resample_to_16k, rms


def test_resample_48k_to_16k_changes_length_ratio():
    x = np.zeros(48000, dtype=np.float32)  # 1s @48k
    y = resample_to_16k(x, 48000)
    assert abs(len(y) - 16000) <= 2  # ~1s @16k


def test_resample_passthrough_at_16k():
    x = np.random.randn(16000).astype(np.float32)
    y = resample_to_16k(x, 16000)
    assert np.allclose(x, y)


def test_rms_and_silence_guard():
    assert is_silent(np.zeros(16000, dtype=np.float32))
    assert is_silent(np.array([], dtype=np.float32))  # P2: empty clip ⇒ silent
    loud = np.full(16000, 0.5, dtype=np.float32)
    assert not is_silent(loud)
    assert rms(loud) > rms(np.zeros(16000, dtype=np.float32))
