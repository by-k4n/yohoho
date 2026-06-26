from yohoho.core.events import ErrorCode, State, Terminal
from yohoho.core.ui.panel_model import PanelModel, _FINISH_FRAMES, cancelled_blink, close_step, ease_wait, mmss, rec_on
from yohoho.core.ui.theme import CANCELLED_FG, CYAN, ERROR_AMBER, REC_RED


def test_banner_gives_human_error_and_cancelled_ack():
    m = PanelModel()
    assert m.banner() is None  # idle / recording: no banner
    m.set_terminal(Terminal.ERROR, ErrorCode.MIC)
    assert m.banner() == ("no mic", ERROR_AMBER)  # human message, not "MIC"
    m2 = PanelModel()
    m2.set_terminal(Terminal.CANCELLED)
    assert m2.banner() == ("cancelled", CANCELLED_FG)  # visible acknowledgement
    m3 = PanelModel()
    m3.set_terminal(Terminal.DONE)
    assert m3.banner() is None  # success shows the 100% bar, not a banner


def test_progress_eases_toward_90():
    p = 0.0
    for _ in range(5):
        p = ease_wait(p)
    assert round(p, 4) == 0.3686


def test_rec_blink_phase_cycles():
    assert [rec_on(f) for f in (0, 8, 9, 17, 18)] == [True, True, False, False, True]


def test_mmss():
    assert mmss(65) == "01:05" and mmss(600) == "10:00" and mmss(599) == "09:59"


def test_inserting_renders_like_transcribing_no_waveform_flash():
    # INSERTING (paste moment) must keep the progress bar, not flash the waveform.
    m = PanelModel()
    m.set_state(State.INSERTING)
    assert m.style().label == "transcribing…"


def test_state_style_recording_and_error_and_done():
    m = PanelModel()
    m.set_state(State.RECORDING)
    assert m.style().label == "REC" and m.style().accent == REC_RED
    m.set_terminal(Terminal.ERROR, ErrorCode.MIC)
    s = m.style()
    assert s.label == "MIC" and s.accent == ERROR_AMBER  # amber code, NOT cyan/100%
    m2 = PanelModel()
    m2.set_terminal(Terminal.DONE)
    assert m2.style().accent == CYAN  # success is cyan


def test_terminal_distinguishes_done_error_cancel():
    for term, finishes in [
        (Terminal.DONE, True),
        (Terminal.ERROR, False),
        (Terminal.CANCELLED, False),
    ]:
        m = PanelModel()
        m.set_state(State.TRANSCRIBING)
        m.set_terminal(term, ErrorCode.MODEL if term == Terminal.ERROR else None)
        before = m.progress
        for _ in range(10):
            m.tick()
        assert (m.progress > before) == finishes  # only DONE eases the bar to 100%


def test_error_after_partway_freezes_progress():
    # the load-bearing invariant: a bar already part-filled must FREEZE on error,
    # never jump to 100% (the old "always fill to 100%" bug)
    m = PanelModel()
    m.set_state(State.TRANSCRIBING)
    for _ in range(10):
        m.tick()
    partway = m.progress
    assert 0.0 < partway < 1.0
    m.set_terminal(Terminal.ERROR, ErrorCode.MODEL)
    for _ in range(30):
        m.tick()
    assert m.progress == partway  # frozen


def test_new_recording_session_resets_progress():
    m = PanelModel()
    m.set_state(State.TRANSCRIBING)
    m.set_terminal(Terminal.DONE)
    for _ in range(30):
        m.tick()
    assert m.progress > 0.99  # finished
    m.set_state(State.RECORDING)  # a fresh session
    assert m.progress == 0.0  # reset — no backward slide on reuse


def test_done_progress_reaches_full_before_close_bang():
    m = PanelModel()
    m.set_state(State.TRANSCRIBING)
    for _ in range(4):
        m.tick()                      # partway, well under 100%
    assert m.progress < 0.95
    m.set_terminal(Terminal.DONE)
    last_visible = 0.0
    while m.close_index < 0:           # advance through the finish window
        m.tick()
        if m.close_index < 0:
            last_visible = m.progress
    assert round(last_visible, 3) >= 0.999   # bar reads 100% on the last frame before the bang


def test_frames_since_terminal_counts_from_set_terminal():
    m = PanelModel()
    m.set_state(State.RECORDING)
    for _ in range(5):
        m.tick()
    m.set_terminal(Terminal.DONE)
    assert m.frames_since_terminal == 0       # zero at the terminal
    m.tick()
    assert m.frames_since_terminal == 1
    for _ in range(4):
        m.tick()
    assert m.frames_since_terminal == 5
    assert m.terminal is Terminal.DONE         # exposed for the view


def test_set_terminal_does_not_zero_the_frame_counter():
    m = PanelModel()
    m.set_state(State.RECORDING)
    for _ in range(7):
        m.tick()
    m.set_terminal(Terminal.CANCELLED)
    assert m.frame == 7                         # frame is NOT reset (only RECORDING resets it)


def test_close_step_table():
    assert close_step(0) == (-26, False)   # starts above the pill (clipped)
    assert close_step(3) == (0, False)     # first contact
    assert close_step(4) == (4, True)      # overshoot below the line — CLACK
    assert close_step(7) == (0, True)      # settle flush — CLACK
    assert close_step(8) == (0, False)     # steady
    assert close_step(99) == (0, False)    # clamps past the end
    assert close_step(-1) == (-26, False)  # clamps before the start


def test_close_index_starts_after_finish_window():
    m = PanelModel()
    m.set_state(State.TRANSCRIBING)
    m.set_terminal(Terminal.DONE)
    assert m.close_index == -_FINISH_FRAMES  # finish window not elapsed (no drop yet)
    for _ in range(_FINISH_FRAMES):
        m.tick()
    assert m.close_index == 0               # the drop begins
    for _ in range(8):
        m.tick()
    assert m.close_index == 8              # settled


def test_cancelled_blink_twice_then_done():
    # on → off → on → off → on → gone (two blinks)
    assert cancelled_blink(0) == (True, False)
    assert cancelled_blink(5) == (False, False)
    assert cancelled_blink(8) == (True, False)
    assert cancelled_blink(11) == (False, False)
    assert cancelled_blink(14) == (True, False)
    assert cancelled_blink(20) == (False, True)   # schedule complete → hide
