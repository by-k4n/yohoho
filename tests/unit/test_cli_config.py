import types
from yohoho.core.cli import run_config
from yohoho.core.config import load_config


def _args(key=None, value=None, yes=False):
    return types.SimpleNamespace(config_key=key, config_value=value, yes=yes)


def test_set_nested_persists(tmp_path, capsys):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.3
    assert "0.3" in capsys.readouterr().out


def test_get_nested_prints_value(tmp_path, capsys):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    capsys.readouterr()
    run_config(_args("sounds.volume"), tmp_path)
    assert capsys.readouterr().out.strip() == "0.3"


def test_unknown_key_errors(tmp_path, capsys):
    run_config(_args("sounds.bogus", "1"), tmp_path)
    assert "unknown setting" in capsys.readouterr().err


def test_list_includes_description(tmp_path, capsys):
    run_config(_args("list"), tmp_path)
    out = capsys.readouterr().out
    assert "sounds.volume" in out and "volume" in out.lower()


def test_reset_key_restores_default(tmp_path, capsys):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    run_config(_args("reset", "sounds.volume"), tmp_path)
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.5


def test_reset_all_with_yes(tmp_path, capsys):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    capsys.readouterr()
    run_config(_args("reset", "all", yes=True), tmp_path)
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.5
    assert "all settings reset to defaults." in capsys.readouterr().out


def test_reset_all_aborts_on_eof(tmp_path, capsys, monkeypatch):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(EOFError()))
    run_config(_args("reset", "all"), tmp_path)
    assert "aborted." in capsys.readouterr().out
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.3


def test_reset_all_aborts_on_no(tmp_path, capsys, monkeypatch):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    run_config(_args("reset", "all"), tmp_path)
    assert "aborted." in capsys.readouterr().out
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.3


def test_reset_all_interactive_accept(tmp_path, capsys, monkeypatch):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    run_config(_args("reset", "all"), tmp_path)
    assert "all settings reset to defaults." in capsys.readouterr().out
    assert load_config(tmp_path / "config.yaml").sounds["volume"] == 0.5


def test_reset_all_preserves_granted_python_path(tmp_path):
    import dataclasses
    from yohoho.core.config import save_config, default_config
    save_config(
        dataclasses.replace(
            default_config(),
            macos={"granted_python_path": "/custom/py"},
            sounds={"enabled": True, "volume": 0.3},
        ),
        tmp_path / "config.yaml",
    )
    run_config(_args("reset", "all", yes=True), tmp_path)
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.macos["granted_python_path"] == "/custom/py"   # preserved
    assert cfg.sounds["volume"] == 0.5                        # reset


def test_set_prints_saved_transition(tmp_path, capsys):
    run_config(_args("sounds.volume", "0.3"), tmp_path)
    out = capsys.readouterr().out
    assert "sounds.volume" in out and "(saved)" in out


def test_bare_reset_errors(tmp_path, capsys):
    run_config(_args("reset"), tmp_path)
    assert "specify a key" in capsys.readouterr().err


def test_top_level_set_still_works(tmp_path):  # backward compat
    run_config(_args("hotkey", "cmd+shift+d"), tmp_path)
    assert load_config(tmp_path / "config.yaml").hotkey == "cmd+shift+d"


def test_bare_config_non_tty_prints_yaml(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    run_config(_args(None), tmp_path)
    assert "hotkey:" in capsys.readouterr().out            # YAML path unchanged


def test_bare_config_tty_launches_menu(tmp_path, monkeypatch):
    launched = {}
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr("yohoho.core.config_tui.run_menu",
                        lambda data_dir: launched.setdefault("ok", True))
    run_config(_args(None), tmp_path)
    assert launched.get("ok")
