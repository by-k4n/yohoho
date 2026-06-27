import dataclasses

import pytest  # used by the rejection tests added in later tasks
from yohoho.core import config_access as ca
from yohoho.core.config import ConfigError  # used by the Task 5/6 range tests


def test_settable_keys_includes_nested_and_top_level():
    keys = set(ca.settable_keys())
    assert "hotkey" in keys
    assert "sounds.volume" in keys and "ui.show_panel" in keys and "audio.device_index" in keys
    assert "history.max_entries" in keys


def test_settable_keys_excludes_protected():
    keys = set(ca.settable_keys())
    assert "version" not in keys
    assert "macos.granted_python_path" not in keys


def test_get_value_nested_and_top_level():
    cfg = ca.default_config()
    assert ca.get_value(cfg, "sounds.volume") == 0.5
    assert ca.get_value(cfg, "hotkey") == "ctrl+alt+space"


def test_get_value_unknown_key_raises():
    with pytest.raises(ca.SettingError):
        ca.get_value(ca.default_config(), "sounds.bogus")


def test_get_value_allows_protected_keys():
    # Protected keys are intentionally readable (set/reset are gated, get is not).
    assert ca.get_value(ca.default_config(), "version") == 1
    assert ca.get_value(ca.default_config(), "macos.granted_python_path") == ""


def test_coerce_by_default_type():
    assert ca.coerce("sounds.volume", "0.3") == 0.3                # float
    assert ca.coerce("ui.show_panel", "false") is False           # bool
    assert ca.coerce("history.max_entries", "50") == 50           # int
    assert ca.coerce("hotkey", "cmd+shift+d") == "cmd+shift+d"    # str


def test_coerce_nullable_int():
    assert ca.coerce("audio.device_index", "2") == 2
    for blank in ("default", "none", "null", ""):
        assert ca.coerce("audio.device_index", blank) is None


def test_coerce_rejects_bad_values():
    for key, raw in [("sounds.volume", "loud"), ("ui.show_panel", "maybe"),
                     ("history.max_entries", "1.5"), ("audio.device_index", "x")]:
        with pytest.raises(ca.SettingError):
            ca.coerce(key, raw)


def test_every_nullable_default_is_registered():
    # Guards coerce(): any leaf whose default is None MUST be in NULLABLE_INT_KEYS,
    # else coerce would silently treat it as a string.
    flat = ca._config_as_dict(ca.default_config())
    none_leaves = {k for k in ca.settable_keys() if ca._get_at(flat, k) is None}
    assert none_leaves <= ca.NULLABLE_INT_KEYS


def test_set_value_roundtrips():
    new = ca.set_value(ca.default_config(), "sounds.volume", "0.3")
    assert ca.get_value(new, "sounds.volume") == 0.3
    assert ca.get_value(ca.set_value(ca.default_config(), "hotkey", "cmd+shift+d"), "hotkey") == "cmd+shift+d"


def test_set_value_rejects_unknown_and_protected():
    with pytest.raises(ca.SettingError):
        ca.set_value(ca.default_config(), "sounds.bogus", "1")
    with pytest.raises(ca.SettingError):
        ca.set_value(ca.default_config(), "version", "2")
    with pytest.raises(ca.SettingError):
        ca.set_value(ca.default_config(), "macos.granted_python_path", "/x")


def test_set_value_out_of_range_raises():
    with pytest.raises(ConfigError):  # value coerces fine, then _validate rejects (bound 0..1)
        ca.set_value(ca.default_config(), "sounds.volume", "5")


def test_reset_value_and_reset_all():
    changed = ca.set_value(ca.default_config(), "sounds.volume", "0.3")
    back = ca.reset_value(changed, "sounds.volume")
    assert ca.get_value(back, "sounds.volume") == 0.5
    assert ca.reset_all(ca.default_config()) == ca.default_config()


def test_reset_all_preserves_protected_keys():
    cfg = dataclasses.replace(ca.default_config(), macos={"granted_python_path": "/custom/py"})
    cfg = ca.set_value(cfg, "sounds.volume", "0.3")          # change a settable leaf too
    out = ca.reset_all(cfg)
    assert ca.get_value(out, "macos.granted_python_path") == "/custom/py"  # protected preserved
    assert ca.get_value(out, "sounds.volume") == 0.5                       # settable reset


def test_reserved_words_not_settable():
    assert not ({"list", "reset", "all"} & set(ca.settable_keys()))


def test_negative_device_index_rejected():
    with pytest.raises(ConfigError):  # coerces to int, then _validate rejects negative
        ca.set_value(ca.default_config(), "audio.device_index", "-1")


def test_negative_history_age_rejected():
    with pytest.raises(ConfigError):  # coerces to int, then _validate rejects negative
        ca.set_value(ca.default_config(), "history.max_age_days", "-5")


def test_list_settings_rows():
    rows = {r[0]: r for r in ca.list_settings(ca.default_config())}
    assert "sounds.volume" in rows
    key, current, default, desc = rows["sounds.volume"]
    assert current == 0.5 and default == 0.5 and "volume" in desc.lower()
    assert "version" not in rows and "macos.granted_python_path" not in rows  # protected hidden


def test_every_settable_key_has_a_description():
    from yohoho.core.config import SETTING_DESCRIPTIONS
    missing = [k for k in ca.settable_keys() if k not in SETTING_DESCRIPTIONS]
    assert missing == [], f"settings missing a description: {missing}"
