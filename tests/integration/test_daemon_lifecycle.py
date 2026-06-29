"""
Gated integration test: proves the real detach-survives-parent + stop lifecycle.

Requirements to run:
- macOS (darwin) with a GUI login session (Tk window will briefly appear)
- `uv run yohoho` available (i.e. run via `uv run pytest -m integration -k daemon`)

Mark: pytest.mark.integration  (skip unless -m integration is passed)
"""
import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.integration

# `pty` is POSIX-only (it imports termios). This lifecycle test is macOS-first anyway, but the default
# `-m 'not integration'` deselect happens AFTER collection — so a module-level POSIX import would crash
# the ENTIRE Windows test run at collection time. Skip the module on Windows before importing pty.
if sys.platform == "win32":
    pytest.skip("daemon-lifecycle integration is POSIX/macOS-only (uses pty)", allow_module_level=True)

import pty  # noqa: E402


@pytest.mark.skipif(sys.platform != "darwin", reason="daemon lifecycle integration is macOS-first")
def test_start_detaches_survives_parent_then_stop_cleans_up(tmp_path):
    """
    Full lifecycle: `yohoho start` in a pty detaches into the background, writes a
    pidfile, stays alive after the launcher exits, and `yohoho stop` gracefully
    terminates it and removes the pidfile.

    Isolation: YOHOHO_DATA_DIR redirects all I/O to tmp_path; HF_HUB_OFFLINE=1
    prevents any model download (the daemon acquires the pidfile BEFORE the engine
    loads, so a failed load still lets us test the lifecycle).
    """
    env = {
        **os.environ,
        "YOHOHO_DATA_DIR": str(tmp_path),
        "HF_HUB_OFFLINE": "1",
    }
    pidfile = tmp_path / "yohoho.pid"

    # A pty makes _has_tty() return True so `yohoho start` actually detaches.
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["uv", "run", "yohoho", "start"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )

    daemon_pid = None
    try:
        # The launcher forks, prints "started (pid N)", and exits quickly.
        proc.wait(timeout=60)

        # The detached child writes its pidfile before loading the engine.
        for _ in range(150):  # up to ~15 s
            if pidfile.exists():
                break
            time.sleep(0.1)

        assert pidfile.exists(), "daemon pidfile never appeared after `yohoho start`"
        daemon_pid = int(pidfile.read_text().strip())

        # Daemon must be alive and must NOT be the launcher process.
        os.kill(daemon_pid, 0)  # raises ProcessLookupError if dead
        assert daemon_pid != proc.pid, "daemon pid must differ from launcher pid (detached)"

        # Optional: verify it was reparented to init (survived parent exit).
        try:
            ppid_out = subprocess.check_output(
                ["/bin/ps", "-o", "ppid=", "-p", str(daemon_pid)],
                text=True,
            ).strip()
            ppid = int(ppid_out)
            # On macOS the reparented ppid is 1 (launchd).
            assert ppid == 1, f"expected daemon reparented to launchd (ppid=1), got ppid={ppid}"
        except (subprocess.CalledProcessError, ValueError):
            # ps may race if the process exits quickly; don't hard-fail on this optional check.
            pass

        # Stop from "another terminal" — simulates the user running `yohoho stop`.
        stop = subprocess.run(
            ["uv", "run", "yohoho", "stop"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert stop.returncode == 0, f"`yohoho stop` failed:\n{stop.stderr}"

        # Pidfile must disappear after graceful stop.
        for _ in range(150):  # up to ~15 s
            if not pidfile.exists():
                break
            time.sleep(0.1)

        assert not pidfile.exists(), "pidfile not cleaned up after `yohoho stop`"

        # Daemon process must be dead.
        with pytest.raises(ProcessLookupError):
            os.kill(daemon_pid, 0)

    finally:
        os.close(master)
        os.close(slave)
        # Bulletproof cleanup: kill the daemon (and Tk window) if any assertion failed.
        if daemon_pid is not None:
            try:
                os.kill(daemon_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.poll() is None:
            proc.kill()
