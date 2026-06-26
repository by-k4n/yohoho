from yohoho.platform.macos.focus import MacFocusProbe


def test_snapshot_stamps_gen_and_app_id():
    fp = MacFocusProbe(frontmost_fn=lambda: "com.apple.TextEdit")
    t1 = fp.snapshot()
    t2 = fp.snapshot()
    assert t1.app_id == "com.apple.TextEdit" and t2.gen == t1.gen + 1


def test_unchanged_true_when_same_app_false_when_moved():
    cur = {"app": "com.apple.TextEdit"}
    fp = MacFocusProbe(frontmost_fn=lambda: cur["app"], self_ids=("unknown",))
    t = fp.snapshot()
    assert fp.unchanged(t) is True
    cur["app"] = "com.google.Chrome"
    assert fp.unchanged(t) is False


def test_unchanged_true_when_our_own_panel_is_frontmost():
    # Showing the accessory panel can make THIS process the active app, which
    # surfaces as one of self_ids ('unknown' / our bundle id) — NOT a user switch.
    cur = {"app": "com.apple.TextEdit"}
    fp = MacFocusProbe(frontmost_fn=lambda: cur["app"], self_ids=("unknown", "pro.bykc.yohoho"))
    t = fp.snapshot()
    for ours in ("unknown", "pro.bykc.yohoho"):
        cur["app"] = ours
        assert fp.unchanged(t) is True
    cur["app"] = "com.google.Chrome"  # a different REAL app -> genuinely changed
    assert fp.unchanged(t) is False
