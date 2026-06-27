"""Interactive settings menu for `yohoho config` (TTY only). Drives the config_access engine;
rendered through an injected terminal (Terminal in production, a fake in tests)."""
from __future__ import annotations

from pathlib import Path

from yohoho.core import config_access as ca
from yohoho.core.config import (
    ConfigError,
    _VALID_INPUT_METHODS,
    _VALID_LOG_LEVELS,
    _VALID_RECORDING_MODES,
    load_config,
    save_config,
)

# Enum pick-lists, built from the canonical constants in config.py (order = display order;
# current value is pre-selected). Single source of truth — no duplicated literals here.
_ENUM_OPTIONS = {
    "recording_mode": sorted(_VALID_RECORDING_MODES),
    "log_level": list(_VALID_LOG_LEVELS),
    "input_method": list(_VALID_INPUT_METHODS),
}
# Keys driven by the hold-to-record overlay (Task 8).
_HOTKEY_KEYS = {"hotkey", "cancel_channel"}
# Settings tucked below the "── advanced ──" divider (spec §4.2).
_ADVANCED_KEYS = {"model", "device", "compute_type", "language", "input_method", "log_level"}


def run_menu(data_dir) -> None:
    """Launch the interactive settings menu against the real terminal.

    Wires the raw-mode ``Terminal`` (entered/exited as a context manager so the
    tty state is always restored) around a ``ConfigMenu`` driven by the platform's
    real hotkey capturer. Called from ``cli.run_config`` on an interactive TTY.
    """
    from yohoho.core.platform_factory import get_platform
    from yohoho.core.ui.term import Terminal

    cfg_path = Path(data_dir) / "config.yaml"
    capturer = get_platform().hotkey_capturer
    with Terminal() as term:
        try:
            ConfigMenu(term, cfg_path, capturer=capturer).run()
        except KeyboardInterrupt:
            pass                                 # Ctrl-C: exit cleanly (Terminal restores the tty)


