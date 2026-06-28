from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Sequence

from yohoho.core.config import data_dir


class MacProcessController:
    def spawn_detached(self, argv: Sequence[str]) -> int:
        log_dir = data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        # Popen dups the fd synchronously, so the child keeps its own copy after
        # the parent closes this handle on exiting the with-block.
        with open(log_dir / "daemon.out", "ab") as out:
            p = subprocess.Popen(
                list(argv),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
            )
        return p.pid

    def is_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def terminate(self, pid: int, graceful: bool = True) -> None:
        if not self.is_alive(pid):
            return
        # The target can exit between is_alive() and os.kill(); a vanished
        # process means termination already succeeded, so swallow the race.
        try:
            os.kill(pid, signal.SIGTERM if graceful else signal.SIGKILL)
        except ProcessLookupError:
            return
        if not graceful:
            return
        for _ in range(50):
            if not self.is_alive(pid):
                return
            time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
