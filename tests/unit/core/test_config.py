"""Unit tests for YOHOHO_DATA_DIR env override in config._resolve_data_dir()."""
import sys

from yohoho.core.config import _resolve_data_dir, data_dir


def test_yohoho_data_dir_env_override(monkeypatch, tmp_path):
    """YOHOHO_DATA_DIR env var redirects _resolve_data_dir to the given path."""
    monkeypatch.setenv("YOHOHO_DATA_DIR", str(tmp_path))
    assert _resolve_data_dir() == tmp_path


def test_yohoho_data_dir_override_propagates_to_data_dir(monkeypatch, tmp_path):
    """data_dir() creates and returns the override path when YOHOHO_DATA_DIR is set."""
    target = tmp_path / "isolated"
    monkeypatch.setenv("YOHOHO_DATA_DIR", str(target))
    result = data_dir()
    assert result == target
    assert target.is_dir()


def test_no_override_falls_back_to_platform_default(monkeypatch, tmp_path):
    """Without YOHOHO_DATA_DIR the result is the platform-specific default (not tmp_path)."""
    monkeypatch.delenv("YOHOHO_DATA_DIR", raising=False)
    result = _resolve_data_dir()
    assert result != tmp_path
    if sys.platform == "darwin":
        assert "Application Support" in str(result) and result.name == "yohoho"
    elif sys.platform == "win32":
        assert result.name == "yohoho"
    else:
        assert result.name == "yohoho"
