"""macOS WindowChrome: wraps the existing macos_window primitives behind the WindowChrome seam.
Only ever constructed on macOS (via make_macos_platform); module-level import of macos_window is
safe because its AppKit import is lazy (inside set_accessory_policy)."""
from yohoho.platform import macos_window


class MacWindowChrome:
    def __init__(
        self,
        *,
        apply_chrome_fn=macos_window.apply_chrome,
        enable_round_fn=macos_window.enable_round,
        set_policy_fn=macos_window.set_accessory_policy,
    ) -> None:
        self._apply_chrome = apply_chrome_fn
        self._enable_round = enable_round_fn
        self._set_policy = set_policy_fn

    def set_app_policy(self) -> None:
        self._set_policy()

    def style_window(self, root, toplevel, canvas) -> None:
        # Order is load-bearing: enable_round must follow apply_chrome (Tk -transparent vs
        # overrideredirect interaction — see panel.py history).
        self._apply_chrome(root, toplevel)
        self._enable_round(toplevel, canvas)
