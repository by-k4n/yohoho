import time

from yohoho.core.engine import FakeEngine, watchdog_ceiling


def test_fake_engine_returns_scripted_text():
    e = FakeEngine(result="hello there")
    e.load()
    assert e.recognize(b"\x00" * 32000, sample_rate=16000) == "hello there"


def test_watchdog_ceiling_is_monotonic_and_floored():
    assert watchdog_ceiling(duration_s=0.5) >= 8.0  # floor
    assert watchdog_ceiling(duration_s=30) >= 30 * 1.5  # scales with clip length


def test_fake_engine_can_simulate_slow_call():
    e = FakeEngine(result="x", delay_s=0.05)
    e.load()
    t = time.monotonic()
    e.recognize(b"\x00" * 16000, 16000)
    assert time.monotonic() - t >= 0.05


def test_fake_engine_raise_on_load():
    import pytest
    from yohoho.core.engine import EngineLoadError

    e = FakeEngine(result="x", raise_on_load=True)
    with pytest.raises(EngineLoadError):
        e.load()
