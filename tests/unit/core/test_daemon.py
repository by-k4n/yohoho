import json
import os
from yohoho.core.daemon import PidFile, StateWriter

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
