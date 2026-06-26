"""Real macOS native calls, each isolated so unit tests fake them. pyobjc is
imported lazily INSIDE each function (never at module top): safe to import this
module on any OS, and AppKit must not load before the Tk root exists (M2 rule)."""
from __future__ import annotations
import subprocess


def input_monitoring_state() -> str:
    """'granted' | 'denied' for Input Monitoring via CGPreflightListenEventAccess().

    Confirmed against the installed pyobjc: `IOHIDCheckAccess` is not exposed in
    Quartz; the CoreGraphics preflight is the available API and returns a plain
    bool (no 'unknown' tristate).
    """
    import Quartz  # pyobjc-framework-Quartz
    return "granted" if Quartz.CGPreflightListenEventAccess() else "denied"


def input_monitoring_request() -> None:
    """Trigger the Input Monitoring permission prompt (CGRequestListenEventAccess)."""
    import Quartz
    Quartz.CGRequestListenEventAccess()


def accessibility_trusted(prompt: bool = False) -> bool:
    """AXIsProcessTrusted[/WithOptions]."""
    import ApplicationServices as AS
    if prompt:
        opts = {AS.kAXTrustedCheckOptionPrompt: True}
        return bool(AS.AXIsProcessTrustedWithOptions(opts))
    return bool(AS.AXIsProcessTrusted())


def frontmost_bundle_id() -> str:
    import AppKit
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return (app.bundleIdentifier() if app else None) or "unknown"


def current_bundle_id() -> str:
    """Bundle id of THIS process (the yohoho app).  Often 'unknown' for a bare
    `python` interpreter — used to recognise when our own panel is the frontmost
    app so we don't mistake it for the user switching apps."""
    import AppKit
    app = AppKit.NSRunningApplication.currentApplication()
    return (app.bundleIdentifier() if app else None) or "unknown"


def activate_bundle(bundle_id: str) -> bool:
    """Bring the running app with *bundle_id* to the front (re-key it).

    Used right before pasting: showing our accessory panel can make THIS process
    the active app, so a synthetic Cmd+V would land on us, not the user's app.
    Re-activating the target app first makes the keystroke land where intended.
    """
    import AppKit
    ws = AppKit.NSWorkspace.sharedWorkspace()
    for app in ws.runningApplications():
        if app.bundleIdentifier() == bundle_id:
            try:
                return bool(app.activate())  # macOS 14+
            except Exception:
                opt = getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 1 << 1)
                return bool(app.activateWithOptions_(opt))
    return False


def pasteboard_get() -> str | None:
    import AppKit
    pb = AppKit.NSPasteboard.generalPasteboard()
    return pb.stringForType_(AppKit.NSPasteboardTypeString)


def pasteboard_set(text: str) -> None:
    import AppKit
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)


def pasteboard_has_nontext() -> bool:
    import AppKit
    pb = AppKit.NSPasteboard.generalPasteboard()
    types = list(pb.types() or [])
    return bool(types) and AppKit.NSPasteboardTypeString not in types


def open_url(url: str) -> None:
    subprocess.run(["open", url], check=False)
