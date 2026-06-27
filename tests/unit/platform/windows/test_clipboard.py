from yohoho.platform.windows.clipboard import WindowsClipboard


def test_get_set_via_seam():
    store = {"v": None}
    cb = WindowsClipboard(
        get_fn=lambda: store["v"],
        set_fn=lambda t: store.__setitem__("v", t),
        nontext_fn=lambda: False,
    )
    cb.set_text("hi")
    assert cb.get_text() == "hi" and cb.has_nontext() is False


def test_has_nontext_true():
    cb = WindowsClipboard(get_fn=lambda: None, set_fn=lambda t: None, nontext_fn=lambda: True)
    assert cb.has_nontext() is True
