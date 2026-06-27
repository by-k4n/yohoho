"""Windows clipboard via win32clipboard (pywin32), behind injectable seams. Real win32 is imported
lazily inside the seam functions so importing this module off-Windows never fails."""
from typing import Callable, Optional


def _real_get() -> Optional[str]:
    import win32clipboard as w
    w.OpenClipboard()
    try:
        if w.IsClipboardFormatAvailable(w.CF_UNICODETEXT):
            return w.GetClipboardData(w.CF_UNICODETEXT)
        return None
    finally:
        w.CloseClipboard()


def _real_set(text: str) -> None:
    import win32clipboard as w
    w.OpenClipboard()
    try:
        w.EmptyClipboard()
        w.SetClipboardData(w.CF_UNICODETEXT, text)
    finally:
        w.CloseClipboard()


def _real_has_nontext() -> bool:
    import win32clipboard as w
    w.OpenClipboard()
    try:
        has_any = bool(w.EnumClipboardFormats(0))
        return has_any and not w.IsClipboardFormatAvailable(w.CF_UNICODETEXT)
    finally:
        w.CloseClipboard()


class WindowsClipboard:
    def __init__(
        self,
        get_fn: Callable[[], Optional[str]] = _real_get,
        set_fn: Callable[[str], None] = _real_set,
        nontext_fn: Callable[[], bool] = _real_has_nontext,
    ) -> None:
        self._get, self._set, self._nontext = get_fn, set_fn, nontext_fn

    def get_text(self) -> Optional[str]:
        return self._get()

    def set_text(self, text: str) -> None:
        self._set(text)

    def has_nontext(self) -> bool:
        return self._nontext()
