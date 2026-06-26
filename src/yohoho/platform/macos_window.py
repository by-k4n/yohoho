"""macOS window chrome for the status panel: frameless, always-on-top, and
(critically) non-focus-stealing so it floats over the app being dictated into.

Pure-Tk cannot prevent focus theft on macOS; we pair the IDLE-proven
MacWindowStyle 'help noActivates' with NSApplication accessory policy (pyobjc).
"""

from __future__ import annotations

import sys
import tkinter


def enable_round(top: "tkinter.Toplevel", canvas: "tkinter.Canvas") -> bool:
    """Make the window background transparent so a canvas-drawn rounded card shows
    rounded corners (macOS systemTransparent). Returns True on success, False if the
    platform/Tk doesn't support it (caller falls back to a square near-black card)."""
    try:
        top.wm_attributes("-transparent", True)
        top.configure(bg="systemTransparent")
        canvas.configure(bg="systemTransparent")
        return True
    except tkinter.TclError:
        return False


def set_accessory_policy() -> bool:
    """Make the process an accessory app (never frontmost, no Dock icon) so the
    panel can't steal keyboard focus. Call ONCE, AFTER tk.Tk() exists (importing
    AppKit before Tk creates its windows crashes Tk). Returns True if applied."""
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
        return True
    except Exception:
        return False


def apply_chrome(root, toplevel, alpha: float = 0.96) -> None:
    """Apply frameless + always-on-top + translucent + non-activating chrome.
    Set the non-activating style BEFORE the window is mapped."""
    if sys.platform == "darwin":
        try:
            root.tk.call(
                "::tk::unsupported::MacWindowStyle",
                "style",
                toplevel._w,
                "help",
                "noActivates",
            )
        except Exception:
            pass
    toplevel.overrideredirect(True)
    toplevel.attributes("-topmost", True)
    try:
        toplevel.attributes("-alpha", alpha)
    except Exception:
        pass


def place_bottom_center(root, toplevel, w: int, h: int, margin: int = 64) -> None:
    """Position the panel bottom-center of the primary display."""
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    toplevel.geometry(f"{w}x{h}+{(sw - w) // 2}+{sh - h - margin}")
