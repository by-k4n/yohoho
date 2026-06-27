"""Windows WindowChrome: borderless, non-activating, translucent, ANTI-ALIASED rounded "pill".

Tk's canvas on Windows draws via GDI, which does NOT anti-alias — so a `SetWindowRgn` round-rect clip
(or canvas-drawn corners) comes out visibly stair-stepped (macOS Tk uses Quartz, which AAs — that's why
it looks smooth there). We get smooth corners with a TWO-WINDOW per-pixel-alpha scheme:

  * Window A — the real Tk Toplevel/Canvas. We keep it INVISIBLE via `-alpha 0` (so its jagged GDI edges
    never show), but it stays composited, so `PrintWindow(A, PW_RENDERFULLCONTENT)` can read its full
    pixels every frame even while it's off-screen.
  * Window B — a bare layered popup OWNED by A (so the OS auto-destroys it with A — no leak, no teardown
    hook). Each frame we capture A, composite it under a precomputed anti-aliased rounded-rect alpha mask
    (numpy), and push the result to B with `UpdateLayeredWindow`. B mirrors A's screen rect, so the
    existing `show()`/`hide()` (which move A on/off-screen) need no changes — B follows A.

All raw win32/GDI lives behind the injectable `win32` facade so unit tests never touch pywin32; the mask
and compositing are pure numpy (already a runtime dependency). Best-effort throughout — any failure
degrades to an (invisible) plain window and never crashes the panel."""

import ctypes
from ctypes import wintypes

# Extended/standard window styles (inlined to avoid importing win32con off-Windows).
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOPMOST = 0x00000008
_WS_POPUP = 0x80000000
_GWL_EXSTYLE = -20
_PW_RENDERFULLCONTENT = 0x02
_ULW_ALPHA = 0x02
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_BI_RGB = 0
_DIB_RGB_COLORS = 0
_SW_SHOWNOACTIVATE = 4

# Compositing refresh cadence (ms) — matches the panel's ~55ms render tick (~18fps).
_REFRESH_MS = 55


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def _bmi_topdown(w: int, h: int) -> _BITMAPINFO:
    b = _BITMAPINFO()
    b.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    b.bmiHeader.biWidth = w
    b.bmiHeader.biHeight = -h  # top-down
    b.bmiHeader.biPlanes = 1
    b.bmiHeader.biBitCount = 32
    b.bmiHeader.biCompression = _BI_RGB
    return b


def _capsule_mask(w: int, h: int, ss: int = 4):
    """Anti-aliased stadium (pill) coverage in [0, 1], shape (h, w). `ss`x supersampled."""
    import numpy as np
    r = h / 2.0
    cx_lo, cx_hi, cy = r, w - r, h / 2.0
    xs = (np.arange(w * ss) + 0.5) / ss
    ys = (np.arange(h * ss) + 0.5) / ss
    x, y = np.meshgrid(xs.astype(np.float32), ys.astype(np.float32))
    clx = np.clip(x, cx_lo, cx_hi)  # nearest point on the capsule spine
    inside = (np.hypot(x - clx, y - cy) <= r).astype(np.float32)
    return inside.reshape(h, ss, w, ss).mean(axis=(1, 3))  # box-downsample → AA coverage


def _compose_premul(bgra, mask, alpha):
    """Premultiplied top-down BGRA = captured colors * (AA coverage * uniform translucency)."""
    import numpy as np
    a_f = (mask * alpha).astype(np.float32)
    out = np.empty(bgra.shape, np.uint8)
    bgr = bgra[:, :, :3].astype(np.float32)
    out[:, :, 0] = bgr[:, :, 0] * a_f
    out[:, :, 1] = bgr[:, :, 1] * a_f
    out[:, :, 2] = bgr[:, :, 2] * a_f
    out[:, :, 3] = a_f * 255.0
    return np.ascontiguousarray(out)


