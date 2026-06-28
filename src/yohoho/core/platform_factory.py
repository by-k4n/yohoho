from __future__ import annotations

import sys

from yohoho.core.platform_api import PlatformBundle
from yohoho.core.null_platform import make_null_platform


def get_process_controller():
    """Return the OS-appropriate ProcessController only.

    Avoids building the full PlatformBundle and pulling in the heavy native
    dependencies (pyobjc frameworks, onnxruntime, sounddevice). Note: on macOS
    this still imports the lightweight ``yohoho.platform.macos`` adapter package,
    which loads tkinter and the thin macos adapters (~tens of ms) — that is a
    tracked follow-up, not the expensive native stack.
    """
    if sys.platform == "darwin":
        from yohoho.platform.macos.process import MacProcessController
        return MacProcessController()
    if sys.platform == "win32":
        from yohoho.platform.windows.process import WindowsProcessController
        return WindowsProcessController()
    from yohoho.core.null_platform import NullProcessController
    return NullProcessController()


def get_platform() -> PlatformBundle:
    """The ONLY core module that imports yohoho.platform.*."""
    if sys.platform == "darwin":
        from yohoho.platform.macos import make_macos_platform
        return make_macos_platform()
    if sys.platform == "win32":
        from yohoho.platform.windows import make_windows_platform
        return make_windows_platform()
    return make_null_platform()
