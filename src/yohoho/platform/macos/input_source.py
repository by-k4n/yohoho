"""Work around a pynput × macOS Tahoe (26) incompatibility.

macOS 26 made the Text Services input-source API (``TISCopyCurrentKeyboardInputSource``)
**main-thread only** — any off-main call aborts the whole process with SIGTRAP and
no catchable exception.  pynput's keyboard ``Listener`` calls it (via
``keycode_context``) on its own listener thread when it starts, which crashes us —
reliably once the status panel is being drawn on the main thread.

``prewarm_keyboard_layout`` computes that context ONCE on the main thread and
patches pynput to reuse the cached value, so the listener thread (and anything
else) never touches the input-source API off-main again.  It MUST be called on the
main thread, before the hotkey listener starts.
"""

from __future__ import annotations

import contextlib

_patched = False


def _install(cached, targets) -> None:
    """Replace ``keycode_context`` on each target module with one yielding *cached*."""

    @contextlib.contextmanager
    def cached_context():
        yield cached

    for module in targets:
        module.keycode_context = cached_context


def prewarm_keyboard_layout() -> bool:
    """Cache pynput's keyboard-layout context from THIS (main) thread; patch pynput.

    Idempotent and best-effort: returns True once the patch is installed, False if
    pynput isn't importable.  Safe to call on non-macOS (it just no-ops to False if
    the darwin backend is absent).
    """
    global _patched
    if _patched:
        return True
    try:
        import pynput._util.darwin as _util_darwin
        import pynput.keyboard._darwin as _keyboard_darwin

        # Compute on the current (main) thread, where the TSM call is legal.
        with _util_darwin.keycode_context() as ctx:
            cached = ctx

        _install(cached, (_util_darwin, _keyboard_darwin))
        _patched = True
        return True
    except Exception:
        return False
