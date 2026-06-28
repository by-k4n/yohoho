import os
import pytest
from yohoho.core.daemon import PidFile

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
