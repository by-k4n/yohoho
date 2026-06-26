from tests.helpers import _one_second_loud_16k
from yohoho.core.controller import Controller
from yohoho.core.engine import FakeEngine
from yohoho.core.events import State
from yohoho.core.history import HistoryStore
from yohoho.core.null_platform import make_null_platform


def test_on_status_receives_state_transitions(tmp_path):
    eng = FakeEngine(result="hi")
    eng.load()
    states = []
    ctl = Controller(
        engine=eng,
        bundle=make_null_platform(),
        history=HistoryStore(tmp_path, enabled=False),
        on_terminal=lambda e: None,
        on_status=states.append,
    )
    ctl.toggle()
    ctl.feed_audio_result(_one_second_loud_16k())
    ctl.wait_idle()
    assert State.RECORDING in states and State.TRANSCRIBING in states and states[-1] == State.IDLE


def test_on_status_optional_keeps_m1_behaviour(tmp_path):
    eng = FakeEngine(result="hi")
    eng.load()
    ctl = Controller(
        engine=eng,
        bundle=make_null_platform(),
        history=HistoryStore(tmp_path, enabled=False),
        on_terminal=lambda e: None,
    )  # no on_status — default None, must not crash
    ctl.toggle()
    ctl.feed_audio_result(_one_second_loud_16k())
    ctl.wait_idle()
