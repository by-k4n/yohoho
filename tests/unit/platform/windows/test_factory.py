from yohoho.platform.windows import make_windows_platform
from yohoho.platform.windows.chrome import WindowsWindowChrome
from yohoho.core.platform_api import PlatformBundle


def test_make_windows_platform_returns_full_bundle():
    import sys
    b = make_windows_platform()
    assert isinstance(b, PlatformBundle) and b.name == "windows"
    assert isinstance(b.window_chrome, WindowsWindowChrome)
    # Cross-platform assertion: quoted interpreter + module. (On non-Windows hosts _pythonw_path()
    # falls back to sys.executable, which is not "pythonw" — so only assert pythonw under win32.)
    cmd = b.autostart._cmd
    assert cmd.startswith('"') and cmd.endswith("-m yohoho start")
    if sys.platform == "win32":
        assert "pythonw" in cmd.lower()
