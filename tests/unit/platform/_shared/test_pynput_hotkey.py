from yohoho.platform._shared.pynput_hotkey import PynputHotkeyListener


class _FakeKey:
    def __init__(self, name):
        self.name = name


def _fake_factory(captured, started):
    def factory(on_press, on_release):
        captured["on_press"], captured["on_release"] = on_press, on_release
        return type("L", (), {
            "start": lambda s: started.__setitem__("on", True),
            "stop": lambda s: started.__setitem__("on", False),
            "is_alive": lambda s: started["on"],
        })()
    return factory


def test_chord_completion_fires_activate():
    fired, started, captured = [], {"on": False}, {}
    hk = PynputHotkeyListener(listener_factory=_fake_factory(captured, started))
    hk.configure("ctrl+alt+space", on_activate=lambda: fired.append(1))
    hk.start()
    for k in (_FakeKey("ctrl_l"), _FakeKey("alt_l"), _FakeKey("space")):
        captured["on_press"](k)
    assert fired == [1] and hk.is_alive() is True


def test_double_start_creates_one_listener():
    calls = {"n": 0}
    def factory(on_press, on_release):
        calls["n"] += 1
        return type("L", (), {"start": lambda s: None, "stop": lambda s: None,
                              "is_alive": lambda s: True})()
    hk = PynputHotkeyListener(listener_factory=factory)
    hk.configure("ctrl+alt+space", on_activate=lambda: None)
    hk.start()
    hk.start()
    assert calls["n"] == 1


def test_is_valid_spec():
    assert PynputHotkeyListener.is_valid_spec("ctrl+alt+space") is True
    assert PynputHotkeyListener.is_valid_spec("") is False
