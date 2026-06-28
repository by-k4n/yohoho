from tests.helpers import _one_second_loud_16k, _silence_16k
from yohoho.core.cli import build_parser, run_dictate
from yohoho.core.platform_api import Permission, PermissionStatus


def test_parser_has_dictate():
    ns = build_parser().parse_args(
        ["dictate", "--seconds", "3", "--device", "0", "--save", "x.wav"]
    )
    assert ns.cmd == "dictate" and ns.seconds == 3 and ns.device == 0 and ns.save == "x.wav"


def test_parser_no_panel_flag():
    """--no-panel sets no_panel=True; default is False."""
    ns = build_parser().parse_args(["dictate", "--no-panel"])
    assert ns.no_panel is True

    ns_default = build_parser().parse_args(["dictate"])
    assert ns_default.no_panel is False


def test_run_dictate_prints_transcript(monkeypatch, capsys, tmp_path):
    from yohoho.core.engine import FakeEngine

    monkeypatch.setattr("yohoho.core.cli._make_engine", lambda dd: FakeEngine(result="it works"))
    monkeypatch.setattr(
        "yohoho.core.cli._capture_seconds",
        lambda dev, secs, on_amp: _one_second_loud_16k(),
    )
    run_dictate(seconds=1, device=None, data_dir=tmp_path, no_panel=True)
    out = capsys.readouterr()
    assert "it works" in out.out  # transcript on stdout
    assert "recording" in out.err.lower()  # progress feedback on stderr


def test_run_dictate_reports_no_speech_on_silence(monkeypatch, capsys, tmp_path):
    from yohoho.core.engine import FakeEngine

    monkeypatch.setattr("yohoho.core.cli._make_engine", lambda dd: FakeEngine(result=""))
    monkeypatch.setattr(
        "yohoho.core.cli._capture_seconds",
        lambda dev, secs, on_amp: _silence_16k(),
    )
    run_dictate(seconds=1, device=None, data_dir=tmp_path, no_panel=True)
    out = capsys.readouterr()
    assert out.out.strip() == ""  # nothing pasted to stdout
    assert "no speech detected" in out.err.lower()  # clear feedback instead of silence


def test_run_dictate_saves_clip(monkeypatch, tmp_path):
    import soundfile as sf

    from yohoho.core.engine import FakeEngine

    monkeypatch.setattr("yohoho.core.cli._make_engine", lambda dd: FakeEngine(result="x"))
    monkeypatch.setattr(
        "yohoho.core.cli._capture_seconds",
        lambda dev, secs, on_amp: _one_second_loud_16k(),
    )
    out = tmp_path / "clip.wav"
    run_dictate(seconds=1, device=None, data_dir=tmp_path, save=str(out), no_panel=True)
    assert out.exists()
    audio, sr = sf.read(out, dtype="float32")
    assert sr == 16000 and len(audio) > 0


# ---------------------------------------------------------------------------
# Task 11: config get/set + doctor tests
# ---------------------------------------------------------------------------


def test_parser_has_config_and_doctor():
    """build_parser must accept 'config' and 'doctor' subcommands."""
    ns = build_parser().parse_args(["config"])
    assert ns.cmd == "config"
    ns2 = build_parser().parse_args(["doctor"])
    assert ns2.cmd == "doctor"


def test_parser_config_accepts_key_value():
    """'config hotkey ctrl+f9' parses into key/value attrs."""
    ns = build_parser().parse_args(["config", "hotkey", "ctrl+f9"])
    assert ns.cmd == "config" and ns.config_key == "hotkey" and ns.config_value == "ctrl+f9"


def test_run_config_prints_config(capsys, tmp_path):
    """'yohoho config' (no key/value) prints the config."""
    from yohoho.core.cli import run_config
    import argparse
    args = argparse.Namespace(config_key=None, config_value=None)
    run_config(args, tmp_path)
    out = capsys.readouterr().out
    assert "hotkey" in out


def test_run_config_sets_valid_key(tmp_path):
    """'yohoho config hotkey ctrl+f9' writes and persists the value."""
    from yohoho.core.cli import run_config
    from yohoho.core.config import load_config
    import argparse
    args = argparse.Namespace(config_key="hotkey", config_value="ctrl+f9")
    run_config(args, tmp_path)
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.hotkey == "ctrl+f9"


def test_run_config_rejects_unknown_key(capsys, tmp_path):
    """'yohoho config nonexistent_key val' prints an error (unknown key)."""
    from yohoho.core.cli import run_config
    import argparse
    args = argparse.Namespace(config_key="nonexistent_key_xyz", config_value="val")
    run_config(args, tmp_path)
    out = capsys.readouterr()
    assert "unknown" in out.err.lower() or "error" in out.err.lower()


def test_run_doctor_prints_permission_rows(capsys, tmp_path):
    """'yohoho doctor' prints each permission row from a fake platform."""
    from yohoho.core.cli import run_doctor

    class FakePerms:
        def check(self):
            return PermissionStatus(
                ok=False,
                permissions=(
                    Permission(
                        key="input_monitoring",
                        state="denied",
                        label="Input Monitoring",
                        fix_hint="Enable under Input Monitoring.",
                        deep_link="x-apple.systempreferences:...",
                    ),
                    Permission(
                        key="accessibility",
                        state="granted",
                        label="Accessibility",
                        fix_hint="",
                    ),
                ),
                identity_ok=True,
            )

    class FakePlatform:
        permissions = FakePerms()

    run_doctor(tmp_path, platform=FakePlatform())
    out = capsys.readouterr().out
    assert "input_monitoring" in out.lower() or "input monitoring" in out.lower()
    assert "denied" in out.lower()
    assert "granted" in out.lower()
    assert "identity" in out.lower()


