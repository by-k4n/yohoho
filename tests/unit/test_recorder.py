import numpy as np

from yohoho.core.recorder import Recorder


def test_recorder_finish_resamples_blocks_to_16k():
    sr = 48000
    block = np.full((1024,), 0.4, dtype=np.float32)
    rec = Recorder(device_index=None)
    audio16k = rec._finish_from_blocks([block] * 47, sr)  # ~1s of 48k -> 16k
    assert audio16k is not None
    assert abs(len(audio16k) - 16000) <= 64


def test_recorder_finish_empty_returns_none():
    rec = Recorder(device_index=None)
    assert rec._finish_from_blocks([], 48000) is None


def test_stop_closes_stream_even_if_stop_raises_and_consumes_blocks():
    rec = Recorder(device_index=None)
    closed = {"v": False}

    class FakeStream:
        def stop(self):
            raise RuntimeError("device unplugged mid-recording")

        def close(self):
            closed["v"] = True

    rec._stream = FakeStream()
    rec._blocks = [np.full((1024,), 0.4, dtype=np.float32)] * 47
    rec._stream_samplerate = 48000

    out = rec.stop()
    assert closed["v"] is True       # close() ran despite stop() raising (no mic leak)
    assert rec._stream is None       # reference cleared
    assert out is not None           # captured audio still returned
    assert rec.stop() is None        # a second stop replays nothing (blocks consumed)


def test_recorder_open_failure_is_caught(monkeypatch):
    import sounddevice as sd

    def boom(**kwargs):
        raise sd.PortAudioError("no device")

    monkeypatch.setattr(sd, "InputStream", boom)
    rec = Recorder(device_index=999)
    err = rec.start()  # returns an error sentinel, does NOT raise
    assert err is not None
