from __future__ import annotations
from typing import Callable

_MOD_ALIASES = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
                "shift_l": "shift", "shift_r": "shift", "cmd_l": "cmd", "cmd_r": "cmd"}


def normalize_id(key_id: str) -> str:
    return _MOD_ALIASES.get(key_id, key_id)


def parse_spec(spec: str) -> frozenset[str]:
    return frozenset(normalize_id(tok) for tok in spec.lower().split("+") if tok)


# Raw pynput id -> spec token. Modifiers become side-specific; side-less names map to generic;
# anything else (literals: space, letters, f-keys) passes through unchanged in raw_to_token().
_RAW_TO_TOKEN = {
    "ctrl_l": "lctrl", "ctrl_r": "rctrl",
    "alt_l": "lalt", "alt_r": "ralt", "alt_gr": "ralt",
    "shift_l": "lshift", "shift_r": "rshift",
    "cmd_l": "lcmd", "cmd_r": "rcmd",
    "ctrl": "ctrl", "alt": "alt", "shift": "shift", "cmd": "cmd",
}
# Generic modifier token -> raw ids that satisfy it (either side, side-less, + AltGr for alt).
_GENERIC_RAWS = {
    "ctrl": {"ctrl_l", "ctrl_r", "ctrl"},
    "alt": {"alt_l", "alt_r", "alt_gr", "alt"},
    "shift": {"shift_l", "shift_r", "shift"},
    "cmd": {"cmd_l", "cmd_r", "cmd"},
}
# Side-specific token -> raw ids that satisfy it.
_SIDE_RAWS = {
    "lctrl": {"ctrl_l"}, "rctrl": {"ctrl_r"},
    "lalt": {"alt_l"}, "ralt": {"alt_r", "alt_gr"},
    "lshift": {"shift_l"}, "rshift": {"shift_r"},
    "lcmd": {"cmd_l"}, "rcmd": {"cmd_r"},
}
_MOD_ORDER = ["lctrl", "rctrl", "ctrl", "lalt", "ralt", "alt",
              "lshift", "rshift", "shift", "lcmd", "rcmd", "cmd"]


def raw_to_token(raw_id: str) -> str:
    """Raw pynput key-id -> spec token (side-specific for modifiers; literals pass through)."""
    return _RAW_TO_TOKEN.get(raw_id, raw_id)


def holds_to_spec(held_raw) -> str:
    """A set of raw held key-ids -> a canonical spec string (modifiers first, deterministic)."""
    tokens = {raw_to_token(r) for r in held_raw}

    def _key(tok):
        return (_MOD_ORDER.index(tok), "") if tok in _MOD_ORDER else (len(_MOD_ORDER), tok)

    return "+".join(sorted(tokens, key=_key))


def _token_satisfied(token: str, held_raw: set[str]) -> bool:
    if token in _GENERIC_RAWS:
        return bool(_GENERIC_RAWS[token] & held_raw)
    if token in _SIDE_RAWS:
        return bool(_SIDE_RAWS[token] & held_raw)
    return token in held_raw  # literal key (exact raw match)


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
        return all(_token_satisfied(tok, self._down_raw) for tok in self._required)

    def press(self, key_id: str) -> None:
        self._down_raw.add(key_id)
        if self._armed and self._satisfied():
            self._armed = False
            self._on_activate()

    def release(self, key_id: str) -> None:
        self._down_raw.discard(key_id)
        if not self._satisfied():
            self._armed = True   # re-arm only once no required modifier is held at all
