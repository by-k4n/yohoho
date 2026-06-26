"""Per-monitor DPI awareness for the Tk panel on Windows. Must run BEFORE the first tk.Tk().
No-op on every non-Windows platform. The real shcore/user32 calls are isolated in
_real_set_dpi_awareness so they only ever execute on win32 and unit tests never touch ctypes.windll."""
import sys


def _real_set_dpi_awareness() -> None:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # SYSTEM_DPI_AWARE (safest for Tk 8.6)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()  # legacy fallback


def ensure_dpi_awareness(platform: str = sys.platform, set_awareness=_real_set_dpi_awareness) -> None:
    if platform == "win32":
        try:
            set_awareness()
        except Exception:  # noqa: BLE001 — DPI is best-effort; never block startup
            pass
