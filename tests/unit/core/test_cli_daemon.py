"""T8 tests: real start/stop daemon control + hidden _run-daemon + marker un-scoping."""
from __future__ import annotations

import yohoho.core.cli as cli
from yohoho.core.cli import main, run_start, run_stop
from yohoho.core.daemon import PidFile
from yohoho.core.null_platform import NullProcessController


# ---------------------------------------------------------------------------
# run_start tests
# ---------------------------------------------------------------------------


def test_start_detaches_when_tty(monkeypatch, tmp_path, capsys):
    fake = NullProcessController()
    monkeypatch.setattr(cli, "get_process_controller", lambda: fake)
    monkeypatch.setattr(cli, "_has_tty", lambda: True)

    assert run_start(tmp_path) == 0

    assert fake.spawned == [["yohoho", "_run-daemon"]]
    out = capsys.readouterr().out
    assert "started" in out


def test_start_foreground_when_no_tty(monkeypatch, tmp_path):
    called = {}

    def fake_run_daemon(data_dir):
        called["fg"] = data_dir
        return 0

    fake = NullProcessController()
    monkeypatch.setattr(cli, "_has_tty", lambda: False)
    monkeypatch.setattr(cli, "run_daemon", fake_run_daemon)
    monkeypatch.setattr(cli, "get_process_controller", lambda: fake)

    assert run_start(tmp_path) == 0

    assert called["fg"] == tmp_path
    assert fake.spawned == []


def test_start_foreground_propagates_daemon_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_has_tty", lambda: False)
    monkeypatch.setattr(cli, "run_daemon", lambda dd: 1)

    assert run_start(tmp_path) == 1


def test_start_refuses_second_instance(monkeypatch, tmp_path, capsys):
    # Acquire the pidfile so is_running() → True
    PidFile(tmp_path).acquire()

    fake = NullProcessController()
    monkeypatch.setattr(cli, "_has_tty", lambda: True)
    monkeypatch.setattr(cli, "get_process_controller", lambda: fake)

    run_start(tmp_path)

    out = capsys.readouterr().out
    assert "already running" in out
    assert fake.spawned == []


# ---------------------------------------------------------------------------
# run_stop tests
# ---------------------------------------------------------------------------


def test_stop_when_not_running(tmp_path, capsys):
    rc = run_stop(tmp_path)
    out = capsys.readouterr().out
    assert "not running" in out
    assert rc == 0


def test_stop_pid_none_does_not_terminate(monkeypatch, tmp_path, capsys):
    """If read_pid() returns None (pidfile gone), treat as not running — never
    call terminate(None)."""

    class FakePidFile:
        def __init__(self, *args, **kwargs):
            pass

        def read_pid(self):
            return None

        def is_running(self):
            return False

    fake_ctrl = NullProcessController()
    monkeypatch.setattr(cli, "PidFile", FakePidFile)
    monkeypatch.setattr(cli, "get_process_controller", lambda: fake_ctrl)

    rc = run_stop(tmp_path)

    assert rc == 0
    assert "not running" in capsys.readouterr().out
    assert fake_ctrl.terminated == []


def test_stop_graceful(monkeypatch, tmp_path, capsys):
    calls = []

    class FakePidFile:
        def __init__(self, *args, **kwargs):
            pass

        def is_running(self):
            # First call returns True (running), subsequent calls False (exited)
            calls.append(1)
            return len(calls) == 1

        def read_pid(self):
            return 9999

    fake_ctrl = NullProcessController()
    monkeypatch.setattr(cli, "PidFile", FakePidFile)
    monkeypatch.setattr(cli, "get_process_controller", lambda: fake_ctrl)

    rc = run_stop(tmp_path)

    # Sentinel written
    assert (tmp_path / "stop").exists()
    # NOT force-killed
    assert fake_ctrl.terminated == []
    out = capsys.readouterr().out
    assert "stopped" in out
    assert rc == 0


def test_stop_force(monkeypatch, tmp_path, capsys):
    class FakePidFile:
        def __init__(self, *args, **kwargs):
            pass

        def is_running(self):
            return True  # never exits gracefully

        def read_pid(self):
            return 4242

    fake_ctrl = NullProcessController()
    monkeypatch.setattr(cli, "PidFile", FakePidFile)
    monkeypatch.setattr(cli, "get_process_controller", lambda: fake_ctrl)

    # Pre-create the files that force cleanup should remove
    (tmp_path / "yohoho.pid").write_text("4242")
    (tmp_path / "stop").write_text("1")
    (tmp_path / "state.json").write_text("{}")

    rc = run_stop(tmp_path, grace_s=0)

    assert fake_ctrl.terminated == [(4242, False)]
    assert not (tmp_path / "yohoho.pid").exists()
    assert not (tmp_path / "stop").exists()
    assert not (tmp_path / "state.json").exists()
    out = capsys.readouterr().out
    assert "force-stopped" in out
    assert rc == 0


# ---------------------------------------------------------------------------
# _has_tty tests
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, result):
        # result: True/False to return, or an Exception class/instance to raise.
        self._result = result

    def isatty(self):
        if isinstance(self._result, type) and issubclass(self._result, BaseException):
            raise self._result()
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


def test_has_tty_true_when_both_ttys(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", _FakeStream(True))
    monkeypatch.setattr(cli.sys, "stdout", _FakeStream(True))
    assert cli._has_tty() is True


def test_has_tty_false_when_stdin_none(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", None)
    monkeypatch.setattr(cli.sys, "stdout", _FakeStream(True))
    assert cli._has_tty() is False


def test_has_tty_false_when_isatty_raises(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", _FakeStream(ValueError))
    monkeypatch.setattr(cli.sys, "stdout", _FakeStream(True))
    assert cli._has_tty() is False


# ---------------------------------------------------------------------------
# marker un-scoping test
# ---------------------------------------------------------------------------


def test_non_daemon_commands_do_not_write_markers(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_default_data_dir", lambda: tmp_path)

    main(["status"])

    assert not (tmp_path / "running").exists()
    assert not (tmp_path / "clean_shutdown").exists()
