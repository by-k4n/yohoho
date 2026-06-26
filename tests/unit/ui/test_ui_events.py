from yohoho.core.events import State, Terminal
from yohoho.core.ui.events import apply_event
from yohoho.core.ui.panel_model import PanelModel


def test_apply_amp_state_terminal():
    m = PanelModel()
    apply_event(m, {"t": "amp", "level": 1.0})
    assert m.current_level == 1.0
    apply_event(m, {"t": "state", "state": State.RECORDING})
    assert m.style().label == "REC"
    apply_event(m, {"t": "terminal", "kind": Terminal.DONE, "code": None})
    m.tick()
    assert m.progress > 0.0  # DONE began finishing


def test_apply_ignores_unknown_event():
    m = PanelModel()
    apply_event(m, {"t": "nope"})  # no crash
