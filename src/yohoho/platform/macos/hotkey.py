from __future__ import annotations
from typing import Optional
from yohoho.platform._shared.chords import ChordMatcher, parse_spec


def _key_id(key) -> str:
    name = getattr(key, "name", None)
    if name:
        return name
    char = getattr(key, "char", None)
    return char if char else repr(key)


def _real_listener_factory(on_press, on_release):
    from pynput import keyboard
    return keyboard.Listener(on_press=on_press, on_release=on_release)


class MacHotkeyListener:
    def __init__(self, listener_factory=_real_listener_factory) -> None:
        self._factory = listener_factory
        self._listener = None
        self._matcher: Optional[ChordMatcher] = None
        self._cancel_matcher: Optional[ChordMatcher] = None

    @staticmethod
    def is_valid_spec(spec: str) -> bool:
        return bool(parse_spec(spec))

    def prepare(self) -> None:
        """Main-thread startup prep — call ONCE on the main thread before start().

        Pre-warms the keyboard layout so the pynput listener thread never calls the
        main-thread-only input-source API off-main (a SIGTRAP on macOS Tahoe)."""
        from yohoho.platform.macos.input_source import prewarm_keyboard_layout
        prewarm_keyboard_layout()

    def configure(self, spec, on_activate, on_cancel=None) -> None:
        self._matcher = ChordMatcher(spec, on_activate)
        # on_cancel wiring (e.g. an Esc cancel) is optional; M3 may leave it unset (Esc-cancel is M4).
        self._cancel_matcher = None

    def _on_press(self, key) -> None:
        if self._matcher:
            self._matcher.press(_key_id(key))

    def _on_release(self, key) -> None:
        if self._matcher:
            self._matcher.release(_key_id(key))

    def start(self) -> None:
        if self._listener is not None and self._listener.is_alive():
            return  # already running
        self._listener = self._factory(self._on_press, self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def is_alive(self) -> bool:
        return bool(self._listener and self._listener.is_alive())
