from yohoho.core import config_access as ca
from yohoho.core.config import load_config
from yohoho.core.config_tui import (
    _ADVANCED_KEYS,
    _ENUM_OPTIONS,
    _HOTKEY_KEYS,
    ConfigMenu,
)


class FakeTerm:
    def __init__(self, keys):
        self._keys = list(keys)
        self.frames = []
        self.drained = 0

    def read_key(self):
        return self._keys.pop(0) if self._keys else "q"

    def render(self, lines):
        self.frames.append(list(lines))

    def drain_input(self):
        self.drained += 1


def test_menu_lists_settable_keys_and_quits(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    term = FakeTerm(["down", "down", "q"])
    ConfigMenu(term, cfg_path, capturer=None).run()
    flat = "\n".join("\n".join(f) for f in term.frames)
    assert "sounds.volume" in flat and "hotkey" in flat
    assert "version" not in flat and "macos.granted_python_path" not in flat  # protected hidden


def test_menu_selection_moves_with_arrows(tmp_path):
    term = FakeTerm(["down", "q"])
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=None)
    menu.run()
    # selection index advanced by one (0 -> 1) before quit
    assert menu.index == 1


def _run(tmp_path, keys):
    term = FakeTerm(keys)
    ConfigMenu(term, tmp_path / "config.yaml", capturer=None).run()
    return load_config(tmp_path / "config.yaml"), term


def test_toggle_bool_setting(tmp_path):
    # navigate to ui.show_panel, press enter to flip true->false
    term = FakeTerm([])
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=None)
    menu.index = [r[0] for r in menu._rows].index("ui.show_panel")
    menu._edit_current()                       # bool editor flips immediately
    assert load_config(tmp_path / "config.yaml").ui["show_panel"] is False


def test_edit_number_setting(tmp_path):
    term = FakeTerm(["0", ".", "3", "enter"])   # type "0.3" then enter
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=None)
    menu.index = [r[0] for r in menu._rows].index("sounds.volume")
    menu._edit_current()
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.3


def test_invalid_number_shows_error_and_keeps_value(tmp_path):
    term = FakeTerm(["9", "enter", "esc"])      # 9 is out of 0..1; reject; esc cancels
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=None)
    menu.index = [r[0] for r in menu._rows].index("sounds.volume")
    menu._edit_current()
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.5   # unchanged
    assert any("0.0 and 1.0" in "\n".join(f) for f in term.frames)         # engine error shown


def test_enum_pick_recording_mode(tmp_path):
    # enum editor: arrow to the other option, enter
    term = FakeTerm(["down", "enter"])
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=None)
    menu.index = [r[0] for r in menu._rows].index("recording_mode")
    menu._edit_current()
    assert load_config(tmp_path / "config.yaml").recording_mode == "voice_activity_detection"


def test_reset_row_restores_default(tmp_path):
    import yohoho.core.config_access as ca
    from yohoho.core.config import load_config, save_config
    path = tmp_path / "config.yaml"
    save_config(ca.set_value(load_config(path), "sounds.volume", "0.2"), path)   # set a non-default
    menu = ConfigMenu(FakeTerm([]), path, capturer=None)
    menu.index = [r[0] for r in menu._rows].index("sounds.volume")
    menu._reset_current()
    assert load_config(path).sounds["volume"] == 0.5


class FakeCapturer:
    def __init__(self, result):
        self._result = result
        self.called = False

    def capture(self, seconds=3.0, on_progress=None):
        self.called = True
        if on_progress:
            on_progress(0.5)
        return self._result


def test_record_hotkey_saves_captured_chord(tmp_path):
    cap = FakeCapturer("rcmd+space")
    term = FakeTerm([])
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=cap)
    menu.index = [r[0] for r in menu._rows].index("hotkey")
    menu._edit_current()
    assert cap.called
    assert load_config(tmp_path / "config.yaml").hotkey == "rcmd+space"
    assert term.drained >= 1   # tty drained after capture so leaked keys don't reach the menu


class RaisingCapturer:
    def capture(self, seconds=3.0, on_progress=None):
        raise RuntimeError("backend not available (no Accessibility permission)")


def test_record_hotkey_capture_exception_falls_back_to_typed(tmp_path):
    cap = RaisingCapturer()
    term = FakeTerm(["c", "t", "r", "l", "+", "s", "p", "a", "c", "e", "enter"])
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=cap)
    menu.index = [r[0] for r in menu._rows].index("hotkey")
    menu._edit_current()                          # must not raise
    assert load_config(tmp_path / "config.yaml").hotkey == "ctrl+space"
    assert term.drained >= 1                       # still drained after the failed capture


def test_record_hotkey_none_falls_back_to_typed(tmp_path):
    cap = FakeCapturer(None)                     # capture unavailable
    term = FakeTerm(["c", "t", "r", "l", "+", "s", "p", "a", "c", "e", "enter"])
    menu = ConfigMenu(term, tmp_path / "config.yaml", capturer=cap)
    menu.index = [r[0] for r in menu._rows].index("hotkey")
    menu._edit_current()
    assert load_config(tmp_path / "config.yaml").hotkey == "ctrl+space"   # typed fallback applied


def test_error_banner_clears_on_next_action(tmp_path):
    path = tmp_path / "config.yaml"
    term = FakeTerm(["9", "enter", "esc"])      # reject 9 (out of 0..1), then cancel
    menu = ConfigMenu(term, path, capturer=None)
    menu.index = [r[0] for r in menu._rows].index("sounds.volume")
    menu._edit_current()
    assert menu._error is not None              # banner set by the rejected value
    # a different action (reset some row) must clear the stale banner
    menu.index = [r[0] for r in menu._rows].index("ui.show_panel")
    menu._reset_current()
    assert menu._error is None
    assert all("0.0 and 1.0" not in line for line in menu._frame())   # banner absent from frame


def test_reset_all_confirm_yes_resets(tmp_path):
    path = tmp_path / "config.yaml"
    from yohoho.core.config import save_config
    save_config(ca.set_value(load_config(path), "sounds.volume", "0.2"), path)
    menu = ConfigMenu(FakeTerm(["y"]), path, capturer=None)
    menu._reset_all()
    assert load_config(path).sounds["volume"] == 0.5     # back to default


def test_reset_all_confirm_no_keeps_value(tmp_path):
    path = tmp_path / "config.yaml"
    from yohoho.core.config import save_config
    save_config(ca.set_value(load_config(path), "sounds.volume", "0.2"), path)
    menu = ConfigMenu(FakeTerm(["n"]), path, capturer=None)
    menu._reset_all()
    assert load_config(path).sounds["volume"] == 0.2     # unchanged


def test_routing_constants_are_settable_keys():
    settable = set(ca.settable_keys())
    assert _ADVANCED_KEYS <= settable
    assert _HOTKEY_KEYS <= settable
    assert set(_ENUM_OPTIONS) <= settable
