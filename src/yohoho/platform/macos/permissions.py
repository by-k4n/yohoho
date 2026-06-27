"""macOS permission checks (Input Monitoring, Accessibility) — native calls behind injectable seams."""
from __future__ import annotations
import os
import sys
from typing import Callable, Optional
from yohoho.core.platform_api import Permission, PermissionStatus
from yohoho.platform.macos import _appkit

_IM_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
_AX_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

# TERM_PROGRAM values -> the friendly app name macOS attributes the TCC grant to.
_TERM_NAMES = {
    "Apple_Terminal": "Apple Terminal",
    "iTerm.app": "iTerm",
    "vscode": "VS Code",
    "Hyper": "Hyper",
    "WezTerm": "WezTerm",
    "ghostty": "Ghostty",
    "Tabby": "Tabby",
    "tmux": "your terminal app",
}


def responsible_app_name(term_program: str) -> str:
    """Friendly name of the app macOS attaches the TCC grant to (the launching terminal).

    macOS binds Accessibility / Input-Monitoring trust to the *responsible process* — the
    terminal yohoho was launched from, not the uv-managed Python — so the "enable X" entry
    in System Settings is the terminal. Naming it makes the instruction concrete. Falls back
    to a generic phrase when TERM_PROGRAM is unset (ssh / tmux / unknown).
    """
    tp = (term_program or "").strip()
    if not tp:
        return "your terminal app"
    if tp in _TERM_NAMES:
        return _TERM_NAMES[tp]
    cleaned = tp.replace(".app", "").replace("_", " ").strip()
    return cleaned or "your terminal app"


class MacPermissions:
    def __init__(self,
                 input_monitoring_fn: Callable[[], str] = _appkit.input_monitoring_state,
                 accessibility_fn: Callable[[], bool] = _appkit.accessibility_trusted,
                 recorded_path_fn: Callable[[], Optional[str]] = lambda: None,
                 current_path: str = sys.executable,
                 open_fn: Callable[[str], None] = _appkit.open_url,
                 im_request_fn: Callable[[], None] = _appkit.input_monitoring_request,
                 ax_request_fn: Optional[Callable[[], None]] = None,
                 term_program_fn: Callable[[], str] = lambda: os.environ.get("TERM_PROGRAM", "")) -> None:
        self._im, self._ax = input_monitoring_fn, accessibility_fn
        self._recorded, self._current, self._open = recorded_path_fn, current_path, open_fn
        self._im_request = im_request_fn
        # default fires the Accessibility prompt via the existing _appkit function
        self._ax_request = ax_request_fn or (lambda: _appkit.accessibility_trusted(prompt=True))
        self._term_program = term_program_fn

    def check(self) -> PermissionStatus:
        im_state = self._im()                                  # "granted"|"denied"|"unknown"
        ax_state = "granted" if self._ax() else "denied"
        perms = (
            Permission(key="input_monitoring", state=im_state, label="Input Monitoring",
                       fix_hint="Enable yohoho under Input Monitoring.", deep_link=_IM_PANE),
            Permission(key="accessibility", state=ax_state, label="Accessibility",
                       fix_hint="Enable yohoho under Accessibility.", deep_link=_AX_PANE),
        )
        recorded = self._recorded()
        identity_ok = (recorded is None) or (recorded == self._current)
        ok = all(p.state == "granted" for p in perms) and identity_ok
        return PermissionStatus(ok=ok, permissions=perms, identity_ok=identity_ok)

    def request(self) -> None:
        """Fire the native OS prompt for each not-yet-granted permission, then open its pane.

        Triggering the prompt is what makes macOS show the dialog AND list a toggle entry to
        enable; opening the pane is the fallback so the user can finish in System Settings.
        """
        for p in self.check().permissions:
            if p.state == "granted":
                continue
            if p.key == "input_monitoring":
                self._im_request()
            elif p.key == "accessibility":
                self._ax_request()
            if p.deep_link:
                self._open(p.deep_link)

    def guide(self) -> str:
        app = responsible_app_name(self._term_program())
        return ("yohoho needs two macOS permissions:\n"
                "  • Input Monitoring — to hear your hotkey\n"
                "  • Accessibility — to paste into the focused app\n"
                f"In System Settings ▸ Privacy & Security, enable **{app}** under each pane "
                "(macOS grants these to the app you launched yohoho from), then re-run `yohoho setup`.")
