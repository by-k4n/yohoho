import os

import pytest

pytestmark = pytest.mark.integration


def test_real_model_transcribes_fixture():
    import soundfile as sf

    from yohoho.core.config import data_dir
    from yohoho.core.engine import ParakeetEngine

    fixture = "tests/fixtures/hello.wav"
    if not os.path.exists(fixture):
        pytest.skip(
            "no speech fixture yet; capture one via "
            "`yohoho dictate --seconds 5 --save tests/fixtures/hello.wav`"
        )
    audio, sr = sf.read(fixture, dtype="float32")
    assert sr == 16000
    # Reuse the per-user model cache populated by `yohoho dictate` (no re-download).
    engine = ParakeetEngine(data_dir=data_dir())
    engine.load()
    text = engine.recognize(audio, 16000)
    assert isinstance(text, str) and len(text) > 0
