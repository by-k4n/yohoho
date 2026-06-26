from yohoho.platform.windows.chrome import WindowsWindowChrome


class _FakeTop:
    def __init__(self, child_hwnd=111):
        self._child = child_hwnd
        self.calls = []
    def overrideredirect(self, v): self.calls.append(("overrideredirect", v))
    def attributes(self, *a): self.calls.append(("attributes", a))
    def update_idletasks(self): self.calls.append(("update_idletasks",))
    def winfo_id(self): return self._child


class _FakeCanvas:
    def winfo_reqwidth(self): return 280
    def winfo_reqheight(self): return 40


class _FakeWin32:
    def __init__(self):
        self.calls = []
    def get_ancestor_root(self, child):
        self.calls.append(("get_ancestor_root", child))
        return 999  # resolved top-level
    def add_ex_styles(self, hwnd, styles):
        self.calls.append(("add_ex_styles", hwnd, styles))
    def set_round_region(self, hwnd, w, h):
        self.calls.append(("set_round_region", hwnd, w, h))


def test_style_window_resolves_top_level_then_applies_to_it():
    w32 = _FakeWin32()
    top = _FakeTop(child_hwnd=111)
    WindowsWindowChrome(win32=w32).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    # Resolved the wrapper from the child, and applied styles/region to the RESOLVED hwnd (999), not 111.
    assert ("get_ancestor_root", 111) in w32.calls
    assert any(c[0] == "add_ex_styles" and c[1] == 999 for c in w32.calls)
    assert ("set_round_region", 999, 280, 40) in w32.calls


def test_style_window_degrades_on_win32_error():
    class Boom(_FakeWin32):
        def get_ancestor_root(self, child): raise OSError("nope")
    top = _FakeTop()
    WindowsWindowChrome(win32=Boom()).style_window(root=object(), toplevel=top, canvas=_FakeCanvas())
    # Did not raise; at least made the window borderless.
    assert ("overrideredirect", True) in top.calls


def test_set_app_policy_is_noop():
    WindowsWindowChrome(win32=_FakeWin32()).set_app_policy()  # must not raise