class ConfigMenu:
    def __init__(self, term, cfg_path: Path, capturer=None):
        self._term = term
        self._cfg_path = Path(cfg_path)
        self._capturer = capturer
        self._error: str | None = None
        self._cfg = load_config(self._cfg_path)
        self._rows = self._ordered(ca.list_settings(self._cfg))   # [(key, current, default, desc)]
        self.index = 0

    # ----- rendering -------------------------------------------------------

    @staticmethod
    def _ordered(rows):
        """Everyday settings first (schema order), then the advanced block."""
        everyday = [r for r in rows if r[0] not in _ADVANCED_KEYS]
        advanced = [r for r in rows if r[0] in _ADVANCED_KEYS]
        return everyday + advanced

    def _frame(self) -> list[str]:
        lines = ["yohoho — settings    ↑↓ move · ↵ edit · r reset · R reset-all · q quit", ""]
        advanced_shown = False
        for i, (key, current, _default, _desc) in enumerate(self._rows):
            if key in _ADVANCED_KEYS and not advanced_shown:
                lines.append("  ── advanced ──")
                advanced_shown = True
            cursor = "›" if i == self.index else " "
            lines.append(f"{cursor} {key:<26} {ca.format_value(current)}")
        if self._error:
            lines += ["", self._error]
        lines += ["", "changes apply on next 'yohoho start'"]
        return lines

    # ----- main loop -------------------------------------------------------

    def run(self) -> None:
        while True:
            self._term.render(self._frame())
            key = self._term.read_key()
            if key in ("q", "esc", "ctrl-c"):
                return
            if key in ("down", "j"):
                self.index = min(self.index + 1, len(self._rows) - 1)
            elif key in ("up", "k"):
                self.index = max(self.index - 1, 0)
            elif key == "enter":
                self._edit_current()
            elif key == "r":
                self._reset_current()
            elif key == "R":
                self._reset_all()

    def _reload(self) -> None:
        self._cfg = load_config(self._cfg_path)
        self._rows = self._ordered(ca.list_settings(self._cfg))

    # ----- editor dispatch -------------------------------------------------

    def _edit_current(self) -> None:
        self._error = None                       # a new action clears any stale error banner
        if not self._rows:
            return
        key, current, default, _desc = self._rows[self.index]
        if key in _HOTKEY_KEYS:
            self._edit_hotkey(key)
        elif key in _ENUM_OPTIONS:
            self._edit_enum(key, current, _ENUM_OPTIONS[key])
        elif isinstance(default, bool):
            self._edit_bool(key, current)
        else:                                   # int / float / None / str -> typed text
            self._edit_text(key, current)

    def _edit_bool(self, key: str, current) -> None:
        self._apply(key, "false" if current else "true")

    def _edit_enum(self, key: str, current, options: list[str]) -> None:
        try:
            sel = options.index(current)
        except ValueError:
            sel = 0
        while True:
            lines = [f"Edit {key}", ""]
            for i, opt in enumerate(options):
                lines.append(f"{'›' if i == sel else ' '} {opt}")
            lines += ["", "↑↓ choose · ↵ select · esc cancel"]
            self._term.render(lines)
            k = self._term.read_key()
            if k in ("down", "j"):
                sel = min(sel + 1, len(options) - 1)
            elif k in ("up", "k"):
                sel = max(sel - 1, 0)
            elif k == "enter":
                self._apply(key, options[sel])
                return
            elif k in ("esc", "ctrl-c"):
                return

    def _edit_text(self, key: str, current) -> None:
        buf = ""
        hint = self._device_hint() if key == "audio.device_index" else []
        while True:
            lines = [f"Edit {key}", ""]
            lines += hint
            if hint:
                lines.append("")
            lines.append(f"> {buf}")
            if self._error:
                lines += ["", self._error]
            self._term.render(lines)
            k = self._term.read_key()
            if k == "enter":
                self._apply(key, buf)
                if self._error is None:
                    return
            elif k in ("esc", "ctrl-c"):
                return
            elif k == "backspace":
                buf = buf[:-1]
            elif key == "sounds.volume" and k in ("left", "right"):
                try:
                    base = float(buf)
                except (ValueError, TypeError):
                    base = ca.get_value(self._cfg, key)
                if not isinstance(base, (int, float)):
                    base = 0.5
                delta = -0.05 if k == "left" else 0.05
                value = max(0.0, min(1.0, round(base + delta, 2)))
                buf = f"{value}"                  # reflect the nudge in the typed buffer
                self._apply(key, buf)
            elif len(k) == 1 and k.isprintable():
                buf += k

    # ----- hotkey overlay (Task 8) ----------------------------------------

    def _edit_hotkey(self, key: str) -> None:
        self._term.render(["Press & hold your chord for 3s…", ""])
        spec = None
        if self._capturer:
            try:
                spec = self._capturer.capture(3.0, on_progress=self._render_hold)
            except Exception:
                spec = None                      # backend/init failure -> typed fallback
        # Drain any keystrokes the OS queued on the tty during capture (the global listener and
        # the tty saw the same physical keys); otherwise they leak into the next read_key().
        getattr(self._term, "drain_input", lambda: None)()
        if spec is None:
            # typed fallback (capture unavailable / no permission)
            typed = self._read_line("Type the chord (e.g. ctrl+alt+space): ")
            if not typed:
                return
            spec = typed
        self._apply(key, spec)

    def _render_hold(self, frac: float) -> None:
        filled = int(max(0.0, min(1.0, frac)) * 12)
        bar = "⠿" * filled + "⠂" * (12 - filled)
        self._term.render(["Press & hold your chord for 3s…", "", bar])

    def _read_line(self, prompt: str) -> str:
        buf = ""
        while True:
            self._term.render([prompt, "", f"> {buf}"])
            k = self._term.read_key()
            if k == "enter":
                return buf
            if k in ("esc", "ctrl-c"):
                return ""
            if k == "backspace":
                buf = buf[:-1]
            elif len(k) == 1 and k.isprintable():
                buf += k

    # ----- apply / reset ---------------------------------------------------

    def _apply(self, key: str, raw: str) -> None:
        try:
            new = ca.set_value(self._cfg, key, raw)
            save_config(new, self._cfg_path)
            self._reload()
            self._error = None
        except (ca.SettingError, ConfigError) as e:
            self._error = str(e)

    def _reset_current(self) -> None:
        self._error = None                       # a new action clears any stale error banner
        if not self._rows:
            return
        key = self._rows[self.index][0]
        save_config(ca.reset_value(self._cfg, key), self._cfg_path)
        self._reload()

    def _reset_all(self) -> None:
        self._error = None                       # a new action clears any stale error banner
        self._term.render(["Reset ALL settings to defaults?  (y/n)", ""])
        if self._term.read_key() in ("y", "Y"):
            save_config(ca.reset_all(self._cfg), self._cfg_path)
            self._reload()

    # ----- helpers ---------------------------------------------------------

    @staticmethod
    def _device_hint() -> list[str]:
        """Best-effort list of input devices; never crashes the menu if audio is unavailable."""
        try:
            import sounddevice as sd

            lines = ["input devices:"]
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    lines.append(f"  {i}: {dev.get('name', '?')}")
            lines.append("(blank = system default)")
            return lines
        except Exception:
            return ["(device list unavailable; blank = system default)"]
