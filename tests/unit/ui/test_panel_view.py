import pytest

pytestmark = pytest.mark.gui


def test_panel_builds_and_renders():
    import tkinter

    import yohoho.core.ui  # noqa: F401  — Tcl env shim
    from yohoho.core.events import State, Terminal
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel

    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()
    model = PanelModel(rows=7)
    panel = StatusPanel(root, model)

    # Task 7: new single-row geometry; wordmark hidden from the start.
    assert panel.width == 280 and panel.height == 40
    assert panel.canvas.itemcget(panel.word_id, "state") == "hidden"  # no opening bookend

    # Task 8: wordmark stays hidden throughout recording and transcribing.
    model.set_state(State.RECORDING)
    for raw in (0.02, 0.03, 0.0):
        model.push_amplitude(raw)
        model.tick()
        panel.render()
    assert panel.canvas.itemcget(panel.word_id, "state") == "hidden"

    model.set_state(State.TRANSCRIBING)
    for _ in range(4):
        model.tick()
        panel.render()
    assert panel.canvas.itemcget(panel.word_id, "state") == "hidden"

    # Render across the DONE terminal (finish window + close) without raising.
    model.set_terminal(Terminal.DONE)
    for _ in range(14):
        model.tick()
        panel.render()

    # Error renders amber crawl, no crash.
    model2 = PanelModel(rows=7)
    p2 = StatusPanel(root, model2)
    model2.set_terminal(Terminal.ERROR)
    p2.render()

    root.destroy()


@pytest.mark.gui
def test_done_plays_drop_and_clack():
    import tkinter
    import yohoho.core.ui  # noqa: F401
    from yohoho.core.events import State, Terminal
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel
    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()
    model = PanelModel(rows=7)
    panel = StatusPanel(root, model)
    model.set_state(State.TRANSCRIBING)
    model.set_terminal(Terminal.DONE)
    seen_word_shown = False
    ys = []
    for _ in range(14):
        model.tick()
        panel.render()
        if panel.canvas.itemcget(panel.word_id, "state") == "normal":
            seen_word_shown = True
            ys.append(panel.canvas.coords(panel.word_id)[1])
    assert seen_word_shown                      # the wordmark appears at the close
    assert panel.canvas.itemcget(panel.timer_id, "state") == "hidden"  # interface cleared
    assert len(set(ys)) > 1                     # it moved (stepped), not static
    root.destroy()


@pytest.mark.gui
def test_error_centered_and_cancelled_blinks():
    import tkinter
    import yohoho.core.ui  # noqa: F401
    from yohoho.core.events import ErrorCode, Terminal
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel
    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()
    me = PanelModel(rows=7)
    pe = StatusPanel(root, me)
    me.set_terminal(Terminal.ERROR, ErrorCode.MIC)
    # Advance past the two-blink intro (20 frames total) so the banner is steady/held.
    for _ in range(25):
        me.tick()
        pe.render()
    # After the blink schedule completes (done=True), the banner stays lit ("normal").
    assert pe.canvas.itemcget(pe.banner_id, "state") == "normal"
    # Centered: x coordinate should be near width//2.
    bx = pe.canvas.coords(pe.banner_id)[0]
    assert abs(bx - pe.width // 2) <= 5
    # Anchor must be "center" (not "w" as in the old marquee branch).
    assert pe.canvas.itemcget(pe.banner_id, "anchor") == "center"

    mc = PanelModel(rows=7)
    pc = StatusPanel(root, mc)
    mc.set_terminal(Terminal.CANCELLED)
    states = set()
    for _ in range(20):
        mc.tick()
        pc.render()
        states.add(pc.canvas.itemcget(pc.banner_id, "state"))
    assert states == {"normal", "hidden"}           # it blinks (toggles)
    root.destroy()


@pytest.mark.gui
def test_error_renders_banner_centered_and_visible():
    """ERROR state: banner is centered and visible (not crawling) after a few frames."""
    import tkinter
    import yohoho.core.ui  # noqa: F401
    from yohoho.core.events import ErrorCode, Terminal
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel
    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()
    model = PanelModel(rows=7)
    panel = StatusPanel(root, model)
    model.set_terminal(Terminal.ERROR, ErrorCode.MIC)
    # Advance past the blink intro so the banner is in the steady "held" state.
    for _ in range(25):
        model.tick()
        panel.render()
    assert panel.canvas.itemcget(panel.banner_id, "state") == "normal"
    bx = panel.canvas.coords(panel.banner_id)[0]
    assert abs(bx - panel.width // 2) <= 5
    assert panel.canvas.itemcget(panel.banner_id, "anchor") == "center"
    root.destroy()


@pytest.mark.gui
def test_grid_hidden_during_close_and_restored_on_recording():
    """Fix A/B/C regression: grid must be hidden during DONE-close (so the
    wordmark isn't occluded) and re-shown when recording resumes."""
    import tkinter
    import yohoho.core.ui  # noqa: F401
    from yohoho.core.events import State, Terminal
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel

    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()

    model = PanelModel(rows=7)
    panel = StatusPanel(root, model)

    # Drive into a DONE-close frame (close_index >= 0).
    model.set_state(State.TRANSCRIBING)
    model.set_terminal(Terminal.DONE)
    close_frame_rendered = False
    for _ in range(20):
        model.tick()
        panel.render()
        if model.close_index >= 0:
            close_frame_rendered = True
            break

    assert close_frame_rendered, "never reached a close frame in 20 ticks"

    # Wordmark must be visible.
    assert panel.canvas.itemcget(panel.word_id, "state") == "normal"

    # The waveform grid must be HIDDEN (no occlusion of the wordmark).
    a_dot = panel.grid[5][0][1]   # grid[col][row] -> (glow_id, dot_id) -> dot_id
    a_glow = panel.grid[5][0][0]
    assert panel.canvas.itemcget(a_dot, "state") == "hidden"
    assert panel.canvas.itemcget(a_glow, "state") == "hidden"

    # Returning to recording must re-show the grid.
    model.set_state(State.RECORDING)
    model.push_amplitude(0.03)
    model.tick()
    panel.render()
    assert panel.canvas.itemcget(a_dot, "state") == "normal"
    assert panel.canvas.itemcget(a_glow, "state") == "normal"

    root.destroy()


@pytest.mark.gui
def test_timer_and_percent_mutually_exclusive():
    """timer_id and pct_id must never overlap: timer hidden when % shown and vice versa."""
    import tkinter
    import yohoho.core.ui  # noqa: F401
    from yohoho.core.events import State
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel

    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()

    model = PanelModel(rows=7)
    panel = StatusPanel(root, model)

    # While RECORDING: timer should be visible, percent hidden.
    model.set_state(State.RECORDING)
    model.tick()
    panel.render()
    assert panel.canvas.itemcget(panel.timer_id, "state") == "normal"   # timer shown while recording
    assert panel.canvas.itemcget(panel.pct_id, "state") == "hidden"     # percent hidden while recording

    # While TRANSCRIBING: percent should be visible, timer hidden.
    model.set_state(State.TRANSCRIBING)
    model.tick()
    panel.render()
    assert panel.canvas.itemcget(panel.timer_id, "state") == "hidden"   # timer hidden when % shown
    assert panel.canvas.itemcget(panel.pct_id, "state") == "normal"     # percent shown while transcribing

    root.destroy()
