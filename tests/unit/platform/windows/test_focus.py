from yohoho.platform.windows.focus import WindowsFocusProbe


def test_snapshot_stamps_gen_and_hwnd():
    fp = WindowsFocusProbe(foreground_fn=lambda: 12345)
    t1 = fp.snapshot()
    t2 = fp.snapshot()
    assert t1.app_id == "12345" and t2.gen == t1.gen + 1


def test_unchanged_true_when_same_foreground():
    cur = {"hwnd": 999}
    fp = WindowsFocusProbe(foreground_fn=lambda: cur["hwnd"])
    t = fp.snapshot()
    assert fp.unchanged(t) is True
    cur["hwnd"] = 1000
    assert fp.unchanged(t) is False
