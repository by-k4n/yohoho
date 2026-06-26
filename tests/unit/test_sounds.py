import numpy as np

from yohoho.core.sounds import SAMPLE_RATE, ChimePlayer, build_chimes


def test_build_chimes_shapes_dtype_and_full_scale():
    start, end = build_chimes()
    for buf in (start, end):
        assert buf.dtype == np.float32
        assert buf.ndim == 1 and len(buf) > 0
        assert np.max(np.abs(buf)) <= 1.0
    # Normalised together to full scale: the louder of the two peaks at ~1.0.
    assert np.isclose(max(np.max(np.abs(start)), np.max(np.abs(end))), 1.0, atol=1e-3)
    assert len(end) > len(start)  # C5 tail (0.28s) is longer than the G5 (0.24s)


def test_build_chimes_is_deterministic():
    a1, b1 = build_chimes()
    a2, b2 = build_chimes()
    assert np.array_equal(a1, a2) and np.array_equal(b1, b2)


def test_player_applies_volume_and_plays_the_right_buffer():
    calls = []
    player = ChimePlayer(enabled=True, volume=0.5, play_fn=lambda s, sr: calls.append((s, sr)))
    player.play_start()
    player.play_end()

    start, end = build_chimes()
    assert len(calls) == 2
    assert calls[0][1] == SAMPLE_RATE and calls[1][1] == SAMPLE_RATE
    assert np.allclose(calls[0][0], start * 0.5, atol=1e-6)
    assert np.allclose(calls[1][0], end * 0.5, atol=1e-6)


def test_volume_is_clamped_to_unit_range():
    calls = []
    player = ChimePlayer(enabled=True, volume=9.0, play_fn=lambda s, sr: calls.append(s))
    player.play_start()
    assert np.max(np.abs(calls[0])) <= 1.0 + 1e-6


def test_player_is_a_noop_when_disabled():
    calls = []
    player = ChimePlayer(enabled=False, play_fn=lambda s, sr: calls.append(1))
    player.play_start()
    player.play_end()
    assert calls == []


def test_player_swallows_playback_errors():
    def boom(samples, sr):
        raise RuntimeError("no audio device")

    player = ChimePlayer(enabled=True, play_fn=boom)
    player.play_start()  # must not raise — a chime can never break dictation
    player.play_end()
