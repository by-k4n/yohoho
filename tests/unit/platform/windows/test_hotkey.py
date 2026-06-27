from yohoho.platform.windows.hotkey import WindowsHotkeyListener


class _FakeKey:
    def __init__(self, name): self.name = name


def test_chord_fires_and_no_prepare_hook():
    fired, started, captured = [], {"on": False}, {}
    def factory(on_press, on_release):
        captured["on_press"], captured["on_release"] = on_press, on_release
        return type("L", (), {"start": lambda s: started.__setitem__("on", True),
                              "stop": lambda s: None, "is_alive": lambda s: started["on"]})()
    hk = WindowsHotkeyListener(listener_factory=factory)
    hk.configure("ctrl+alt+space", on_activate=lambda: fired.append(1))
    hk.start()
    for k in (_FakeKey("ctrl_l"), _FakeKey("alt_l"), _FakeKey("space")):
        captured["on_press"](k)
    assert fired == [1]
    assert not hasattr(hk, "prepare")  # Windows needs no keyboard-layout prewarm


def test_is_valid_spec():
    assert WindowsHotkeyListener.is_valid_spec("ctrl+alt+space") is True
