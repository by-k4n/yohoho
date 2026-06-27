"""Windows foreground-window probe via win32gui.GetForegroundWindow, behind an injectable seam."""
from typing import Callable
from yohoho.core.platform_api import FocusToken


def _real_foreground() -> int:
    import win32gui
    return win32gui.GetForegroundWindow()


class WindowsFocusProbe:
    def __init__(self, foreground_fn: Callable[[], int] = _real_foreground) -> None:
        self._foreground = foreground_fn
        self._gen = 0

    def snapshot(self) -> FocusToken:
        self._gen += 1
        return FocusToken(gen=self._gen, app_id=str(self._foreground()))

    def unchanged(self, token: FocusToken) -> bool:
        return str(self._foreground()) == token.app_id
