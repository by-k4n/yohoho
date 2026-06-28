from __future__ import annotations

import os
import sys

import pytest

from yohoho.core.platform_factory import get_process_controller
from yohoho.core.null_platform import NullProcessController

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


def test_factory_returns_a_controller():
    pc = get_process_controller()
    for attr in ("spawn_detached", "is_alive", "terminate"):
        assert hasattr(pc, attr)


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
