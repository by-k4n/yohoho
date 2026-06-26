"""LaunchAgent autostart (launchctl) — native calls behind an injectable run() seam."""
from __future__ import annotations
import html
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional


def render_plist(
    label: str,
    program_args: list[str],
    stdout_path: Optional[str] = None,
    stderr_path: Optional[str] = None,
) -> str:
    safe_label = html.escape(label)
    args = "".join(f"      <string>{html.escape(a)}</string>\n" for a in program_args)
    logs = ""
    if stdout_path:
        logs += f"    <key>StandardOutPath</key>\n    <string>{html.escape(str(stdout_path))}</string>\n"
    if stderr_path:
        logs += f"    <key>StandardErrorPath</key>\n    <string>{html.escape(str(stderr_path))}</string>\n"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n  <dict>\n'
        f'    <key>Label</key>\n    <string>{safe_label}</string>\n'
        f'    <key>ProgramArguments</key>\n    <array>\n{args}    </array>\n'
        '    <key>RunAtLoad</key>\n    <true/>\n'
        '    <key>KeepAlive</key>\n    <true/>\n'
        f'{logs}'
        '  </dict>\n</plist>\n'
    )


def _real_run(argv: list[str]) -> int:
    """Run *argv*; return its exit code (so callers can tell success from failure).

    A launch failure (e.g. launchctl missing from PATH) is reported as a non-zero
    code, not an exception, so enable()/disable() degrade gracefully.
    """
    try:
        return subprocess.run(argv, check=False).returncode
    except OSError:
        return 1


def _default_plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


class MacAutostart:
    def __init__(self, label: str = "pro.bykc.yohoho", program_args: Optional[list[str]] = None,
                 plist_path: Optional[Path] = None, uid: Optional[int] = None,
                 log_dir: Optional[Path] = None,
                 run: Callable[[list[str]], int] = _real_run) -> None:
        self._label = label
        self._args = program_args or []
        self._plist = Path(plist_path) if plist_path else _default_plist_path(label)
        self._uid = uid if uid is not None else os.getuid()
        self._run = run
        self._target = f"gui/{self._uid}/{self._label}"
        if log_dir is not None:
            ld = Path(log_dir)
            self._stdout: Optional[Path] = ld / "yohoho.out.log"
            self._stderr: Optional[Path] = ld / "yohoho.err.log"
        else:
            self._stdout = self._stderr = None

    def enable(self) -> bool:
        """Install + (re)load the LaunchAgent.  Returns True iff it is loaded after."""
        self._plist.parent.mkdir(parents=True, exist_ok=True)
        if self._stdout is not None:
            self._stdout.parent.mkdir(parents=True, exist_ok=True)
        self._plist.write_text(render_plist(self._label, self._args, self._stdout, self._stderr))

        self._run(["launchctl", "bootout", self._target])  # idempotent: not-loaded is fine
        rc = self._run(["launchctl", "bootstrap", f"gui/{self._uid}", str(self._plist)])
        if rc not in (0, None):
            # bootstrap usually fails here because the agent is *still loaded* (EIO 5)
            # from a too-quick re-setup; kickstart -k restarts it from the new plist.
            self._run(["launchctl", "kickstart", "-k", self._target])
        return self.is_loaded()

    def is_loaded(self) -> bool:
        """True if launchd currently has the agent loaded (``launchctl print`` exits 0)."""
        return self._run(["launchctl", "print", self._target]) == 0

    def disable(self) -> None:
        self._run(["launchctl", "bootout", self._target])
        if self._plist.exists():
            self._plist.unlink()

    def is_enabled(self) -> bool:
        return self._plist.exists()
