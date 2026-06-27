import sys

import pytest

from yohoho.core.run_loop import format_hotkey


@pytest.mark.parametrize("spec,mac,plain", [
    ("ctrl+alt+space", "⌃⌥Space", "Ctrl+Alt+Space"),     # generic unchanged
    ("rcmd+space", "R⌘Space", "RCmd+Space"),              # side-specific
    ("lshift+f5", "L⇧F5", "LShift+F5"),
])
def test_format_hotkey_sides(monkeypatch, spec, mac, plain):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert format_hotkey(spec) == mac
    monkeypatch.setattr(sys, "platform", "win32")
    assert format_hotkey(spec) == plain
