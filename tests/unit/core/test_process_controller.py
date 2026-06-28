from __future__ import annotations

import os
import subprocess
import sys

import pytest

from yohoho.core.platform_factory import get_process_controller
from yohoho.core.null_platform import NullProcessController

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


def test_factory_returns_a_controller():
    pc = get_process_controller()
    for attr in ("spawn_detached", "is_alive", "terminate"):
        assert hasattr(pc, attr)


def test_factory_does_not_load_expensive_deps():
    # Pins what the lightweight factory actually avoids: the heavy native stack.
    # Run in a clean interpreter — within the shared suite, sibling tests have
    # already imported these into sys.modules, so isolation is required to make
    # the assertion about *this call's* import side effects honest.
    # (tkinter is intentionally NOT asserted absent — the macos adapter loads it.)
    src = (
        "import sys\n"
        "from yohoho.core.platform_factory import get_process_controller\n"
        "get_process_controller()\n"
        "assert 'onnxruntime' not in sys.modules\n"
        "assert 'sounddevice' not in sys.modules\n"
        "assert not any(\n"
        "    m == 'objc' or m.startswith('objc') or 'pyobjc' in m\n"
        "    for m in list(sys.modules)\n"
        ")\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_null_controller_records_calls():
    pc = NullProcessController()
    pid = pc.spawn_detached(["yohoho", "_run-daemon"])
    assert isinstance(pid, int)
    assert pc.spawned == [["yohoho", "_run-daemon"]]
    pc.terminate(pid, graceful=True)
    assert pc.terminated == [(pid, True)]


@macos_only
def test_macos_is_alive_self():
    from yohoho.platform.macos.process import MacProcessController

    pc = MacProcessController()
    assert pc.is_alive(os.getpid()) is True
    assert pc.is_alive(999999) is False


@macos_only
def test_macos_terminate_dead_pid_does_not_raise():
    from yohoho.platform.macos.process import MacProcessController

    pc = MacProcessController()
    # A pid that does not exist must be treated as already-terminated, never raise.
    pc.terminate(999999, graceful=True)
    pc.terminate(999999, graceful=False)
