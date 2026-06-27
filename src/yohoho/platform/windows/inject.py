"""Windows paste via pynput Ctrl+V. No focus-reactivation: the panel is WS_EX_NOACTIVATE so the
user's window stays foreground and a plain Ctrl+V lands."""
from typing import Optional

from yohoho.core.platform_api import FocusToken


class WindowsTextInjector:
    def __init__(self, controller=None) -> None:
        self._controller = controller

    def _ctl(self):
        if self._controller is None:
            from pynput.keyboard import Controller
            self._controller = Controller()
        return self._controller

    def paste(self, token: Optional[FocusToken] = None) -> bool:
        from pynput.keyboard import Key
        ctl = self._ctl()
        try:
            with ctl.pressed(Key.ctrl):
                ctl.press("v")
                ctl.release("v")
            return True
        except Exception:  # noqa: BLE001
            return False

    def release_modifiers(self) -> None:
        from pynput.keyboard import Key
        ctl = self._ctl()
        for k in (Key.ctrl, Key.alt, Key.shift, Key.cmd):
            try:
                ctl.release(k)
            except Exception:  # noqa: BLE001
                pass
