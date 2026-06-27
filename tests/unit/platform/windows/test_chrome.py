import numpy as np

from yohoho.platform.windows.chrome import WindowsWindowChrome


class _FakeTop:
    def __init__(self, child_hwnd=111):
        self._child = child_hwnd
        self.calls = []
        self.afters = []  # (ms, fn, args)

    def overrideredirect(self, v):
        self.calls.append(("overrideredirect", v))

    def attributes(self, *a):
        self.calls.append(("attributes", a))

    def update_idletasks(self):
        self.calls.append(("update_idletasks",))

    def winfo_id(self):
        return self._child

    def winfo_exists(self):
        return True

    def after(self, ms, fn, *args):
        self.afters.append((ms, fn, args))
        return "after-id"


class _FakeCanvas:
    def winfo_reqwidth(self):
        return 300

    def winfo_reqheight(self):
        return 40


class _FakeWin32:
    def __init__(self):
        self.calls = []
        self._wh = None

    def get_ancestor_root(self, child):
        self.calls.append(("get_ancestor_root", child))
        return 999  # window A (the resolved Tk wrapper)

    def add_ex_styles(self, hwnd, styles):
        self.calls.append(("add_ex_styles", hwnd, styles))

    def get_window_rect(self, hwnd):
        self.calls.append(("get_window_rect", hwnd))
        return (10, 20, 310, 60)

    def create_display_window(self, owner, w, h):
        self.calls.append(("create_display_window", owner, w, h))
        self._wh = (w, h)
        return 888  # window B (the mirror)

    def capture_bgra(self, a_hwnd):
        self.calls.append(("capture_bgra", a_hwnd))
        w, h = self._wh
        return np.zeros((h, w, 4), dtype=np.uint8)

    def update_layered(self, b_hwnd, x, y, premul):
        self.calls.append(("update_layered", b_hwnd, x, y, tuple(premul.shape)))


def test_style_window_sets_up_two_window_aa_and_schedules_refresh():
    w32 = _FakeWin32()
    top = _FakeTop(child_hwnd=111)
    WindowsWindowChrome(win32=w32).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    # Borderless + invisible A (-alpha 0); resolved the wrapper; styled the RESOLVED hwnd (999), not 111.
    assert ("overrideredirect", True) in top.calls
    assert ("attributes", ("-alpha", 0.0)) in top.calls
    assert ("get_ancestor_root", 111) in w32.calls
    styled = [c for c in w32.calls if c[0] == "add_ex_styles"]
    assert styled and styled[0][1] == 999
    flags = styled[0][2]
    # NOACTIVATE | LAYERED | TOOLWINDOW | TRANSPARENT
    assert flags & 0x08000000 and flags & 0x00080000 and flags & 0x80 and flags & 0x20
    # No hard region clip — smooth corners come from the composited AA mask.
    assert not any(c[0] == "set_round_region" for c in w32.calls)
    # Created the on-screen mirror window B owned by A (999), at the canvas size.
    assert ("create_display_window", 999, 300, 40) in w32.calls
    # And scheduled the per-pixel-alpha compositing loop on the Tk main thread.
    assert top.afters, "style_window must schedule the refresh loop"


def test_refresh_captures_a_composites_and_presents_on_b():
    w32 = _FakeWin32()
    top = _FakeTop()
    WindowsWindowChrome(win32=w32).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    _ms, fn, args = top.afters[0]
    fn(*args)  # run one compositing frame
    assert ("get_window_rect", 999) in w32.calls   # read A's screen rect
    assert ("capture_bgra", 999) in w32.calls       # PrintWindow A (window A)
    present = [c for c in w32.calls if c[0] == "update_layered"]
    assert present, "refresh must present the composited frame on B"
    # update_layered(B=888, x=10, y=20, premul.shape == (h, w, 4)) — B mirrors A's top-left.
    _, b_hwnd, x, y, shape = present[0]
    assert b_hwnd == 888 and (x, y) == (10, 20) and shape == (40, 300, 4)
    # And it reschedules itself (continuous compositing).
    assert len(top.afters) >= 2


def test_refresh_stops_when_window_destroyed():
    class _GoneTop(_FakeTop):
        def winfo_exists(self):
            return False

    w32 = _FakeWin32()
    top = _GoneTop()
    chrome = WindowsWindowChrome(win32=w32)
    chrome._a_hwnd, chrome._b_hwnd = 999, 888
    chrome._refresh(top)
    assert not any(c[0] in ("capture_bgra", "update_layered") for c in w32.calls)
    assert not top.afters  # no reschedule


def test_style_window_degrades_on_win32_error():
    class _Boom(_FakeWin32):
        def get_ancestor_root(self, child):
            raise OSError("nope")

    top = _FakeTop()
    WindowsWindowChrome(win32=_Boom()).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    # Did not raise; at least made the window borderless.
    assert ("overrideredirect", True) in top.calls


def test_set_app_policy_is_noop():
    WindowsWindowChrome(win32=_FakeWin32()).set_app_policy()  # must not raise
