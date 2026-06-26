from __future__ import annotations
from typing import Callable, Optional
from yohoho.platform.macos import _appkit


class MacClipboard:
    def __init__(self, get_fn: Callable[[], Optional[str]] = _appkit.pasteboard_get,
                 set_fn: Callable[[str], None] = _appkit.pasteboard_set,
                 nontext_fn: Callable[[], bool] = _appkit.pasteboard_has_nontext) -> None:
        self._get, self._set, self._nontext = get_fn, set_fn, nontext_fn

    def get_text(self) -> Optional[str]:
        return self._get()

    def set_text(self, text: str) -> None:
        self._set(text)

    def has_nontext(self) -> bool:
        return self._nontext()
