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


def test_alpha_applied_before_ex_styles():
    # Tk's -alpha recomputes GWL_EXSTYLE and clobbers foreign ex-style bits, so our add_ex_styles
    # (WS_EX_NOACTIVATE / WS_EX_TOOLWINDOW) MUST run AFTER -alpha. Pin that ordering so it can't regress.
    events = []

    class _RecTop(_FakeTop):
        def attributes(self, *a):
            events.append(("attributes",) + a)
            super().attributes(*a)

    class _RecWin32(_FakeWin32):
        def add_ex_styles(self, hwnd, styles):
            events.append(("add_ex_styles", hwnd, styles))
            super().add_ex_styles(hwnd, styles)

    WindowsWindowChrome(win32=_RecWin32()).style_window(root=object(), toplevel=_RecTop(), canvas=_FakeCanvas())
    alpha_idx = next(i for i, e in enumerate(events) if e[0] == "attributes" and len(e) > 1 and e[1] == "-alpha")
    exstyle_idx = next(i for i, e in enumerate(events) if e[0] == "add_ex_styles")
    assert alpha_idx < exstyle_idx, f"-alpha must precede add_ex_styles; events={events}"
