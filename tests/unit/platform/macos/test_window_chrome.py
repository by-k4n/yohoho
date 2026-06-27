from yohoho.platform.macos.chrome import MacWindowChrome


def test_mac_chrome_keeps_pre_m5_sizing():
    # macOS keeps the pre-M5 pill: 280px logical, no DPI multiply (Quartz handles Retina).
    c = MacWindowChrome()
    assert c.preferred_panel_width == 280
    assert c.panel_scale == 1.0


def test_set_app_policy_delegates_to_accessory_policy():
    called = {"n": 0}
    c = MacWindowChrome(set_policy_fn=lambda: called.__setitem__("n", called["n"] + 1))
    c.set_app_policy()
    assert called["n"] == 1


def test_style_window_applies_chrome_then_round_in_order():
    order = []
    c = MacWindowChrome(
        apply_chrome_fn=lambda root, top: order.append("chrome"),
        enable_round_fn=lambda top, canvas: order.append("round"),
    )
    c.style_window(root="r", toplevel="t", canvas="c")
    assert order == ["chrome", "round"]  # enable_round MUST follow apply_chrome
