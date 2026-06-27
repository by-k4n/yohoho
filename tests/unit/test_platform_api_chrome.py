# tests/unit/test_platform_api_chrome.py
from yohoho.core import platform_api as pa
from yohoho.core.ui.main_thread import MainThreadExecutor, marshal_bundle
from yohoho.core.null_platform import make_null_platform


class _FakeTop:
    def __init__(self):
        self.calls = []

    def overrideredirect(self, v):
        self.calls.append(("overrideredirect", v))

    def attributes(self, *args):
        self.calls.append(("attributes", args))


def test_window_chrome_protocol_is_runtime_checkable():
    nc = pa.NullWindowChrome()
    assert isinstance(nc, pa.WindowChrome)


def test_null_chrome_exposes_macos_default_sizing():
    # The seam's per-OS sizing; the null/default is the macOS-tuned pill, safe for every OS.
    nc = pa.NullWindowChrome()
    assert nc.preferred_panel_width == 280
    assert nc.panel_scale == 1.0


def test_null_chrome_set_app_policy_is_noop():
    pa.NullWindowChrome().set_app_policy()  # must not raise


def test_null_chrome_style_window_makes_plain_borderless_topmost():
    top = _FakeTop()
    pa.NullWindowChrome().style_window(root=object(), toplevel=top, canvas=object())
    assert ("overrideredirect", True) in top.calls
    assert any(c[0] == "attributes" and c[1] == ("-topmost", True) for c in top.calls)


def test_bundle_defaults_window_chrome_to_null():
    b = make_null_platform()
    assert isinstance(b.window_chrome, pa.NullWindowChrome)


def test_null_chrome_style_window_swallows_exceptions():
    class _RaisingTop:
        def overrideredirect(self, v):
            raise RuntimeError("chrome boom")
        def attributes(self, *a):
            raise RuntimeError("chrome boom")
    # must not raise
    pa.NullWindowChrome().style_window(root=object(), toplevel=_RaisingTop(), canvas=object())


def test_marshal_bundle_preserves_window_chrome():
    b = make_null_platform()
    marshalled = marshal_bundle(b, MainThreadExecutor())
    assert marshalled.window_chrome is b.window_chrome
