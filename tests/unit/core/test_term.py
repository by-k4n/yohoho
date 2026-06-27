import os
import sys

import pytest

from yohoho.core.ui.term import Terminal, decode_key, read_escape_sequence


def _peek_from(values):
    """peek_next() that yields each value once, then None (nothing more ready)."""
    it = iter(values)

    def peek():
        return next(it, None)

    return peek


def test_read_escape_sequence_bare_esc():
    # nothing ready after the ESC -> true bare Esc, no block
    assert read_escape_sequence("\x1b", _peek_from([])) == "esc"


def test_read_escape_sequence_arrows():
    assert read_escape_sequence("\x1b", _peek_from(["[", "B"])) == "down"
    assert read_escape_sequence("\x1b", _peek_from(["[", "A"])) == "up"
    assert read_escape_sequence("\x1b", _peek_from(["[", "C"])) == "right"
    assert read_escape_sequence("\x1b", _peek_from(["[", "D"])) == "left"


def test_read_escape_sequence_drains_parameterized_csi():
    # Home/End/PageUp style: \x1b[3~ — fully consumed through '~', single token, no leak.
    calls = {"n": 0}

    def peek():
        seq = ["[", "3", "~"]
        if calls["n"] < len(seq):
            v = seq[calls["n"]]
            calls["n"] += 1
            return v
        return None

    token = read_escape_sequence("\x1b", peek)
    assert calls["n"] == 3                       # drained exactly through the final '~'
    assert token == decode_key("\x1b[3~")        # unknown sequence -> consumed (no stray bytes)


def test_decode_key_named_and_arrows():
    assert decode_key("\r") == "enter"
    assert decode_key("\n") == "enter"
    assert decode_key("\x1b") == "esc"
    assert decode_key("\x7f") == "backspace"
    assert decode_key("\x03") == "ctrl-c"
    assert decode_key("\x1b[A") == "up"
    assert decode_key("\x1b[B") == "down"
    assert decode_key("\x1b[C") == "right"
    assert decode_key("\x1b[D") == "left"


def test_decode_key_printable_passthrough():
    assert decode_key("q") == "q"
    assert decode_key("R") == "R"
    assert decode_key("5") == "5"


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")
def test_terminal_read_key_decodes_arrows_over_real_pty():
    """Regression: reading via the raw fd (not buffered sys.stdin) so escape sequences like the
    down-arrow '\\x1b[B' are not misread as a bare Esc (which used to quit the menu on any arrow)."""
    import pty
    import tty

    master, slave = pty.openpty()
    tty.setcbreak(slave)
    term = Terminal()
    term._fd = slave
    term._is_windows = False
    try:
        cases = [
            (b"q", "q"),
            (b"\x1b[A", "up"),
            (b"\x1b[B", "down"),
            (b"\x1b[C", "right"),
            (b"\x1b[D", "left"),
            (b"\x1b", "esc"),       # bare Esc still resolves (after the peek timeout)
        ]
        for raw, want in cases:
            os.write(master, raw)
            assert term.read_key() == want, f"{raw!r} should decode to {want!r}"
    finally:
        os.close(master)
        os.close(slave)
