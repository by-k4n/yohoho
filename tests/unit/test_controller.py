"""Controller state-machine tests — TDD for Task 7.

Exercises the happy path plus every resilience gate:
  P1 generation-id gating (cancel/supersede drops stale results, recovery bucket)
  P2 silence/empty suppression (no clipboard / paste / history side effects)
  P3 clipboard set->paste critical section
  P5 focus-change degrades paste -> clipboard (never paste into the wrong app)
plus engine-load-error mapping and double-toggle debounce.
"""

from tests.helpers import _RaisingEngine, _one_second_loud_16k, _silence_16k
from yohoho.core.controller import Controller
from yohoho.core.engine import FakeEngine
from yohoho.core.events import State, Terminal
from yohoho.core.history import HistoryStore
from yohoho.core.null_platform import make_null_platform


def make_ctl(tmp_path, result="hello world", enabled=True, engine=None):
    bundle = make_null_platform()
    eng = engine if engine is not None else FakeEngine(result=result)
    eng.load()
    hist = HistoryStore(tmp_path, enabled=enabled)
    events = []
    ctl = Controller(
        engine=eng,
        bundle=bundle,
        history=hist,
        on_terminal=events.append,
        clipboard_restore=False,
    )
    return ctl, bundle, hist, events


# ---------------------------------------------------------------------------
# Step 1 — happy path
# ---------------------------------------------------------------------------


def test_full_cycle_pastes_and_records_history(tmp_path, capsys):
    ctl, bundle, hist, events = make_ctl(tmp_path)
    ctl.toggle()  # start a session
    ctl.feed_audio_result(_one_second_loud_16k())  # recorder finished; here's the clip
    ctl.wait_idle()
    assert "hello world" in capsys.readouterr().out  # NullPlatform pasted (P3)
    assert events[-1].kind == Terminal.DONE
    assert hist.read()[-1]["outcome"] == "pasted"  # P1 allowed the write
    assert ctl.state == State.IDLE


# ---------------------------------------------------------------------------
# Step 2 — the resilience gates
# ---------------------------------------------------------------------------


def test_silence_is_suppressed_no_paste_no_history(tmp_path, capsys):
    ctl, bundle, hist, events = make_ctl(tmp_path, result="")
    ctl.toggle()
    ctl.feed_audio_result(_silence_16k())
    ctl.wait_idle()
    assert capsys.readouterr().out == ""  # P2: no side effects
    assert hist.read() == []
    assert events[-1].kind in (Terminal.DONE, Terminal.CANCELLED)


def test_none_audio_is_treated_as_silence_and_does_not_wedge(tmp_path, capsys):
    # An empty/too-fast capture yields None from Recorder.stop(); the worker must
    # NOT crash on is_silent(None) and leave the controller stuck at TRANSCRIBING.
    ctl, bundle, hist, events = make_ctl(tmp_path)
    ctl.toggle()
    ctl.feed_audio_result(None)
    ctl.wait_idle()
    assert capsys.readouterr().out == ""          # no paste / side effects
    assert hist.read() == []                      # no history
    assert events[-1].kind == Terminal.DONE       # benign terminal (not wedged)
    assert ctl.state == State.IDLE                # recovered, ready for the next press


def test_cancel_during_transcribe_drops_result(tmp_path, capsys):
    slow = FakeEngine(result="late text", delay_s=0.2)  # slow so cancel lands mid-transcribe
    ctl, bundle, hist, events = make_ctl(tmp_path, engine=slow)
    ctl.toggle()
    ctl.feed_audio_result(_one_second_loud_16k())
    ctl.cancel()  # bump gen-id mid-transcribe
    ctl.wait_idle()
    assert "late text" not in capsys.readouterr().out  # P1: stale result dropped, never pasted
    assert hist.read() == []  # no main-timeline write
    assert hist.read_discarded()[-1]["text"] == "late text"  # recovery bucket
    assert events[-1].kind == Terminal.CANCELLED


def test_focus_changed_degrades_to_clipboard(tmp_path, capsys):
    ctl, bundle, hist, events = make_ctl(tmp_path, result="hi")
    bundle.focus.unchanged = lambda token: False  # user alt-tabbed (P5)
    ctl.toggle()
    ctl.feed_audio_result(_one_second_loud_16k())
    ctl.wait_idle()
    assert capsys.readouterr().out == ""  # did NOT paste into the wrong app
    assert bundle.clipboard.get_text() == "hi"  # left on clipboard
    assert hist.read()[-1]["outcome"] == "copied"  # recorded as copied


def test_engine_load_error_emits_model_error(tmp_path):
    from yohoho.core.engine import EngineLoadError

    ctl, bundle, hist, events = make_ctl(
        tmp_path, engine=_RaisingEngine(EngineLoadError("bad onnx"))
    )
    ctl.toggle()
    ctl.feed_audio_result(_one_second_loud_16k())
    ctl.wait_idle()
    assert events[-1].kind == Terminal.ERROR and events[-1].code.value == "MODEL"


def test_double_toggle_is_debounced(tmp_path):
    ctl, *_ = make_ctl(tmp_path)
    ctl.toggle()
    ctl.toggle()  # rapid second press
    assert ctl.state in (State.RECORDING, State.IDLE)  # no crash / illegal transition


def test_cancel_while_idle_is_noop(tmp_path):
    ctl, bundle, hist, events = make_ctl(tmp_path)
    ctl.cancel()  # no active session
    assert events == []  # no spurious CANCELLED terminal (M2 UI would flash on it)
    assert ctl.state == State.IDLE
