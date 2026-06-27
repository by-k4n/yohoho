import types

import numpy as np

from yohoho.platform.windows.chrome import (
    WindowsWindowChrome,
    _capsule_mask,
    _compose_premul,
)


class _FakeTop:
    def __init__(self, child_hwnd=111):
        self._child = child_hwnd
        self.calls = []
        self.afters = []   # (ms, fn, args)
        self.binds = []     # (sequence, fn)

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

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def after(self, ms, fn, *args):
        self.afters.append((ms, fn, args))
        return "after-id"

    def bind(self, sequence, fn):
        self.binds.append((sequence, fn))


class _FakeCanvas:
    def winfo_reqwidth(self):
        return 300

    def winfo_reqheight(self):
        return 40


class _FakeWin32:
    def __init__(self, dpi=1.0, rect=(10, 20, 310, 60), capture="content"):
        self.calls = []
        self._wh = None
        self._dpi = dpi
        self._rect = rect
        self._capture = capture   # "content" | "blank" | "fail"
        self.torn = 0

    def dpi_scale(self):
        self.calls.append(("dpi_scale",))
        return self._dpi

    def get_ancestor_root(self, child):
        self.calls.append(("get_ancestor_root", child))
        return 999  # window A (the resolved Tk wrapper)

    def add_ex_styles(self, hwnd, styles):
        self.calls.append(("add_ex_styles", hwnd, styles))

    def get_window_rect(self, hwnd):
        self.calls.append(("get_window_rect", hwnd))
        return self._rect

    def create_display_window(self, owner, w, h):
        self.calls.append(("create_display_window", owner, w, h))
        self._wh = (w, h)
        return 888  # window B (the mirror)

    def capture_bgra(self, a_hwnd):
        self.calls.append(("capture_bgra", a_hwnd))
        w, h = self._wh
        if self._capture == "fail":
            return None                                   # PrintWindow reported failure
        if self._capture == "blank":
            return np.zeros((h, w, 4), dtype=np.uint8)    # all-zero (RDP/secure-desktop)
        return np.full((h, w, 4), 200, dtype=np.uint8)    # real content

    def update_layered(self, b_hwnd, x, y, premul):
        self.calls.append(("update_layered", b_hwnd, x, y, tuple(premul.shape)))

    def show_window(self, hwnd, visible):
        self.calls.append(("show_window", hwnd, visible))

    def teardown(self):
        self.torn += 1


# --------------------------------------------------------------------------
# Seam: per-OS width + DPI scale
# --------------------------------------------------------------------------

def test_preferred_panel_width_is_300():
    assert WindowsWindowChrome(win32=_FakeWin32()).preferred_panel_width == 300


def test_panel_scale_reads_system_dpi():
    assert WindowsWindowChrome(win32=_FakeWin32(dpi=1.5)).panel_scale == 1.5


def test_panel_scale_clamps_degenerate_values():
    assert WindowsWindowChrome(win32=_FakeWin32(dpi=0.2)).panel_scale == 1.0   # too small
    assert WindowsWindowChrome(win32=_FakeWin32(dpi=9.0)).panel_scale == 1.0   # absurd


def test_panel_scale_degrades_to_one_on_probe_error():
    class _Boom(_FakeWin32):
        def dpi_scale(self):
            raise OSError("no windll here")

    assert WindowsWindowChrome(win32=_Boom()).panel_scale == 1.0


# --------------------------------------------------------------------------
# numpy mask + compositing math (runs anywhere — no Tk / Win32)
# --------------------------------------------------------------------------

def test_capsule_mask_is_anti_aliased():
    m = _capsule_mask(300, 40)
    assert m.shape == (40, 300)  # (h, w)
    assert m[20, 150] > 0.99                                  # deep interior fully covered
    for (r, c) in ((0, 0), (0, 299), (39, 0), (39, 299)):
        assert m[r, c] < 0.01                                 # corners outside the stadium
    # Fractional edge coverage exists → genuine anti-aliasing, not a hard square clip.
    assert bool(((m > 0.05) & (m < 0.95)).any())


