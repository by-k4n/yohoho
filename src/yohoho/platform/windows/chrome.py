"""Windows WindowChrome: borderless, non-activating (WS_EX_NOACTIVATE), translucent, pill-clipped panel.
All raw win32/ctypes calls are isolated behind the injectable `win32` facade so unit tests never touch
pywin32. Best-effort: any failure degrades to a plain borderless window — never crashes."""

# Extended window styles (win32con values, inlined to avoid importing win32con off-Windows).
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOOLWINDOW = 0x00000080
_GWL_EXSTYLE = -20


class _RealWin32:
    def get_ancestor_root(self, child_hwnd: int) -> int:
        import ctypes
        from ctypes import wintypes
        fn = ctypes.windll.user32.GetAncestor
        fn.argtypes = (wintypes.HWND, wintypes.UINT)
        fn.restype = wintypes.HWND  # explicit so the 64-bit handle is not truncated
        return fn(child_hwnd, 2)  # GA_ROOT

    def add_ex_styles(self, hwnd: int, styles: int) -> None:
        import win32gui
        cur = win32gui.GetWindowLong(hwnd, _GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, _GWL_EXSTYLE, cur | styles)

    def set_round_region(self, hwnd: int, w: int, h: int) -> None:
        import win32gui
        # ellipse w/h = h → radius h/2 → true stadium (matches the macOS pill).
        rgn = win32gui.CreateRoundRectRgn(0, 0, w + 1, h + 1, h, h)
        win32gui.SetWindowRgn(hwnd, rgn, True)


class WindowsWindowChrome:
    def __init__(self, *, win32=None, alpha: float = 0.96) -> None:
        self._w = win32 or _RealWin32()
        self._alpha = alpha

    def set_app_policy(self) -> None:
        pass  # DPI awareness is handled in core/ui/_dpi before tk.Tk(); nothing process-level here.

    def style_window(self, root, toplevel, canvas) -> None:
        # Always make it borderless+topmost first, so even a later failure leaves a usable panel.
        try:
            toplevel.overrideredirect(True)
            toplevel.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        try:
            toplevel.update_idletasks()  # the native wrapper must exist before we resolve it
            child = toplevel.winfo_id()
            hwnd = self._w.get_ancestor_root(child)  # the real top-level wrapper (NOT winfo_id)
            self._w.add_ex_styles(hwnd, _WS_EX_NOACTIVATE | _WS_EX_LAYERED | _WS_EX_TOOLWINDOW)
            toplevel.attributes("-alpha", self._alpha)  # uniform translucency (targets the wrapper)
            w, h = canvas.winfo_reqwidth(), canvas.winfo_reqheight()
            self._w.set_round_region(hwnd, w, h)  # PRIMARY pill shape on all Windows versions
        except Exception:  # noqa: BLE001 — degrade to the plain borderless window set above
            pass
