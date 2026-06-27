"""Windows hotkey listener: the shared pynput listener used directly (no keyboard-layout prewarm —
that's a macOS-Tahoe-only fix)."""
from yohoho.platform._shared.pynput_hotkey import PynputHotkeyListener


class WindowsHotkeyListener(PynputHotkeyListener):
    pass