def test_compose_premul_is_premultiplied():
    bgra = np.full((40, 300, 4), 200, dtype=np.uint8)
    mask = _capsule_mask(300, 40)
    alpha = 0.96
    out = _compose_premul(bgra, mask, alpha)
    assert out.shape == (40, 300, 4) and out.dtype == np.uint8
    # Interior pixel (mask≈1): RGB == color·(mask·alpha); A == mask·alpha·255.
    a_center = mask[20, 150] * alpha
    assert abs(int(out[20, 150, 0]) - round(200 * a_center)) <= 2
    assert abs(int(out[20, 150, 3]) - round(255 * a_center)) <= 2
    # Corner outside the mask is fully transparent and premultiplied to black.
    assert out[0, 0, 0] == 0 and out[0, 0, 3] == 0


# --------------------------------------------------------------------------
# style_window: two-window AA setup
# --------------------------------------------------------------------------

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
    # Bound a teardown to A's <Destroy> so the GDI objects are freed.
    assert any(seq == "<Destroy>" for seq, _ in top.binds)
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
    # And it reschedules itself at the active (~18fps) cadence while shown.
    assert len(top.afters) >= 2 and top.afters[-1][0] == 55


def test_refresh_idles_without_compositing_while_parked_offscreen():
    # A parked far past the screen edge (show()/hide() park at sw+2000, sh+2000).
    w32 = _FakeWin32(rect=(3920, 3080, 4220, 3120))
    top = _FakeTop()
    WindowsWindowChrome(win32=w32).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    _ms, fn, args = top.afters[0]
    fn(*args)
    # Saw A's rect, but ran NO capture/composite/present while hidden.
    assert ("get_window_rect", 999) in w32.calls
    assert not any(c[0] in ("capture_bgra", "update_layered") for c in w32.calls)
    # B is explicitly hidden (else it would freeze on-screen showing the last frame).
    assert ("show_window", 888, False) in w32.calls
    # Rescheduled at the slow idle cadence, not the active one.
    assert top.afters[-1][0] == 150


def test_refresh_reshows_b_after_it_was_parked():
    # Park first (hides B), then bring A back on-screen — B is presented and re-shown.
    w32 = _FakeWin32(rect=(3920, 3080, 4220, 3120))
    top = _FakeTop()
    chrome = WindowsWindowChrome(win32=w32)
    chrome.style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    _ms, fn, args = top.afters[0]
    fn(*args)                                     # parked → B hidden
    assert ("show_window", 888, False) in w32.calls
    w32._rect = (10, 20, 310, 60)                 # A slides back on-screen
    fn(top)                                       # unpark → present + re-show B
    assert any(c[0] == "update_layered" for c in w32.calls)
    assert ("show_window", 888, True) in w32.calls


def test_refresh_skips_present_on_blank_or_failed_capture():
    for mode in ("blank", "fail"):
        w32 = _FakeWin32(capture=mode)
        top = _FakeTop()
        WindowsWindowChrome(win32=w32).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
        _ms, fn, args = top.afters[0]
        fn(*args)
        assert ("capture_bgra", 999) in w32.calls          # it tried
        assert not any(c[0] == "update_layered" for c in w32.calls), mode  # but showed no black pill
        assert top.afters[-1][0] == 55                      # still ticking at the active cadence


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


def test_destroy_of_toplevel_tears_down_gdi_once():
    w32 = _FakeWin32()
    top = _FakeTop()
    chrome = WindowsWindowChrome(win32=w32)
    chrome.style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    handler = [fn for seq, fn in top.binds if seq == "<Destroy>"][0]
    handler(types.SimpleNamespace(widget=object()))   # a CHILD widget destroy — must NOT tear down
    assert w32.torn == 0
    handler(types.SimpleNamespace(widget=top))        # the Toplevel itself — tear down the GDI
    assert w32.torn == 1


def test_style_window_degrades_to_visible_pill_on_win32_error():
    class _Boom(_FakeWin32):
        def get_ancestor_root(self, child):
            raise OSError("nope")

    top = _FakeTop()
    WindowsWindowChrome(win32=_Boom()).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    # Did not raise; made the window borderless...
    assert ("overrideredirect", True) in top.calls
    # ...and CRUCIALLY restored a VISIBLE alpha (not left at the invisible -alpha 0) so the whole
    # status UI never vanishes — a jagged pill beats no pill.
    assert ("attributes", ("-alpha", 0.96)) in top.calls


def test_set_app_policy_is_noop():
    WindowsWindowChrome(win32=_FakeWin32()).set_app_policy()  # must not raise
