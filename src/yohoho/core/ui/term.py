"""Minimal raw-mode terminal + ANSI helper for the config TUI. The Terminal class is a thin
OS adapter (termios on POSIX, msvcrt on Windows); decode_key is pure and unit-tested."""
from __future__ import annotations

import sys

_KEYS = {
    "\r": "enter", "\n": "enter", "\x1b": "esc", "\x7f": "backspace", "\x08": "backspace",
    "\x03": "ctrl-c", "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
}

_CYAN = "\x1b[38;5;44m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"

# How long to wait for the tail of an escape sequence after reading ESC before treating it as a
# bare Esc. Long enough to absorb byte-at-a-time delivery (SSH), short enough to feel instant.
_ESC_PEEK_TIMEOUT = 0.05


def decode_key(seq: str) -> str:
    """Map a raw key byte-sequence to a normalized token; printables pass through."""
    return _KEYS.get(seq, seq)


def read_escape_sequence(first: str, peek_next) -> str:
    """Given the first char already read ('\\x1b') and peek_next() -> str|None (next byte
    if ready, else None), return the normalized key token, fully draining the sequence.

    A bare Esc (nothing ready) -> 'esc'. A CSI/SS3 intro ('[' or 'O') is read up to and
    including its final byte (0x40-0x7e); the assembled sequence is passed to decode_key, so
    arrows map to up/down/left/right and any other sequence is consumed (returns '' if unknown)."""
    nxt = peek_next()
    if nxt is None:                          # nothing follows -> true bare Esc
        return "esc"
    seq = first + nxt
    if nxt in ("[", "O"):
        while True:
            b = peek_next()
            if b is None:
                break
            seq += b
            if "\x40" <= b <= "\x7e":        # CSI/SS3 final byte
                break
    return decode_key(seq)


def cyan(s: str) -> str:
    """Wrap text in the brand cyan (#39BFC6 ~ 256-color 44)."""
    return f"{_CYAN}{s}{_RESET}"


def dim(s: str) -> str:
    """Wrap text in dim/faint."""
    return f"{_DIM}{s}{_RESET}"


class Terminal:
    """Context manager: enters cbreak/raw mode on __enter__, guarantees restore on __exit__
    (including exceptions / Ctrl-C). read_key() returns a decode_key token; render() paints a frame.

    POSIX: termios.cbreak via tty.setcbreak; reads 1 byte, and on ESC greedily reads up to 2 more
    for arrow sequences (\\x1b[A..D). Windows: msvcrt.getwch; arrow keys arrive as a \\xe0/\\x00 prefix
    + a code which is normalized to up/down/left/right.

    Rendering: ANSI — hide cursor on enter, show on exit; render() does '\\x1b[2J\\x1b[H' then prints
    the given lines. Colors via cyan()/dim()."""

    def __init__(self) -> None:
        self._is_windows = sys.platform == "win32"
        self._fd = None
        self._saved = None

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> "Terminal":
        if not self._is_windows:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        # hide cursor
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Restore terminal state unconditionally (normal exit, exception, Ctrl-C).
        try:
            if not self._is_windows and self._fd is not None and self._saved is not None:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        finally:
            # show cursor again no matter what
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        return False  # never suppress exceptions

    # -- input ------------------------------------------------------------
    def read_key(self) -> str:
        if self._is_windows:
            return self._read_key_windows()
        return self._read_key_posix()

    def _read_key_posix(self) -> str:
        # Read straight from the raw fd, NOT buffered sys.stdin: a TextIOWrapper would buffer
        # the tail of an escape sequence (e.g. the '[B' of a down-arrow) where select() on the
        # fd can't see it, so the sequence would be misread as a bare Esc and quit the menu.
        import os
        import select

        b = os.read(self._fd, 1)
        if not b:
            return ""  # EOF
        ch = b.decode("latin-1")
        if ch == "\x1b":
            def peek_next():
                # Small timeout (not 0) so the rest of an escape sequence has time to arrive even
                # if the terminal delivers it a byte at a time (e.g. over SSH); a true bare Esc
                # waits this out and returns None.
                if not select.select([self._fd], [], [], _ESC_PEEK_TIMEOUT)[0]:
                    return None
                nb = os.read(self._fd, 1)
                return nb.decode("latin-1") if nb else None

            return read_escape_sequence(ch, peek_next)
        return decode_key(ch)

    def drain_input(self) -> None:
        """Non-blockingly discard any pending input. Used after a global hotkey capture, where
        the same physical keys were also queued on the tty and would otherwise leak into the next
        read_key(). Never raises out of the menu (best-effort)."""
        try:
            if self._is_windows:
                import msvcrt

                while msvcrt.kbhit():
                    msvcrt.getwch()
            elif self._fd is not None:
                import os
                import select

                while select.select([self._fd], [], [], 0)[0]:
                    os.read(self._fd, 4096)
        except Exception:
            pass

    def _read_key_windows(self) -> str:
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            # arrow / function key prefix; next code identifies the key
            code = msvcrt.getwch()
            return {"H": "up", "P": "down", "M": "right", "K": "left"}.get(code, "")
        return decode_key(ch)

    # -- output -----------------------------------------------------------
    def render(self, lines: list[str]) -> None:
        # clear screen + home cursor, then paint the frame
        out = "\x1b[2J\x1b[H" + "\r\n".join(lines) + "\r\n"
        sys.stdout.write(out)
        sys.stdout.flush()

    # -- color helpers ----------------------------------------------------
    def cyan(self, s: str) -> str:
        return cyan(s)

    def dim(self, s: str) -> str:
        return dim(s)
