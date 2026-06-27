"""Dotted-path settings access over the Config schema. Pure functions, no I/O, no OS calls.
The CLI (core/cli.py) and any other caller use these to get/set/reset/list individual settings."""
from typing import Any

from yohoho.core.config import (
    Config,
    SETTING_DESCRIPTIONS,
    _config_as_dict,
    _migrate,
    _validate,
    default_config,
)


class SettingError(ValueError):
    """Raised for an unknown/protected key or an un-coercible value (distinct from ConfigError)."""


# Protected leaves: not settable, hidden from `list`. Value = the reason shown on a set attempt.
PROTECTED_KEYS = {
    "version": "is internal and can't be changed",
    "macos.granted_python_path": "is managed by 'yohoho setup'",
}

# Leaves whose default is None, so their type can't be inferred from the default.
# Members must be int-or-None leaves — coerce() treats a non-blank value as int().
NULLABLE_INT_KEYS = {"audio.device_index"}


def _leaf_paths(d: dict, prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, val in d.items():
        dotted = f"{prefix}{key}"
        if isinstance(val, dict):
            paths.extend(_leaf_paths(val, f"{dotted}."))
        else:
            paths.append(dotted)
    return paths


def settable_keys() -> list[str]:
    """Every non-protected leaf dotted-path in the schema (order = schema order)."""
    return [k for k in _leaf_paths(_config_as_dict(default_config())) if k not in PROTECTED_KEYS]


def _get_at(d: dict, dotted: str) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise SettingError(f"unknown setting {dotted!r}. Run 'yohoho config list' to see valid keys.")
        cur = cur[part]
    return cur


def _set_at(d: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = value


def _default_value(key: str) -> Any:
    return _get_at(_config_as_dict(default_config()), key)


def get_value(cfg: Config, key: str) -> Any:
    # Protected keys are intentionally readable here (read-only transparency); only set/reset are gated.
    return _get_at(_config_as_dict(cfg), key)


def coerce(key: str, raw: str) -> Any:
    """Coerce the CLI string *raw* to the type of *key*'s default value."""
    if key in NULLABLE_INT_KEYS:
        if raw.strip().lower() in ("", "none", "null", "default"):
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise SettingError(f"{key!r} expects an integer or 'default' (got {raw!r}).") from exc

    default_val = _default_value(key)
    target = type(default_val)
    if target is bool:  # checked before int (bool subclasses int)
        low = raw.strip().lower()
        if low == "true":
            return True
        if low == "false":
            return False
        raise SettingError(f"{key!r} expects true or false (got {raw!r}).")
    if target is int:
        try:
            return int(raw)
        except ValueError as exc:
            raise SettingError(f"{key!r} expects an integer (got {raw!r}).") from exc
    if target is float:
        try:
            return float(raw)
        except ValueError as exc:
            raise SettingError(f"{key!r} expects a number (got {raw!r}).") from exc
    return raw  # str (or any other type) passes through unchanged


def _require_settable(key: str) -> None:
    if key in PROTECTED_KEYS:
        raise SettingError(f"{key!r} {PROTECTED_KEYS[key]}.")
    if key not in settable_keys():
        raise SettingError(f"unknown setting {key!r}. Run 'yohoho config list' to see valid keys.")


def set_value(cfg: Config, key: str, raw: str) -> Config:
    """Coerce + validate + return a new Config (caller persists). Raises SettingError/ConfigError."""
    _require_settable(key)
    coerced = coerce(key, raw)
    d = _config_as_dict(cfg)
    _set_at(d, key, coerced)
    _migrate(d)
    _validate(d)  # ConfigError on a semantic violation (range/enum/format)
    return Config(**d)


def reset_value(cfg: Config, key: str) -> Config:
    _require_settable(key)
    default_at = _default_value(key)
    d = _config_as_dict(cfg)
    _set_at(d, key, default_at)
    _migrate(d)
    _validate(d)
    return Config(**d)


def reset_all(cfg: Config) -> Config:
    """Reset every settable leaf to its default, preserving protected keys
    (e.g. macos.granted_python_path, which records macOS Accessibility/TCC trust)."""
    d = _config_as_dict(default_config())
    current = _config_as_dict(cfg)
    for pkey in PROTECTED_KEYS:
        _set_at(d, pkey, _get_at(current, pkey))
    _migrate(d)
    _validate(d)
    return Config(**d)


def format_value(v) -> str:
    """Human-readable rendering of a setting value (None -> default, bools lowercased)."""
    if v is None:
        return "(default)"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def list_settings(cfg: Config) -> list[tuple[str, Any, Any, str]]:
    """Return (key, current, default, description) for every settable key."""
    cur = _config_as_dict(cfg)
    return [(k, _get_at(cur, k), _default_value(k), SETTING_DESCRIPTIONS.get(k, "")) for k in settable_keys()]
