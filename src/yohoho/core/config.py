"""Config schema, defaults, data-dir resolution, and load/save helpers for yohoho."""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Warning / exception types
# ---------------------------------------------------------------------------


class CloudSyncWarning(UserWarning):
    """Raised when the resolved data directory lives inside a cloud-sync folder."""


class ConfigError(Exception):
    """Raised when a config value fails validation."""


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

_CLOUD_SYNC_SEGMENTS = {"onedrive", "dropbox", "google drive", "icloud"}


def _default_clipboard() -> dict:
    return {"restore_previous": False, "restore_delay_ms": 150}


def _default_history() -> dict:
    return {"enabled": True, "capture_app_id": False, "max_entries": 1000, "max_age_days": 30}


def _default_audio() -> dict:
    return {"device_index": None}


def _default_ui() -> dict:
    return {"show_panel": True}


def _default_macos() -> dict:
    return {"granted_python_path": ""}


def _default_sounds() -> dict:
    return {"enabled": True, "volume": 0.5}


@dataclass(frozen=True)
class Config:
    version: int = 1
    model: str = "nemo-parakeet-tdt-0.6b-v2"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "en"
    hotkey: str = "ctrl+alt+space"
    cancel_channel: str = "esc"
    recording_mode: str = "press_to_toggle"
    input_method: str = "clipboard"
    clipboard: dict = field(default_factory=_default_clipboard)
    history: dict = field(default_factory=_default_history)
    audio: dict = field(default_factory=_default_audio)
    ui: dict = field(default_factory=_default_ui)
    macos: dict = field(default_factory=_default_macos)
    sounds: dict = field(default_factory=_default_sounds)
    log_level: str = "info"


# ---------------------------------------------------------------------------
# Defaults helper
# ---------------------------------------------------------------------------


def default_config() -> Config:
    """Return a fresh Config with all default values."""
    return Config()


# Human-readable one-line descriptions for `yohoho config list`. Co-located with the schema:
# adding a setting means adding its field/default AND a line here (enforced by a coverage test).
SETTING_DESCRIPTIONS = {
    "hotkey": "Activation chord (e.g. ctrl+alt+space)",
    "model": "Speech-to-text model name",
    "device": "Compute device (cpu)",
    "compute_type": "Model quantization (int8)",
    "language": "Input language code",
    "cancel_channel": "Key that cancels an in-progress dictation",
    "recording_mode": "press_to_toggle or voice_activity_detection",
    "input_method": "How transcribed text is delivered",
    "log_level": "Logging verbosity (debug/info/…)",
    "clipboard.restore_previous": "Restore the clipboard after pasting",
    "clipboard.restore_delay_ms": "Delay before restoring the clipboard (ms)",
    "history.enabled": "Keep a local transcript history",
    "history.capture_app_id": "Record the active app in history",
    "history.max_entries": "Max history entries to keep",
    "history.max_age_days": "Days to retain history",
    "audio.device_index": "Microphone device index (blank/default = system default)",
    "ui.show_panel": "Show the dot-matrix status panel during dictation",
    "sounds.enabled": "Play the on/off chimes",
    "sounds.volume": "Chime volume (0.0–1.0)",
}


# ---------------------------------------------------------------------------
# Data-dir resolution
# ---------------------------------------------------------------------------


