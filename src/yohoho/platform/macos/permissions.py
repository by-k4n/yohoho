"""macOS permission checks (Input Monitoring, Accessibility) — native calls behind injectable seams."""
from __future__ import annotations
import sys
from typing import Callable, Optional
from yohoho.core.platform_api import Permission, PermissionStatus
from yohoho.platform.macos import _appkit

_IM_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
_AX_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


class MacPermissions:
    def __init__(self,
                 input_monitoring_fn: Callable[[], str] = _appkit.input_monitoring_state,
                 accessibility_fn: Callable[[], bool] = _appkit.accessibility_trusted,
                 recorded_path_fn: Callable[[], Optional[str]] = lambda: None,
                 current_path: str = sys.executable,
                 open_fn: Callable[[str], None] = _appkit.open_url) -> None:
        self._im, self._ax = input_monitoring_fn, accessibility_fn
        self._recorded, self._current, self._open = recorded_path_fn, current_path, open_fn

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
        st = self.check()
        for p in st.permissions:
            if p.state != "granted" and p.deep_link:
                self._open(p.deep_link)

    def guide(self) -> str:
        return ("yohoho needs two macOS permissions:\n"
                "  • Input Monitoring — to hear your hotkey\n"
                "  • Accessibility — to paste into the focused app\n"
                "System Settings ▸ Privacy & Security ▸ (each pane), enable yohoho, then re-check.")
