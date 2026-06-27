"""Windows platform bundle: mirrors make_macos_platform. Records an ABSOLUTE pythonw.exe for autostart
(derived from sys.executable, like the macOS adapter records sys.executable)."""
import sys
from pathlib import Path
from yohoho.core.platform_api import PlatformBundle
from yohoho.platform.windows.hotkey import WindowsHotkeyListener
from yohoho.platform.windows.clipboard import WindowsClipboard
from yohoho.platform.windows.inject import WindowsTextInjector
from yohoho.platform.windows.focus import WindowsFocusProbe
from yohoho.platform.windows.autostart import WindowsAutostart
from yohoho.platform.windows.permissions import WindowsPermissions
from yohoho.platform.windows.chrome import WindowsWindowChrome
from yohoho.platform._shared.hotkey_capture import PynputHotkeyCapturer


def _pythonw_path() -> str:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw) if pyw.exists() else sys.executable  # fallback: launches with a console, not silent fail


def make_windows_platform() -> PlatformBundle:
    return PlatformBundle(
        name="windows",
        hotkeys=WindowsHotkeyListener(),
        clipboard=WindowsClipboard(),
        injector=WindowsTextInjector(),
        focus=WindowsFocusProbe(),
        autostart=WindowsAutostart(program_args=[_pythonw_path(), "-m", "yohoho", "start"]),
        permissions=WindowsPermissions(),
        window_chrome=WindowsWindowChrome(),
        hotkey_capturer=PynputHotkeyCapturer(),
    )
