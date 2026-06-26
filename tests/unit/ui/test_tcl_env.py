import os

from yohoho.core.ui._tcl_env import ensure_tcl_env


def test_respects_preexisting_tcl_library(monkeypatch):
    monkeypatch.setenv("TCL_LIBRARY", "/already/set")
    ensure_tcl_env()
    assert os.environ["TCL_LIBRARY"] == "/already/set"  # never overrides


def test_sets_nothing_when_no_lib_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("TCL_LIBRARY", raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "bin" / "python"))
    ensure_tcl_env()
    assert "TCL_LIBRARY" not in os.environ  # no tcl dir found → no-op, no crash
