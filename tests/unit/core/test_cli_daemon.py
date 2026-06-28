"""T8 + T9 tests: real start/stop daemon control + status/history/logs readers."""
from __future__ import annotations

import json
import os

import yohoho.core.cli as cli
from yohoho.core.cli import main, run_start, run_stop
from yohoho.core.daemon import PidFile
from yohoho.core.null_platform import NullProcessController


# ---------------------------------------------------------------------------
# Fake platform for injectable permission checks (T9)
# ---------------------------------------------------------------------------


class _FakePermResult:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok


class _FakePermissions:
    def __init__(self, ok: bool = True) -> None:
        self._ok = ok

    def check(self) -> _FakePermResult:
        return _FakePermResult(ok=self._ok)


class _FakePlatform:
    def __init__(self, ok: bool = True) -> None:
        self.permissions = _FakePermissions(ok=ok)


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


# ---------------------------------------------------------------------------
# T9: run_status tests
# ---------------------------------------------------------------------------


def _write_state_json(data_dir, state="idle", hotkey="ctrl+alt+space", started_at=None):
    import time as _time
    if started_at is None:
        started_at = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    payload = {
        "pid": os.getpid(),
        "state": state,
        "hotkey": hotkey,
        "started_at": started_at,
    }
    (data_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def test_status_running(tmp_path, capsys):
    """status with a running daemon shows running=yes, the state, and hotkey —
    and must NOT report a crash even though mark_running leaves the 'running'
    marker on disk for the daemon's whole lifetime."""
    from yohoho.core.cli import run_status

    # Write a live pidfile pointing at the current process
    (tmp_path / "yohoho.pid").write_text(str(os.getpid()), encoding="utf-8")
    _write_state_json(tmp_path, state="idle", hotkey="ctrl+alt+space")
    # Mimic what observability.mark_running leaves on disk for a live daemon:
    # the 'running' marker present and no 'clean_shutdown' marker.
    (tmp_path / "running").write_text("1", encoding="utf-8")

    rc = run_status(tmp_path, platform=_FakePlatform(ok=True))

    assert rc == 0
    out = capsys.readouterr().out
    assert "running" in out
    assert "yes" in out
    assert "idle" in out
    assert "ctrl+alt+space" in out
    # A live daemon is healthy — never "crashed".
    assert "crashed" not in out
    assert "last run: clean" in out


def test_status_running_json_not_crashed(tmp_path, capsys):
    """--json crashed_last_run is False for a live daemon, even with the
    'running' marker on disk (regression: gated on liveness)."""
    from yohoho.core.cli import run_status

    (tmp_path / "yohoho.pid").write_text(str(os.getpid()), encoding="utf-8")
    _write_state_json(tmp_path)
    (tmp_path / "running").write_text("1", encoding="utf-8")

    rc = run_status(tmp_path, json_out=True, platform=_FakePlatform(ok=True))

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["running"] is True
    assert data["crashed_last_run"] is False


def test_status_reports_crash_when_down_after_unclean_exit(tmp_path, capsys):
    """A dead daemon with the 'running' marker and no 'clean_shutdown' (kill -9)
    is correctly reported as crashed — human + --json."""
    from yohoho.core.cli import run_status

    # Dead pid + running marker, no clean_shutdown marker = unclean exit.
    (tmp_path / "yohoho.pid").write_text("999999", encoding="utf-8")
    (tmp_path / "running").write_text("1", encoding="utf-8")

    rc = run_status(tmp_path, platform=_FakePlatform(ok=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "last run: crashed" in out

    rc = run_status(tmp_path, json_out=True, platform=_FakePlatform(ok=True))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["running"] is False
    assert data["crashed_last_run"] is True


def test_status_not_running(tmp_path, capsys):
    """status with no pidfile shows running=no."""
    from yohoho.core.cli import run_status

    rc = run_status(tmp_path, platform=_FakePlatform(ok=True))

    assert rc == 0
    out = capsys.readouterr().out
    assert "running" in out
    assert "no" in out


def test_status_json(tmp_path, capsys):
    """--json flag emits valid JSON with expected keys."""
    from yohoho.core.cli import run_status

    (tmp_path / "yohoho.pid").write_text(str(os.getpid()), encoding="utf-8")
    _write_state_json(tmp_path)

    rc = run_status(tmp_path, json_out=True, platform=_FakePlatform(ok=True))

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    for key in ("running", "state", "hotkey", "model_ready", "crashed_last_run", "last_error"):
        assert key in data, f"missing key: {key}"


def test_status_permissions_ok_true(tmp_path, capsys):
    """permissions field shows OK when fake platform returns ok=True."""
    from yohoho.core.cli import run_status

    run_status(tmp_path, platform=_FakePlatform(ok=True))
    out = capsys.readouterr().out
    assert "OK" in out


def test_status_permissions_ok_false(tmp_path, capsys):
    """permissions field shows NOT OK when fake platform returns ok=False."""
    from yohoho.core.cli import run_status

    run_status(tmp_path, platform=_FakePlatform(ok=False))
    out = capsys.readouterr().out
    assert "NOT OK" in out


def test_status_permissions_exception_shows_unknown(tmp_path, capsys):
    """permissions field shows 'unknown' if the platform raises."""
    from yohoho.core.cli import run_status

    class _BrokenPlatform:
        class permissions:
            @staticmethod
            def check():
                raise RuntimeError("pyobjc not available")

    run_status(tmp_path, platform=_BrokenPlatform())
    out = capsys.readouterr().out
    assert "unknown" in out


# ---------------------------------------------------------------------------
# T9: run_history tests
# ---------------------------------------------------------------------------


def _write_history_jsonl(data_dir, records):
    """Write records (list of dicts) as JSONL to data_dir/history.jsonl."""
    lines = "\n".join(json.dumps(r) for r in records) + "\n"
    (data_dir / "history.jsonl").write_text(lines, encoding="utf-8")


def test_history_newest_first_and_limit(tmp_path, capsys):
    """run_history with n=2 returns the 2 newest; oldest is absent."""
    from yohoho.core.cli import run_history

    records = [
        {"v": 1, "id": "a", "ts": "2026-06-28T10:00:00+00:00", "dur_s": 1.0,
         "len": 5, "word_count": 1, "outcome": "PASTED", "text": "alpha"},
        {"v": 1, "id": "b", "ts": "2026-06-28T11:00:00+00:00", "dur_s": 1.0,
         "len": 4, "word_count": 1, "outcome": "PASTED", "text": "beta"},
        {"v": 1, "id": "c", "ts": "2026-06-28T12:00:00+00:00", "dur_s": 1.0,
         "len": 5, "word_count": 1, "outcome": "COPIED", "text": "gamma"},
    ]
    _write_history_jsonl(tmp_path, records)

    rc = run_history(tmp_path, n=2)

    assert rc == 0
    out = capsys.readouterr().out
    assert "gamma" in out   # newest
    assert "beta" in out    # second newest
    assert "alpha" not in out  # oldest — excluded by n=2


def test_history_empty(tmp_path, capsys):
    """run_history on empty dir prints (empty)."""
    from yohoho.core.cli import run_history

    rc = run_history(tmp_path)
    assert rc == 0
    assert "(empty)" in capsys.readouterr().out


def test_history_json(tmp_path, capsys):
    """--json emits valid JSON list of the selected entries."""
    from yohoho.core.cli import run_history

    records = [
        {"v": 1, "id": "x", "ts": "2026-06-28T09:00:00+00:00", "dur_s": 1.0,
         "len": 3, "word_count": 1, "outcome": "PASTED", "text": "foo"},
        {"v": 1, "id": "y", "ts": "2026-06-28T10:00:00+00:00", "dur_s": 1.0,
         "len": 3, "word_count": 1, "outcome": "PASTED", "text": "bar"},
    ]
    _write_history_jsonl(tmp_path, records)

    rc = run_history(tmp_path, n=5, json_out=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    # newest first: bar then foo
    assert data[0]["text"] == "bar"
    assert data[1]["text"] == "foo"


# ---------------------------------------------------------------------------
# T9: run_logs tests
# ---------------------------------------------------------------------------


def _write_log_lines(data_dir, lines):
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "yohoho.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_logs_tail(tmp_path, capsys):
    """run_logs n=5 prints the last 5 lines, not the first 5."""
    from yohoho.core.cli import run_logs

    # Use distinct non-overlapping labels to avoid substring confusion
    all_lines = [f"LOG_ENTRY_{i:03d}" for i in range(1, 11)]  # LOG_ENTRY_001 .. LOG_ENTRY_010
    _write_log_lines(tmp_path, all_lines)

    rc = run_logs(tmp_path, n=5)
    assert rc == 0
    out = capsys.readouterr().out
    for i in range(6, 11):   # last 5: entries 006–010
        assert f"LOG_ENTRY_{i:03d}" in out
    for i in range(1, 6):    # first 5: entries 001–005
        assert f"LOG_ENTRY_{i:03d}" not in out


def test_logs_no_file(tmp_path, capsys):
    """run_logs with no log file prints the expected message."""
    from yohoho.core.cli import run_logs

    rc = run_logs(tmp_path)
    assert rc == 0
    assert "(no log file yet)" in capsys.readouterr().out


def test_logs_n_zero_prints_nothing(tmp_path, capsys):
    """run_logs n=0 prints NOTHING (not the whole file — the lines[-0:] trap)."""
    from yohoho.core.cli import run_logs

    _write_log_lines(tmp_path, [f"LOG_ENTRY_{i:03d}" for i in range(1, 11)])

    rc = run_logs(tmp_path, n=0)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == ""


# ---------------------------------------------------------------------------
# T9: _read_new (pure log-follow helper) tests
# ---------------------------------------------------------------------------


def test_read_new_appends(tmp_path):
    """_read_new returns appended text and an advanced offset."""
    from yohoho.core.cli import _read_new

    p = tmp_path / "f.log"
    p.write_bytes(b"hello\n")
    text, off = _read_new(p, 0)
    assert text == "hello\n"
    assert off == 6

    # Append more; reading from the prior offset returns only the new bytes
    with p.open("ab") as fh:
        fh.write(b"world\n")
    text2, off2 = _read_new(p, off)
    assert text2 == "world\n"
    assert off2 == 12


def test_read_new_resets_on_shrink(tmp_path):
    """If the file shrinks below offset (rotation), _read_new rereads from start."""
    from yohoho.core.cli import _read_new

    p = tmp_path / "f.log"
    p.write_bytes(b"aaaaaaaaaa\n")  # 11 bytes
    _, off = _read_new(p, 0)
    assert off == 11

    # Rotation/truncation: file is now smaller than the old offset
    p.write_bytes(b"new\n")  # 4 bytes
    text, off2 = _read_new(p, off)
    assert text == "new\n"   # full new content, read from 0
    assert off2 == 4


def test_read_new_partial_line_not_duplicated(tmp_path):
    """A partial line (no trailing newline) is returned verbatim and NOT
    re-emitted once the rest of the line + newline arrives."""
    from yohoho.core.cli import _read_new

    p = tmp_path / "f.log"
    p.write_bytes(b"par")           # partial — no newline yet
    text1, off1 = _read_new(p, 0)
    assert text1 == "par"
    assert off1 == 3

    with p.open("ab") as fh:
        fh.write(b"tial\n")         # completes the line
    text2, off2 = _read_new(p, off1)
    assert text2 == "tial\n"        # only the NEW bytes — "par" not repeated
    assert off2 == 8


# ---------------------------------------------------------------------------
# T9: _format_uptime tests
# ---------------------------------------------------------------------------


def test_format_uptime_variants():
    from yohoho.core.cli import _format_uptime

    assert _format_uptime(0) == "0s"
    assert _format_uptime(45) == "45s"
    assert _format_uptime(3600) == "1h 0m 0s"
    assert _format_uptime(3 * 3600 + 12 * 60 + 7) == "3h 12m 7s"
    assert _format_uptime(192) == "3m 12s"


def test_format_uptime_negative_clamped():
    """Negative uptime (clock skew) renders as 0s, not garbage."""
    from yohoho.core.cli import _format_uptime

    assert _format_uptime(-5) == "0s"


# ---------------------------------------------------------------------------
# T9: malformed-TYPE state.json tolerance (status must never crash)
# ---------------------------------------------------------------------------


def test_status_tolerates_wrong_typed_state_json(tmp_path, capsys):
    """A machine-written-but-corrupt state.json (wrong value TYPES) must not
    crash status: int started_at + int hotkey → prints, uptime '—'."""
    from yohoho.core.cli import run_status

    (tmp_path / "yohoho.pid").write_text(str(os.getpid()), encoding="utf-8")
    (tmp_path / "state.json").write_text(
        json.dumps({"pid": os.getpid(), "state": "idle", "hotkey": 42, "started_at": 123}),
        encoding="utf-8",
    )

    rc = run_status(tmp_path, platform=_FakePlatform(ok=True))

    assert rc == 0
    out = capsys.readouterr().out
    assert "running" in out
    # Uptime could not be parsed from an int started_at → shows the em-dash
    assert "uptime: —" in out


# ---------------------------------------------------------------------------
# T9: no marker side-effects
# ---------------------------------------------------------------------------


def test_status_history_logs_do_not_write_markers(tmp_path):
    """None of the three reader commands touch running/clean_shutdown markers."""
    from yohoho.core.cli import run_status, run_history, run_logs

    run_status(tmp_path, platform=_FakePlatform(ok=True))
    run_history(tmp_path)
    run_logs(tmp_path)

    assert not (tmp_path / "running").exists()
    assert not (tmp_path / "clean_shutdown").exists()
