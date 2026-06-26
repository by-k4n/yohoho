from yohoho.platform.macos.hotkey import MacHotkeyListener, _key_id


class _FakeKey:           # mimics pynput Key/KeyCode enough for _key_id
    def __init__(self, name=None, char=None): self.name, self.char = name, char


def test_key_id_from_named_key():
    assert _key_id(_FakeKey(name="ctrl_l")) == "ctrl_l"
    assert _key_id(_FakeKey(name="space")) == "space"


def test_key_id_from_char():
    assert _key_id(_FakeKey(char="a")) == "a"


def test_is_valid_spec():
    assert MacHotkeyListener.is_valid_spec("ctrl+alt+space") is True
    assert MacHotkeyListener.is_valid_spec("") is False


def test_configure_then_synthetic_presses_activate():
    fired = []
    started = {"on": False}
    # inject a fake pynput Listener factory that just records the callbacks
    captured = {}
    def fake_factory(on_press, on_release):
        captured["on_press"], captured["on_release"] = on_press, on_release
        return type("L", (), {"start": lambda s: started.__setitem__("on", True),
                               "stop": lambda s: None, "is_alive": lambda s: started["on"]})()
    hk = MacHotkeyListener(listener_factory=fake_factory)
    hk.configure("ctrl+alt+space", on_activate=lambda: fired.append(1))
    hk.start()
    for k in (_FakeKey(name="ctrl_l"), _FakeKey(name="alt_l"), _FakeKey(name="space")):
        captured["on_press"](k)
    assert fired == [1] and hk.is_alive() is True


def test_double_start_does_not_create_second_listener():
    calls = {"n": 0}
    def fake_factory(on_press, on_release):
        calls["n"] += 1
        return type("L", (), {"start": lambda s: None, "stop": lambda s: None,
                              "is_alive": lambda s: True})()
    hk = MacHotkeyListener(listener_factory=fake_factory)
    hk.configure("ctrl+alt+space", on_activate=lambda: None)
    hk.start()
    hk.start()
    assert calls["n"] == 1   # second start is a no-op
