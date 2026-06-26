from yohoho.platform._shared.pynput_hotkey import PynputHotkeyListener, _key_id  # noqa: F401 — re-exported for test_hotkey.py
from yohoho.platform.macos.input_source import prewarm_keyboard_layout


class MacHotkeyListener(PynputHotkeyListener):
    """macOS adds a main-thread keyboard-layout prewarm (Tahoe SIGTRAP fix); everything else is shared."""

    def prepare(self) -> None:
        prewarm_keyboard_layout()