def _resolve_data_dir() -> Path:
    """Return the platform-appropriate local data directory for yohoho."""
    override = os.environ.get("YOHOHO_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "yohoho"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "yohoho"
    # Linux / BSD / other
    xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(xdg) / "yohoho"


def data_dir() -> Path:
    """Resolve and return the yohoho data directory.

    Emits a :class:`CloudSyncWarning` if the path passes through a known
    cloud-sync folder (OneDrive, Dropbox, Google Drive, iCloud).
    Creates the directory if it does not exist.
    """
    path = _resolve_data_dir()
    parts_lower = {p.lower() for p in path.parts}
    if parts_lower & _CLOUD_SYNC_SEGMENTS:
        warnings.warn(
            f"yohoho data directory '{path}' appears to be inside a cloud-sync folder. "
            "This can cause data corruption and is not recommended. "
            "Set XDG_DATA_HOME (Linux) or move the folder outside the synced area.",
            CloudSyncWarning,
            stacklevel=2,
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Deep-merge helper
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Migration hook
# ---------------------------------------------------------------------------


def _migrate(d: dict) -> dict:
    """M1 pass-through migration — add a default version key if absent."""
    d.setdefault("version", 1)
    return d


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_RECORDING_MODES = {"press_to_toggle", "voice_activity_detection"}
# Canonical option lists for the interactive editor's enum pickers. These are intentionally NOT
# enforced by _validate — log_level / input_method stay permissive (typos via `config set` are an
# accepted, pre-existing behavior); these constants only remove duplicated pick-lists / drift risk.
_VALID_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")
_VALID_INPUT_METHODS = ("clipboard",)


def _validate(d: dict) -> None:
    """Raise :class:`ConfigError` if *d* contains invalid values."""
    if not d.get("hotkey", ""):
        raise ConfigError("'hotkey' must not be empty.")
    if d.get("recording_mode") not in _VALID_RECORDING_MODES:
        raise ConfigError(
            f"'recording_mode' must be one of {sorted(_VALID_RECORDING_MODES)}, "
            f"got: {d.get('recording_mode')!r}"
        )
    clipboard = d.get("clipboard", {})
    if clipboard.get("restore_delay_ms", 0) < 0:
        raise ConfigError("'clipboard.restore_delay_ms' must be >= 0.")
    history = d.get("history", {})
    if history.get("max_entries", 1) <= 0:
        raise ConfigError("'history.max_entries' must be > 0.")
    if history.get("max_age_days", 0) < 0:
        raise ConfigError("'history.max_age_days' must be >= 0.")
    audio = d.get("audio", {})
    dev = audio.get("device_index", None)
    if dev is not None and (not isinstance(dev, int) or isinstance(dev, bool) or dev < 0):
        raise ConfigError("'audio.device_index' must be a non-negative integer or null.")
    sounds = d.get("sounds", {})
    if not isinstance(sounds.get("enabled", True), bool):
        raise ConfigError("'sounds.enabled' must be true or false.")
    volume = sounds.get("volume", 0.5)
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0.0 <= volume <= 1.0:
        raise ConfigError("'sounds.volume' must be a number between 0.0 and 1.0.")


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _config_as_dict(cfg: Config) -> dict:
    """Convert a Config to a plain dict suitable for YAML serialisation."""
    return {
        "version": cfg.version,
        "model": cfg.model,
        "device": cfg.device,
        "compute_type": cfg.compute_type,
        "language": cfg.language,
        "hotkey": cfg.hotkey,
        "cancel_channel": cfg.cancel_channel,
        "recording_mode": cfg.recording_mode,
        "input_method": cfg.input_method,
        "clipboard": dict(cfg.clipboard),
        "history": dict(cfg.history),
        "audio": dict(cfg.audio),
        "ui": dict(cfg.ui),
        "macos": dict(cfg.macos),
        "sounds": dict(cfg.sounds),
        "log_level": cfg.log_level,
    }


def load_config(path) -> Config:
    """Load config from *path*, merging over defaults.

    If the file does not exist the defaults are returned unchanged.
    """
    path = Path(path)
    if not path.exists():
        return default_config()

    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}

    defaults = _config_as_dict(default_config())
    merged = _deep_merge(defaults, loaded)
    _migrate(merged)
    _validate(merged)
    return Config(**merged)


def save_config(cfg: Config, path) -> None:
    """Serialise *cfg* to YAML and write atomically to *path*.

    The file is written to a ``.tmp`` sibling first, then renamed, and
    finally ``chmod``-ed to 0o600 so only the owner can read it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(_config_as_dict(cfg), fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, path)
    path.chmod(0o600)
