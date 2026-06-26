from __future__ import annotations

import sys

from yohoho.core.platform_api import PlatformBundle
from yohoho.platform.macos.hotkey import MacHotkeyListener
from yohoho.platform.macos.clipboard import MacClipboard
from yohoho.platform.macos.inject import MacTextInjector
from yohoho.platform.macos.focus import MacFocusProbe
from yohoho.platform.macos.autostart import MacAutostart
from yohoho.platform.macos.permissions import MacPermissions


def make_macos_platform() -> PlatformBundle:
    from yohoho.core.config import data_dir as _resolve_data_dir, load_config
    _dd = _resolve_data_dir()

    def _recorded_python_path():
        try:
            p = load_config(_dd / "config.yaml").macos.get("granted_python_path") or ""
        except Exception:
            p = ""
        return p or None  # "" (never recorded) -> None

    return PlatformBundle(
        name="macos",
        hotkeys=MacHotkeyListener(),
        clipboard=MacClipboard(),
        injector=MacTextInjector(),
        focus=MacFocusProbe(),
        autostart=MacAutostart(
            program_args=[sys.executable, "-m", "yohoho", "start"], log_dir=_dd
        ),
        permissions=MacPermissions(recorded_path_fn=_recorded_python_path),
    )
