from __future__ import annotations
from typing import Protocol, runtime_checkable, Callable, Literal, Optional
from dataclasses import dataclass, field

# Normalized OS-agnostic hotkey, stored in config: lowercase, '+'-joined, modifiers first.
HotkeySpec = str  # e.g. 'ctrl+alt+space', 'f14'
ActivateCallback = Callable[[], None]


@dataclass(frozen=True)
class FocusToken:
    gen: int
    app_id: str = "null"
    valid: bool = True


@runtime_checkable
class HotkeyListener(Protocol):
    def configure(
        self,
        spec: HotkeySpec,
        on_activate: ActivateCallback,
        on_cancel: Optional[ActivateCallback] = None,
    ) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_alive(self) -> bool: ...
    @staticmethod
    def is_valid_spec(spec: HotkeySpec) -> bool: ...


@runtime_checkable
class Clipboard(Protocol):
    def get_text(self) -> Optional[str]: ...
    def set_text(self, text: str) -> None: ...
    def has_nontext(self) -> bool: ...


@runtime_checkable
class TextInjector(Protocol):
    # token: the record-stop focus snapshot. Adapters that must re-target the
    # original app before pasting (macOS) use it; others may ignore it.
    def paste(self, token: Optional["FocusToken"] = None) -> bool: ...
    def release_modifiers(self) -> None: ...


@runtime_checkable
class FocusProbe(Protocol):
    def snapshot(self) -> FocusToken: ...
    def unchanged(self, token: FocusToken) -> bool: ...


@runtime_checkable
class AutostartManager(Protocol):
    def enable(self) -> bool: ...   # True iff autostart is active afterward
    def disable(self) -> None: ...
    def is_enabled(self) -> bool: ...


PermState = Literal["granted", "denied", "not_applicable", "unknown"]


@dataclass(frozen=True)
class Permission:
    key: str
    state: PermState
    label: str
    fix_hint: str
    deep_link: str = ""


@dataclass(frozen=True)
class PermissionStatus:
    ok: bool
    permissions: tuple[Permission, ...]
    identity_ok: bool = True


@runtime_checkable
class PermissionsManager(Protocol):
    def check(self) -> PermissionStatus: ...
    def request(self) -> None: ...
    def guide(self) -> str: ...


@runtime_checkable
class WindowChrome(Protocol):
    """Process- and window-level styling for the status panel (the only OS-specific UI seam)."""

    # Per-OS panel sizing, read once at StatusPanel construction. The panel derives
    # every position from these, so a per-OS value can never overlap, clip, or break
    # another OS. `preferred_panel_width` is logical px (mac/null 280; win 300 — Doto's
    # timer is wider on Windows GDI). `panel_scale` is the DPI multiplier applied to all
    # geometry (mac/null 1.0 — Tk already renders in points; win = system DPI / 96).
    preferred_panel_width: int
    panel_scale: float

    def set_app_policy(self) -> None: ...  # process-level (mac: accessory; win/null: no-op)

    def style_window(self, root, toplevel, canvas) -> None: ...  # borderless/topmost/non-activating/round


class NullWindowChrome:
    """Plain borderless top-most window; no platform tricks. Used by the null platform and as
    the headless default so existing PlatformBundle/StatusPanel/PanelRunner constructions stay valid."""

    preferred_panel_width: int = 280   # the macOS-tuned width; the safe default for every OS
    panel_scale: float = 1.0           # no DPI multiply (Tk points / headless)

    def set_app_policy(self) -> None:
        pass

    def style_window(self, root, toplevel, canvas) -> None:
        try:
            toplevel.overrideredirect(True)
            toplevel.attributes("-topmost", True)
        except Exception:  # noqa: BLE001 — never crash the panel on chrome
            pass


@runtime_checkable
class HotkeyCapturer(Protocol):
    def capture(
        self, seconds: float = 3.0, on_progress: Optional[Callable[[float], None]] = None
    ) -> Optional[str]: ...


class NullHotkeyCapturer:
    """Default capturer: capture unavailable (headless / no listener). Callers fall back to typed entry."""

    def capture(self, seconds: float = 3.0, on_progress=None) -> Optional[str]:
        return None


@runtime_checkable
class ProcessController(Protocol):
    def spawn_detached(self, argv) -> int: ...
    def is_alive(self, pid: int) -> bool: ...
    def terminate(self, pid: int, graceful: bool = True) -> None: ...


@dataclass(frozen=True)
class PlatformBundle:
    name: str
    hotkeys: HotkeyListener
    clipboard: Clipboard
    injector: TextInjector
    focus: FocusProbe
    autostart: AutostartManager
    permissions: PermissionsManager
    window_chrome: WindowChrome = field(default_factory=NullWindowChrome)
    hotkey_capturer: HotkeyCapturer = field(default_factory=NullHotkeyCapturer)