# ---------------------------------------------------------------------------
# Task 12: setup / start / stop parser + run_setup / run_stop tests
# ---------------------------------------------------------------------------


def test_parser_has_setup_start_stop():
    """build_parser must accept setup / start / stop subcommands."""
    for cmd in ("setup", "start", "stop"):
        ns = build_parser().parse_args([cmd])
        assert ns.cmd == cmd


def test_parser_setup_no_autostart_flag():
    """setup --no-autostart sets the flag."""
    ns = build_parser().parse_args(["setup", "--no-autostart"])
    assert ns.no_autostart is True
    ns2 = build_parser().parse_args(["setup"])
    assert ns2.no_autostart is False


class FakeHotkeys:
    """Minimal hotkeys stub for run_setup tests."""
    @staticmethod
    def is_valid_spec(s: str) -> bool:
        return bool(s)


def test_run_setup_records_python_path_and_enables_autostart(tmp_path):
    """run_setup with a fake platform (all granted) sets granted_python_path and enables autostart."""
    import sys
    import argparse
    from yohoho.core.cli import run_setup
    from yohoho.core.config import load_config
    from yohoho.core.engine import FakeEngine

    autostart_enabled = {"called": False}

    class FakeAutostart:
        def enable(self):
            autostart_enabled["called"] = True

        def disable(self):
            pass

        def is_enabled(self):
            return autostart_enabled["called"]

    class FakePerms:
        def check(self):
            return PermissionStatus(ok=True, permissions=(), identity_ok=True)

        def request(self):
            pass

        def guide(self):
            return "no action needed"

    class FakePlatform:
        permissions = FakePerms()
        autostart = FakeAutostart()
        hotkeys = FakeHotkeys()

    args = argparse.Namespace(no_autostart=False, hotkey=None)
    run_setup(
        tmp_path,
        platform=FakePlatform(),
        args=args,
        engine_factory=lambda dd: FakeEngine(result=""),
    )
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.macos["granted_python_path"] == sys.executable
    assert autostart_enabled["called"] is True


def test_run_setup_skips_autostart_when_flag_set(tmp_path):
    """run_setup --no-autostart must NOT call autostart.enable()."""
    import argparse
    from yohoho.core.cli import run_setup
    from yohoho.core.engine import FakeEngine

    autostart_enabled = {"called": False}

    class FakeAutostart:
        def enable(self):
            autostart_enabled["called"] = True

        def disable(self):
            pass

        def is_enabled(self):
            return False

    class FakePerms:
        def check(self):
            return PermissionStatus(ok=True, permissions=(), identity_ok=True)

        def request(self):
            pass

        def guide(self):
            return ""

    class FakePlatform:
        permissions = FakePerms()
        autostart = FakeAutostart()
        hotkeys = FakeHotkeys()

    args = argparse.Namespace(no_autostart=True, hotkey=None)
    run_setup(
        tmp_path,
        platform=FakePlatform(),
        args=args,
        engine_factory=lambda dd: FakeEngine(result=""),
    )
    assert autostart_enabled["called"] is False


def test_run_setup_is_idempotent(tmp_path):
    """Running run_setup twice does not raise and keeps granted_python_path correct."""
    import sys
    import argparse
    from yohoho.core.cli import run_setup
    from yohoho.core.config import load_config
    from yohoho.core.engine import FakeEngine

    class FakeAutostart:
        def enable(self): pass
        def disable(self): pass
        def is_enabled(self): return True

    class FakePerms:
        def check(self): return PermissionStatus(ok=True, permissions=(), identity_ok=True)
        def request(self): pass
        def guide(self): return ""

    class FakePlatform:
        permissions = FakePerms()
        autostart = FakeAutostart()
        hotkeys = FakeHotkeys()

    args = argparse.Namespace(no_autostart=False, hotkey=None)
    for _ in range(2):
        run_setup(
            tmp_path,
            platform=FakePlatform(),
            args=args,
            engine_factory=lambda dd: FakeEngine(result=""),
        )
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.macos["granted_python_path"] == sys.executable


def test_run_setup_rejects_invalid_hotkey(capsys, tmp_path):
    """run_setup with an empty/invalid hotkey prints an error and returns without enabling autostart."""
    import argparse
    from yohoho.core.cli import run_setup
    from yohoho.core.engine import FakeEngine

    autostart_enabled = {"called": False}

    class FakeAutostart:
        def enable(self): autostart_enabled["called"] = True
        def disable(self): pass
        def is_enabled(self): return False

    class FakePerms:
        def check(self): return PermissionStatus(ok=True, permissions=(), identity_ok=True)
        def request(self): pass
        def guide(self): return ""

    class FakeHotkeysInvalid:
        @staticmethod
        def is_valid_spec(s: str) -> bool:
            return False  # reject everything

    class FakePlatform:
        permissions = FakePerms()
        autostart = FakeAutostart()
        hotkeys = FakeHotkeysInvalid()

    args = argparse.Namespace(no_autostart=False, hotkey="bad-hotkey")
    run_setup(
        tmp_path,
        platform=FakePlatform(),
        args=args,
        engine_factory=lambda dd: FakeEngine(result=""),
    )
    out = capsys.readouterr()
    assert "invalid hotkey" in out.err.lower()
    assert autostart_enabled["called"] is False


def test_run_stop_when_not_running(tmp_path, capsys):
    """run_stop prints 'not running' when no daemon is running (no autostart touched)."""
    from yohoho.core.cli import run_stop

    rc = run_stop(tmp_path)
    assert rc == 0
    assert "not running" in capsys.readouterr().out