class _RealWin32:
    """The real win32/GDI surface. Construction is cheap and touches no windll (so the macOS bundle's
    factory tests can build a Windows bundle); every method imports pywin32/ctypes lazily."""

    def get_ancestor_root(self, child_hwnd: int) -> int:
        fn = ctypes.windll.user32.GetAncestor
        fn.argtypes = (wintypes.HWND, wintypes.UINT)
        fn.restype = wintypes.HWND  # explicit so the 64-bit handle is not truncated
        return fn(child_hwnd, 2)  # GA_ROOT

    def add_ex_styles(self, hwnd: int, styles: int) -> None:
        import win32gui
        cur = win32gui.GetWindowLong(hwnd, _GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, _GWL_EXSTYLE, cur | styles)

    def get_window_rect(self, hwnd: int):
        import win32gui
        return win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)

    def create_display_window(self, owner_hwnd: int, w: int, h: int) -> int:
        """Create the on-screen mirror window B (owned by A) + the reusable capture/push GDI surfaces."""
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        # Full restype/argtypes so 64-bit handles are passed/returned as pointers, not truncated to
        # c_int (a large handle otherwise raises "int too long to convert"). Handles = c_void_p.
        vp, dword, uint, cint, wstr = ctypes.c_void_p, wintypes.DWORD, wintypes.UINT, ctypes.c_int, ctypes.c_wchar_p
        u.CreateWindowExW.restype = vp
        u.CreateWindowExW.argtypes = (dword, wstr, wstr, dword, cint, cint, cint, cint, vp, vp, vp, vp)
        u.ShowWindow.argtypes = (vp, cint)
        u.GetDC.restype = vp
        u.GetDC.argtypes = (vp,)
        u.PrintWindow.argtypes = (vp, vp, uint)
        u.UpdateLayeredWindow.argtypes = (vp, vp, vp, vp, vp, vp, dword, vp, dword)
        g.CreateCompatibleDC.restype = vp
        g.CreateCompatibleDC.argtypes = (vp,)
        g.CreateCompatibleBitmap.restype = vp
        g.CreateCompatibleBitmap.argtypes = (vp, cint, cint)
        g.SelectObject.restype = vp
        g.SelectObject.argtypes = (vp, vp)
        g.CreateDIBSection.restype = vp
        g.CreateDIBSection.argtypes = (vp, vp, uint, ctypes.POINTER(ctypes.c_void_p), vp, dword)
        g.GetDIBits.argtypes = (vp, vp, uint, uint, vp, vp, uint)
        ex = _WS_EX_LAYERED | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE | _WS_EX_TRANSPARENT | _WS_EX_TOPMOST
        b = u.CreateWindowExW(ex, "Static", "yohoho-aa", _WS_POPUP, 0, 0, w, h, owner_hwnd, 0, 0, 0)
        u.ShowWindow(b, _SW_SHOWNOACTIVATE)
        scr = u.GetDC(0)
        self._u, self._g = u, g
        self._wh = (w, h)
        self._cap_mem = g.CreateCompatibleDC(scr)
        self._cap_bmp = g.CreateCompatibleBitmap(scr, w, h)
        g.SelectObject(self._cap_mem, self._cap_bmp)
        self._cap_buf = (ctypes.c_ubyte * (w * h * 4))()
        self._cap_bmi = _bmi_topdown(w, h)
        self._push_screen = scr
        self._push_mem = g.CreateCompatibleDC(scr)
        self._ppv = ctypes.c_void_p()
        self._push_dib = g.CreateDIBSection(scr, ctypes.byref(_bmi_topdown(w, h)),
                                            _DIB_RGB_COLORS, ctypes.byref(self._ppv), None, 0)
        g.SelectObject(self._push_mem, self._push_dib)
        return b

    def capture_bgra(self, a_hwnd: int):
        """Read window A's full content (even off-screen / -alpha 0) via PrintWindow → (h, w, 4) BGRA."""
        import numpy as np
        w, h = self._wh
        self._u.PrintWindow(a_hwnd, self._cap_mem, _PW_RENDERFULLCONTENT)
        self._g.GetDIBits(self._cap_mem, self._cap_bmp, 0, h, self._cap_buf,
                          ctypes.byref(self._cap_bmi), _DIB_RGB_COLORS)
        return np.frombuffer(self._cap_buf, np.uint8).reshape(h, w, 4)

    def update_layered(self, b_hwnd: int, x: int, y: int, premul) -> None:
        """Push premultiplied top-down BGRA onto B at screen (x, y) with UpdateLayeredWindow."""
        w, h = self._wh
        ctypes.memmove(self._ppv, premul.ctypes.data, w * h * 4)
        pd = wintypes.POINT(x, y)
        sz = wintypes.SIZE(w, h)
        ps = wintypes.POINT(0, 0)
        bl = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
        self._u.UpdateLayeredWindow(b_hwnd, self._push_screen, ctypes.byref(pd), ctypes.byref(sz),
                                    self._push_mem, ctypes.byref(ps), 0, ctypes.byref(bl), _ULW_ALPHA)


class WindowsWindowChrome:
    def __init__(self, *, win32=None, alpha: float = 0.96) -> None:
        self._w = win32 or _RealWin32()
        self._alpha = alpha
        self._a_hwnd = None
        self._b_hwnd = None
        self._mask = None

    def set_app_policy(self) -> None:
        pass  # DPI awareness is handled in core/ui/_dpi before tk.Tk(); nothing process-level here.

    def style_window(self, root, toplevel, canvas) -> None:
        # Borderless + topmost first, so even a later failure leaves a usable (if plain) window.
        try:
            toplevel.overrideredirect(True)
            toplevel.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        try:
            toplevel.update_idletasks()
            toplevel.attributes("-alpha", 0.0)  # A invisible; mirror window B shows the AA pill
            toplevel.update_idletasks()
            self._a_hwnd = self._w.get_ancestor_root(toplevel.winfo_id())  # top-level wrapper (NOT winfo_id)
            self._w.add_ex_styles(self._a_hwnd, _WS_EX_NOACTIVATE | _WS_EX_LAYERED
                                  | _WS_EX_TOOLWINDOW | _WS_EX_TRANSPARENT)
            w, h = canvas.winfo_reqwidth(), canvas.winfo_reqheight()
            self._mask = _capsule_mask(w, h)  # cached once (depends only on size)
            self._b_hwnd = self._w.create_display_window(self._a_hwnd, w, h)  # owned by A → auto-freed
            toplevel.after(0, self._refresh, toplevel)  # drive AA compositing on the Tk main thread
        except Exception:  # noqa: BLE001 — degrade to the plain (invisible) window set above
            pass

    def _refresh(self, toplevel) -> None:
        """One compositing frame: capture A → AA-mask + translucency → present on B at A's rect; reschedule."""
        try:
            if not toplevel.winfo_exists():
                return  # window destroyed — stop the loop (B is auto-freed as A's owned window)
        except Exception:  # noqa: BLE001
            return
        if self._a_hwnd is not None and self._b_hwnd is not None:
            try:
                left, top, _r, _b = self._w.get_window_rect(self._a_hwnd)
                out = _compose_premul(self._w.capture_bgra(self._a_hwnd), self._mask, self._alpha)
                self._w.update_layered(self._b_hwnd, left, top, out)
            except Exception:  # noqa: BLE001 — never let one bad frame crash the panel
                pass
        try:
            toplevel.after(_REFRESH_MS, self._refresh, toplevel)
        except Exception:  # noqa: BLE001
            pass
