from __future__ import annotations
from typing import Callable

_MOD_ALIASES = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
                "shift_l": "shift", "shift_r": "shift", "cmd_l": "cmd", "cmd_r": "cmd"}


def normalize_id(key_id: str) -> str:
    return _MOD_ALIASES.get(key_id, key_id)


def parse_spec(spec: str) -> frozenset[str]:
    return frozenset(normalize_id(tok) for tok in spec.lower().split("+") if tok)


class ChordMatcher:
    """Edge-triggered chord detector over normalized key-ids (no pynput)."""
    def __init__(self, spec: str, on_activate: Callable[[], None]) -> None:
        self._required = parse_spec(spec)
        self._on_activate = on_activate
        # Track RAW key-ids so two physical variants of one modifier (cmd_l + cmd_r,
        # which both normalize to 'cmd') are counted separately: releasing one while
        # the other is still held must NOT re-arm and spuriously re-fire the chord.
        self._down_raw: set[str] = set()
        self._armed = True   # True when the chord is NOT fully down (ready to fire)
        if not self._required:
            raise ValueError("ChordMatcher spec must contain at least one key")

    def _satisfied(self) -> bool:
        return self._required <= {normalize_id(k) for k in self._down_raw}

    def press(self, key_id: str) -> None:
        self._down_raw.add(key_id)
        if self._armed and self._satisfied():
            self._armed = False
            self._on_activate()

    def release(self, key_id: str) -> None:
        self._down_raw.discard(key_id)
        if not self._satisfied():
            self._armed = True   # re-arm only once no required modifier is held at all
