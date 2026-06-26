# tests/unit/test_platform_api_chrome.py
from yohoho.core import platform_api as pa


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


def test_null_chrome_set_app_policy_is_noop():
    pa.NullWindowChrome().set_app_policy()  # must not raise


def test_null_chrome_style_window_makes_plain_borderless_topmost():
    top = _FakeTop()
    pa.NullWindowChrome().style_window(root=object(), toplevel=top, canvas=object())
    assert ("overrideredirect", True) in top.calls
    assert any(c[0] == "attributes" and c[1][0] == "-topmost" for c in top.calls)
