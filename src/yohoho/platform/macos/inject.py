from __future__ import annotations

import time
from typing import Callable, Optional

from yohoho.core.platform_api import FocusToken
from yohoho.platform.macos import _appkit


def _real_controller():
    from pynput.keyboard import Controller
    return Controller()


class MacTextInjector:
    def __init__(
        self,
        controller=None,
        *,
        activate_fn: Callable[[str], bool] = _appkit.activate_bundle,
        frontmost_fn: Callable[[], str] = _appkit.frontmost_bundle_id,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        # None = resolve the real pynput Controller lazily on first use (so constructing
        # the adapter never imports pynput); an injected controller is used as-is.
        self._ctl = controller
        self._activate = activate_fn
        self._frontmost = frontmost_fn
        self._sleep = sleep_fn

    def _get_ctl(self):
        if self._ctl is None:
            self._ctl = _real_controller()
        return self._ctl

    def _reactivate(self, token: Optional[FocusToken]) -> None:
        """Bring the record-stop target app back to the front before pasting.

        Showing our accessory panel can make THIS process the active app, so a
        synthetic Cmd+V would land on us.  Re-activate the target and wait (briefly)
        until it is actually frontmost so the keystroke is delivered there.
        """
        app_id = getattr(token, "app_id", None) if token is not None else None
        if not app_id or app_id == "unknown":
            return
        self._activate(app_id)
        # Poll briefly for the activation to register. This runs on the Tk MAIN
        # thread (paste is marshalled), so the budget is kept small (~120ms) to
        # cap how long the panel render can stall; activate() is usually fast.
        for _ in range(12):
            if self._frontmost() == app_id:
                return
            self._sleep(0.01)

    def paste(self, token: Optional[FocusToken] = None) -> bool:
        try:
            self._reactivate(token)
            try:
                from pynput.keyboard import Key, KeyCode
                v = KeyCode.from_vk(0x09)  # kVK_ANSI_V — layout-robust physical key
                cmd = Key.cmd
            except Exception:
                # from_vk unsupported on this layout/platform (or pynput not yet installed): fall back
                v = "v"
                cmd = "cmd"
            with self._get_ctl().pressed(cmd):
                self._get_ctl().press(v)
                self._get_ctl().release(v)
            return True
        except Exception:
            return False

    def release_modifiers(self) -> None:
        try:
            from pynput.keyboard import Key
            mods = (Key.cmd, Key.ctrl, Key.alt, Key.shift)
        except Exception:
            mods = ()
        for m in mods:
            try:
                self._get_ctl().release(m)
            except Exception:
                pass
