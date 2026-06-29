import json
import os
import sys
from pathlib import Path
from yohoho.core.daemon import PidFile, StateWriter, EXIT_ALREADY_RUNNING, run_daemon, _default_alive
from yohoho.core.observability import detect_prior_crash

def test_acquire_writes_own_pid(tmp_path):
    pf = PidFile(tmp_path)
    assert pf.acquire() is True
    assert pf.read_pid() == os.getpid()
    assert pf.is_running() is True

def test_double_acquire_same_process_refused(tmp_path):
    pf = PidFile(tmp_path)
    assert pf.acquire() is True
    other = PidFile(tmp_path)
    assert other.is_running() is True

def test_stale_pidfile_reclaimed_and_flags_crash(tmp_path):
    (tmp_path / "yohoho.pid").write_text("999999")
    pf = PidFile(tmp_path)
    assert pf.is_running() is False
    assert pf.acquire() is True
    assert pf.crashed_prior_run is True
    assert pf.read_pid() == os.getpid()

def test_release_removes_file(tmp_path):
    pf = PidFile(tmp_path)
    pf.acquire()
    pf.release()
    assert not (tmp_path / "yohoho.pid").exists()
    assert pf.is_running() is False

def test_is_running_false_when_no_file(tmp_path):
    assert PidFile(tmp_path).is_running() is False

def test_acquire_blocks_against_live_holder(tmp_path):
    pf = PidFile(tmp_path)
    assert pf.acquire() is True
    other = PidFile(tmp_path)
    assert other.acquire() is False

def test_acquire_blocks_against_live_foreign_holder(tmp_path):
    (tmp_path / "yohoho.pid").write_text("999999")
    pf = PidFile(tmp_path, alive=lambda pid: True)
    assert pf.acquire() is False
    assert pf.read_pid() == 999999


def test_state_writer_writes_atomic_json(tmp_path):
    sw = StateWriter(tmp_path, hotkey="rcmd", started_at="2026-06-28T00:00:00Z")
    sw.set("loading")
    data = json.loads((tmp_path / "state.json").read_text())
    assert data["state"] == "loading"
    assert data["pid"] == os.getpid()
    assert data["hotkey"] == "rcmd"
    assert data["started_at"] == "2026-06-28T00:00:00Z"


def test_state_writer_overwrites(tmp_path):
    sw = StateWriter(tmp_path, hotkey="rcmd", started_at="t")
    sw.set("loading")
    sw.set("idle")
    sw.set("recording")
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "recording"


def test_state_writer_clear_removes_file(tmp_path):
    sw = StateWriter(tmp_path, hotkey="rcmd", started_at="t")
    sw.set("idle")
    sw.clear()
    assert not (tmp_path / "state.json").exists()


# ---------------------------------------------------------------------------
# run_daemon lifecycle tests (T7)
# ---------------------------------------------------------------------------


def test_run_daemon_acquires_and_releases(tmp_path, monkeypatch):
    """Pidfile is held during the loop body and released cleanly after run_daemon returns."""
    seen = {}

    def fake_loop(data_dir, state_writer=None, record_error=None):
        seen["pid_running"] = PidFile(data_dir).is_running()
        state_writer.set("idle")
        seen["state_mid"] = (Path(data_dir) / "state.json").exists()

    monkeypatch.setattr("yohoho.core.run_loop.run_start_loop", fake_loop)
    result = run_daemon(tmp_path)
    assert result == 0
    assert seen.get("pid_running") is True, "pidfile should be live DURING loop"
    assert seen.get("state_mid") is True, "state.json should exist DURING loop"
    assert PidFile(tmp_path).is_running() is False, "pidfile should be released AFTER run"
    # Meaningful now that state.json existed mid-loop: clear() must have removed it.
    assert not (tmp_path / "state.json").exists(), "state.json should be cleared AFTER run"


def test_run_daemon_refuses_when_live_instance_held(tmp_path, monkeypatch):
    """Returns EXIT_ALREADY_RUNNING without calling the loop when another instance is live."""
    holder = PidFile(tmp_path)
    assert holder.acquire() is True  # simulate a live process holding the pidfile

    loop_called = []

    def fake_loop(data_dir, state_writer=None, record_error=None):
        loop_called.append(True)

    monkeypatch.setattr("yohoho.core.run_loop.run_start_loop", fake_loop)
    result = run_daemon(tmp_path)
    assert result == EXIT_ALREADY_RUNNING
    assert not loop_called, "loop must NOT be called when already running"
    # The holder's pidfile must be untouched
    assert PidFile(tmp_path).read_pid() == os.getpid()


def test_run_daemon_writes_and_clears_markers(tmp_path, monkeypatch):
    """Running marker exists mid-loop; detect_prior_crash is False after clean exit."""
    running_marker = tmp_path / "running"
    captured = {}

    def fake_loop(data_dir, state_writer=None, record_error=None):
        captured["running_mid"] = running_marker.exists()

    monkeypatch.setattr("yohoho.core.run_loop.run_start_loop", fake_loop)
    run_daemon(tmp_path)
    assert captured.get("running_mid") is True, "'running' marker must exist during loop"
    assert detect_prior_crash(tmp_path) is False, "clean_shutdown must be written; no prior crash"


def test_run_daemon_started_at_injectable(tmp_path, monkeypatch):
    """Injected 'now' appears in state.json; hotkey matches config defaults."""
    captured = {}

    def fake_loop(data_dir, state_writer=None, record_error=None):
        # StateWriter only writes on .set(); trigger a write so we can read it back.
        if state_writer is not None:
            state_writer.set("idle")
            state_json = data_dir / "state.json"
            captured["state"] = json.loads(state_json.read_text())

    monkeypatch.setattr("yohoho.core.run_loop.run_start_loop", fake_loop)
    run_daemon(tmp_path, now=lambda: "2026-06-28T00:00:00Z")
    state = captured.get("state", {})
    assert state.get("started_at") == "2026-06-28T00:00:00Z"
    # No config.yaml in tmp_path → load_config returns defaults → hotkey = "ctrl+alt+space"
    assert state.get("hotkey") == "ctrl+alt+space"


# --- PidFile default liveness routing (0.2.1 Windows fix) ----------------------
# Guards the headline fix: os.kill(pid, 0) is NOT a valid liveness probe on Windows
# (raises OSError WinError 87 for a dead pid), so _default_alive must route win32
# through the ProcessController seam while POSIX keeps the os.kill probe.

def test_default_alive_posix_uses_os_kill(monkeypatch):
    """POSIX (macOS/Linux): _default_alive probes via os.kill (pid_alive), unchanged."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _default_alive(os.getpid()) is True
    assert _default_alive(999999) is False


def test_default_alive_windows_routes_through_process_controller(monkeypatch):
    """win32: _default_alive must NOT use os.kill — it routes through
    get_process_controller().is_alive (the seam that actually works on Windows)."""
    monkeypatch.setattr(sys, "platform", "win32")
    seen = {}

    class _FakeController:
        def is_alive(self, pid):
            seen["pid"] = pid
            return True

    monkeypatch.setattr(
        "yohoho.core.platform_factory.get_process_controller",
        lambda: _FakeController(),
    )
    assert _default_alive(4242) is True
    assert seen["pid"] == 4242
