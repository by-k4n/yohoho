import pytest
from yohoho.core.config import CloudSyncWarning, data_dir, default_config, load_config, save_config


def test_defaults_match_spec():
    c = default_config()
    assert c.hotkey == "ctrl+alt+space"
    assert c.clipboard["restore_previous"] is False
    assert c.clipboard["restore_delay_ms"] == 150
    assert c.history["enabled"] is True
    assert c.recording_mode == "press_to_toggle"


def test_ui_show_panel_default():
    c = default_config()
    assert c.ui["show_panel"] is True


def test_ui_show_panel_round_trips(tmp_path):
    cfg = default_config()
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.ui["show_panel"] is True


def test_load_missing_file_returns_defaults(tmp_path):
    c = load_config(tmp_path / "config.yaml")
    assert c == default_config()


def test_load_merges_partial_and_keeps_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hotkey: f14\nlog_level: debug\n")
    c = load_config(p)
    assert c.hotkey == "f14" and c.log_level == "debug"
    assert c.history["enabled"] is True  # default preserved


def test_sounds_defaults_and_bad_volume_rejected(tmp_path):
    from yohoho.core.config import ConfigError
    assert default_config().sounds == {"enabled": True, "volume": 0.5}
    # A hand-edited out-of-range / non-numeric volume must fail validation cleanly,
    # not crash `yohoho start` with a raw traceback.
    p = tmp_path / "config.yaml"
    p.write_text("sounds:\n  volume: 50\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("sounds:\n  volume: loud\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_data_dir_warns_on_cloud_path(monkeypatch, tmp_path):
    fake = tmp_path / "OneDrive" / "yohoho"
    monkeypatch.setattr("yohoho.core.config._resolve_data_dir", lambda: fake)
    with pytest.warns(CloudSyncWarning):
        data_dir()


def test_macos_granted_path_default_and_round_trip(tmp_path):
    from yohoho.core.config import default_config, save_config, load_config
    assert default_config().macos["granted_python_path"] == ""
    p = tmp_path / "c.yaml"
    cfg = default_config()
    object.__setattr__(cfg, "macos", {"granted_python_path": "/x/python"})  # frozen dataclass
    save_config(cfg, p)
    assert load_config(p).macos["granted_python_path"] == "/x/python"
