"""yohoho CLI entry point.

Dispatches subcommands:
  dictate    — record N seconds, transcribe on-device, print transcript (M1 dev command)
  panel-demo — drive the status panel through all states with synthetic data (M2)
  config     — get/set/list/reset config values
  doctor     — show permission status
  setup      — first-run: hotkey + permissions + model download + autostart
  start      — run the dictation daemon in the background (detaches from the terminal)
  stop       — stop the background agent
  status     — show daemon status (running, state, hotkey, model, permissions)
  history    — show recent dictation history
  logs       — show / follow the daemon log

Entry point: ``yohoho.core.cli:main`` (declared in pyproject.toml).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

from yohoho.core.config import data_dir as _default_data_dir
from yohoho.core.config import load_config, save_config, _config_as_dict
from yohoho.core.controller import Controller
from yohoho.core.engine import ParakeetEngine
from yohoho.core.events import ErrorCode, Terminal
from yohoho.core.history import HistoryStore
from yohoho.core.null_platform import make_null_platform
from yohoho.core.daemon import PidFile, run_daemon
from yohoho.core.observability import (
    install_crash_net,
    setup_logging,
)
from yohoho.core.platform_factory import get_process_controller
from yohoho.core.recorder import Recorder

_log = logging.getLogger("yohoho.cli")


# ---------------------------------------------------------------------------
# Thin seams (monkeypatched in tests)
# ---------------------------------------------------------------------------


def _make_engine(data_dir: Path) -> ParakeetEngine:
    """Return a ParakeetEngine rooted in *data_dir*.  Replaced by tests."""
    return ParakeetEngine(data_dir=data_dir)


def _has_tty() -> bool:
    """True only if BOTH stdin and stdout are real interactive terminals.
    None-guarded: pythonw (Windows) and detached children set these to None, so
    a bare sys.stdout.isatty() would crash — that's the whole point of the rule."""
    for stream in (sys.stdin, sys.stdout):
        isatty = getattr(stream, "isatty", None)
        if not callable(isatty):
            return False
        try:
            if not isatty():
                return False
        except (ValueError, OSError):
            return False
    return True


def _capture_seconds(
    device: Optional[int],
    seconds: int,
    on_amp: object,
) -> Optional[np.ndarray]:
    """Record *seconds* from *device* and return the 16 kHz float32 clip.

    Returns None on PortAudio failure (message already printed to stderr).
    Replaced by monkeypatch in unit tests so no real mic is touched.
    """
    rec = Recorder(device_index=device, on_amplitude=on_amp)
    err = rec.start()
    if err is not None:
        print(f"yohoho: microphone error — {err.message}", file=sys.stderr)
        return None
    time.sleep(seconds)
    return rec.stop()


# ---------------------------------------------------------------------------
# Terminal-event callback
# ---------------------------------------------------------------------------


def _on_terminal(ev) -> None:  # noqa: ANN001
    """Print error details to stderr; stay silent on DONE/CANCELLED."""
    if ev.kind == Terminal.ERROR:
        code = ev.code.value if ev.code is not None else "UNKNOWN"
        # TODO(M4): also persist via observability.record_error(data_dir, code, ...) so
        # `yohoho status` can surface the last error after the panel/process is gone.
        print(f"yohoho: error {code}", file=sys.stderr)


# ---------------------------------------------------------------------------
# dictate command
# ---------------------------------------------------------------------------


def run_dictate(
    seconds: int,
    device: Optional[int],
    data_dir: Path,
    save: Optional[str] = None,
    no_panel: bool = False,
) -> None:
    """Record → transcribe → print pipeline (NullPlatform, no hotkey/UI).

    All progress feedback goes to stderr so stdout carries ONLY the transcript
    (pipe-friendly). The transcript itself is printed by the NullPlatform stdout
    injector when speech is detected.

    When ``no_panel`` is False (the default) and ``cfg.ui["show_panel"]`` is True,
    the status panel runs on the main thread while the pipeline runs on a daemon
    worker thread.  Pass ``no_panel=True`` (or ``--no-panel`` on the CLI) for the
    M1 synchronous path — fully pipe-friendly and headless.
    """
    # Config + device resolution (needed by both paths)
    cfg = load_config(data_dir / "config.yaml")
    resolved_device = device if device is not None else cfg.audio["device_index"]

    show_panel = (not no_panel) and cfg.ui.get("show_panel", True)

    if not show_panel:
        # ------------------------------------------------------------------
        # M1 synchronous path — unchanged; covered by existing unit tests.
        # ------------------------------------------------------------------
        # 1. Engine load
        print("yohoho: loading model…", file=sys.stderr, flush=True)
        engine = _make_engine(data_dir)
        engine.load()

        # 2. Optional warmup (ParakeetEngine has it; FakeEngine does not)
        warmup = getattr(engine, "warmup", None)
        if callable(warmup):
            warmup()

        # 3. Build controller components
        bundle = make_null_platform()
        hist = HistoryStore(
            data_dir,
            enabled=cfg.history["enabled"],
            max_entries=cfg.history["max_entries"],
            max_age_days=cfg.history["max_age_days"],
        )
        ctl = Controller(engine=engine, bundle=bundle, history=hist, on_terminal=_on_terminal)

        # 4. Capture audio — tell the user it is listening NOW
        print(f"yohoho: ● recording {seconds}s — speak now…", file=sys.stderr, flush=True)
        on_amp = lambda level: None  # noqa: E731  — no UI in M1
        audio = _capture_seconds(resolved_device, seconds, on_amp)
        if audio is None:
            print("yohoho: no audio captured", file=sys.stderr)
            return

        # 4a. Optionally persist the clip (e.g. to create a test fixture)
        if save is not None:
            import soundfile as sf

            sf.write(save, audio, 16000, subtype="PCM_16")
            print(f"yohoho: saved {seconds}s clip → {save}", file=sys.stderr, flush=True)

        # 5. Run through the controller pipeline; NullPlatform injector prints to stdout
        print("yohoho: transcribing…", file=sys.stderr, flush=True)
        ctl.toggle()
        ctl.feed_audio_result(audio)
        ctl.wait_idle()

        # 6. If nothing was transcribed (silence/empty), say so — otherwise the user
        #    sees a blank line and assumes it is broken.
        if not bundle.clipboard.get_text():
            print(
                "yohoho: (no speech detected) — did you speak during the recording window?\n"
                "        on macOS, check System Settings ▸ Privacy & Security ▸ Microphone "
                "is enabled for your terminal app, then try again.",
                file=sys.stderr,
            )
        return

    # ------------------------------------------------------------------
    # Panel path: Tk owns the MAIN thread; pipeline runs on a daemon worker.
    # Off-main threads NEVER touch Tk — only q.put(event_dict).
    # ------------------------------------------------------------------
    import queue
    import threading
    import tkinter as tk

    from yohoho.core.platform_factory import get_platform  # noqa: PLC0415
    import yohoho.core.ui  # noqa: F401  — applies the Tcl env shim and DPI-awareness hook on import
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel, level_from_raw
    from yohoho.core.ui.runner import PanelRunner

    root = tk.Tk()
    root.withdraw()
    model = PanelModel(columns=44, rows=7)
    _chrome = get_platform().window_chrome                         # real OS chrome for the panel
    panel = StatusPanel(root, model, window_chrome=_chrome)
    q: "queue.Queue[dict]" = queue.Queue()

    def _worker() -> None:
        """Run the full record→transcribe pipeline on a daemon thread.

        ONLY communicates back via q.put(event_dict) — never touches Tk. Any
        unhandled failure becomes an ERROR(MODEL) terminal so the panel always
        reaches a terminal state (otherwise the mainloop would block forever).
        """
        try:
            print("yohoho: loading model…", file=sys.stderr, flush=True)
            engine = _make_engine(data_dir)
            engine.load()
            warmup = getattr(engine, "warmup", None)
            if callable(warmup):
                warmup()
            bundle = make_null_platform()
            hist = HistoryStore(
                data_dir,
                enabled=cfg.history["enabled"],
                max_entries=cfg.history["max_entries"],
                max_age_days=cfg.history["max_age_days"],
            )
            ctl = Controller(
                engine=engine,
                bundle=bundle,
                history=hist,
                on_terminal=lambda e: q.put({"t": "terminal", "kind": e.kind, "code": e.code}),
                on_status=lambda s: q.put({"t": "state", "state": s}),
            )
            ctl.toggle()  # -> RECORDING: panel reveals, waveform starts
            print(f"yohoho: ● recording {seconds}s — speak now…", file=sys.stderr, flush=True)
            on_amp = lambda raw: q.put({"t": "amp", "level": level_from_raw(raw)})  # noqa: E731
            audio = _capture_seconds(resolved_device, seconds, on_amp)
            if audio is None:
                q.put({"t": "terminal", "kind": Terminal.ERROR, "code": ErrorCode.MIC})
                return
            if save is not None:
                import soundfile as sf

                sf.write(save, audio, 16000, subtype="PCM_16")
            ctl.feed_audio_result(audio)
            ctl.wait_idle()
        except Exception:
            q.put({"t": "terminal", "kind": Terminal.ERROR, "code": ErrorCode.MODEL})

    # Late binding: _on_done only runs inside runner.run() (after the assignment
    # below), so `runner` is always the real instance by the time it's called.
    runner: Optional[PanelRunner] = None

    def _on_done() -> None:
        if runner is not None:
            runner.stop()

    runner = PanelRunner(root, panel, model, q, on_done=_on_done, window_chrome=_chrome)
    threading.Thread(target=_worker, daemon=True).start()
    runner.run()  # blocks on the main thread until stop()


# ---------------------------------------------------------------------------
# panel-demo command (M2): drive the status panel with synthetic data
# ---------------------------------------------------------------------------


def run_panel_demo(cycle: bool, state: Optional[str], seconds: int) -> None:
    """Run the status panel with a synthetic event producer (no audio/engine).

    Tk mainloop owns the MAIN thread here; a daemon producer thread pushes the
    synthetic sequence to a queue and NEVER touches Tk.  Used by the maintainer
    to verify (1) the panel does NOT steal keyboard focus while animating — the
    load-bearing macOS check — and (2) the visuals across every state.
    """
    import queue
    import threading
    import tkinter as tk

    from yohoho.core.platform_factory import get_platform  # noqa: PLC0415
    import yohoho.core.ui  # noqa: F401  — applies the Tcl env shim and DPI-awareness hook on import
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel
    from yohoho.core.ui.runner import TICK_S, PanelRunner, demo_events

    root = tk.Tk()
    root.withdraw()
    model = PanelModel(columns=44, rows=7)
    _chrome = get_platform().window_chrome                         # real OS chrome for the panel
    panel = StatusPanel(root, model, window_chrome=_chrome)
    q: "queue.Queue[dict]" = queue.Queue()

    def produce() -> None:
        """Daemon producer: replay demo_events with realistic cadence.

        ONLY q.put(...) — never panel/root/Tk (those live on the main thread).
        """
        from yohoho.core.events import State as _State
        from yohoho.core.events import Terminal as _Terminal

        # Hold-only states (no natural terminal) get a dwell before the {quit}
        # sentinel so the maintainer can eyeball them.
        hold_states = {"recording", "transcribing", "cancelled"}
        while True:
            for ev in demo_events(state, seconds, cycle=cycle):
                if ev.get("t") == "quit" and state in hold_states:
                    time.sleep(4.0)  # let the held state linger before exit
                q.put(ev)
                if ev.get("t") == "amp":
                    time.sleep(TICK_S)  # ~55 ms between waveform frames
                elif ev.get("t") == "state" and ev.get("state") is _State.TRANSCRIBING:
                    # After TRANSCRIBING, give the bar ~1.5 s to ease before DONE.
                    time.sleep(1.5)
                elif ev.get("t") == "terminal":
                    # Let an error linger so it's readable; brief pause otherwise.
                    time.sleep(3.0 if ev.get("kind") is _Terminal.ERROR else 0.3)
            if not cycle:
                # Single pass: demo_events already queued a {quit} sentinel.
                return
            # --cycle: brief gap before replaying the sequence.
            time.sleep(0.6)

    threading.Thread(target=produce, daemon=True).start()
    PanelRunner(root, panel, model, q, window_chrome=_chrome).run()


# ---------------------------------------------------------------------------
# config command (Task 11)
# ---------------------------------------------------------------------------

def _fmt_value(v) -> str:
    from yohoho.core.config_access import format_value
    return format_value(v)


def _print_settings_table(rows) -> None:
    headers = ("SETTING", "CURRENT", "DEFAULT", "DESCRIPTION")
    table = [headers] + [(k, _fmt_value(c), _fmt_value(d), desc) for k, c, d, desc in rows]
    widths = [max(len(r[i]) for r in table) for i in range(3)]
    for row in table:
        print(f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  {row[3]}")


def run_config(args, data_dir: Path) -> None:
    """Get/set/reset/list config values.

    ``yohoho config``                  print full config as YAML
    ``yohoho config list``             table of every setting + default + description
    ``yohoho config <key>``            get one value
    ``yohoho config <key> <value>``    set + validate + save
    ``yohoho config reset <key|all>``  restore a key (or everything) to default
    """
    from yohoho.core import config_access as ca
    from yohoho.core.config import ConfigError

    cfg_path = data_dir / "config.yaml"
    cfg = load_config(cfg_path)
    key = getattr(args, "config_key", None)
    value = getattr(args, "config_value", None)

    if key is None:
        if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb":
            from yohoho.core.config_tui import run_menu
            run_menu(data_dir)
            return
        import yaml
        print(yaml.safe_dump(_config_as_dict(cfg), sort_keys=False, allow_unicode=True), end="")
        return

    # 'list' and 'reset' are reserved subcommand tokens: no settable leaf may be named either.
    if key == "list":
        _print_settings_table(ca.list_settings(cfg))
        return

    if key == "reset":
        if value is None:
            print("yohoho config: specify a key to reset, or 'all'", file=sys.stderr)
            return
        try:
            if value == "all":
                if not getattr(args, "yes", False):
                    try:
                        resp = input("Reset ALL settings to defaults? [y/N] ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        resp = ""
                    if resp not in ("y", "yes"):
                        print("aborted.")
                        return
                save_config(ca.reset_all(cfg), cfg_path)
                print("all settings reset to defaults.")
                return
            old = ca.get_value(cfg, value)
            new_cfg = ca.reset_value(cfg, value)
            save_config(new_cfg, cfg_path)
            print(f"{value}: {_fmt_value(old)} → {_fmt_value(ca.get_value(new_cfg, value))}  (default restored)")
        except (ca.SettingError, ConfigError) as exc:
            print(f"yohoho config: {exc}", file=sys.stderr)
        return

    if value is None:  # get one
        try:
            print(_fmt_value(ca.get_value(cfg, key)))
        except ca.SettingError as exc:
            print(f"yohoho config: {exc}", file=sys.stderr)
        return

    try:  # set
        old = ca.get_value(cfg, key)
        new_cfg = ca.set_value(cfg, key, value)
        save_config(new_cfg, cfg_path)
        print(f"{key}: {_fmt_value(old)} → {_fmt_value(ca.get_value(new_cfg, key))}  (saved)")
    except (ca.SettingError, ConfigError) as exc:
        print(f"yohoho config: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# doctor command (Task 11)
# ---------------------------------------------------------------------------


def run_doctor(data_dir: Path, platform=None) -> None:
    """Print the permission status for each required macOS permission."""
    if platform is None:
        from yohoho.core.platform_factory import get_platform
        platform = get_platform()

    status = platform.permissions.check()
    print("yohoho doctor — permission status:")
    for perm in status.permissions:
        indicator = "✓" if perm.state == "granted" else "✗"
        print(f"  {indicator} {perm.label} [{perm.key}]: {perm.state}")
        if perm.state != "granted" and perm.fix_hint:
            print(f"      fix: {perm.fix_hint}")
        if perm.state != "granted" and perm.deep_link:
            print(f"      open: {perm.deep_link}")
    ok_str = "ok" if status.identity_ok else "MISMATCH (re-run `yohoho setup` to refresh)"
    print(f"  identity_ok: {ok_str}")
    from yohoho.core.config import load_config
    from yohoho.core.run_loop import format_hotkey
    cfg = load_config(Path(data_dir) / "config.yaml")
    print(f"  hotkey: {format_hotkey(cfg.hotkey)}  ({cfg.hotkey})")
    print(f"  overall: {'OK' if status.ok else 'NOT OK'}")


# ---------------------------------------------------------------------------
# setup command (Task 12)
# ---------------------------------------------------------------------------

_DEFAULT_HOTKEY = "ctrl+alt+space"
_SETUP_POLL_MAX = 60       # max seconds to poll for permission grant
_SETUP_POLL_INTERVAL = 2   # seconds between checks


def run_setup(
    data_dir: Path,
    platform=None,
    args=None,
    engine_factory=None,
) -> None:
    """First-run wizard: hotkey → permissions → model download → autostart.

    Idempotent: safe to re-run after a permission grant or interpreter change.

    Parameters
    ----------
    platform:
        Injectable platform bundle (defaults to ``get_platform()``).
    args:
        Parsed CLI args namespace.  Expected attrs: ``no_autostart``, ``hotkey``.
    engine_factory:
        Callable(data_dir) → engine with a ``.load()`` method.  Defaults to
        ``_make_engine`` (the real ParakeetEngine).  Replaced in tests with a
        FakeEngine factory so no model is downloaded.
    """
    if platform is None:
        from yohoho.core.platform_factory import get_platform
        platform = get_platform()
    if engine_factory is None:
        engine_factory = _make_engine

    no_autostart = getattr(args, "no_autostart", False)
    hotkey_arg = getattr(args, "hotkey", None)

    # 1. Resolve hotkey
    cfg_path = data_dir / "config.yaml"
    cfg = load_config(cfg_path)
    hotkey = hotkey_arg or cfg.hotkey or _DEFAULT_HOTKEY

    # Validate the hotkey spec via the injected platform bundle (never import platform directly)
    if not platform.hotkeys.is_valid_spec(hotkey):
        print(f"yohoho setup: invalid hotkey spec {hotkey!r}. "
              "Use format like 'ctrl+alt+space'.", file=sys.stderr)
        return

    # Save hotkey to config
    d = _config_as_dict(cfg)
    d["hotkey"] = hotkey
    from yohoho.core.config import Config
    cfg = Config(**d)
    save_config(cfg, cfg_path)
    from yohoho.core.run_loop import format_hotkey
    print(f"yohoho setup: hotkey set to {format_hotkey(hotkey)}  ({hotkey})")

    # 2. Check / request permissions
    status = platform.permissions.check()
    denied = [p for p in status.permissions if p.state != "granted"]
    if denied:
        print("yohoho setup: requesting permissions…")
        print(platform.permissions.guide())
        platform.permissions.request()
        # Bounded poll until all are granted (or give up)
        import time as _time
        elapsed = 0
        while elapsed < _SETUP_POLL_MAX:
            _time.sleep(_SETUP_POLL_INTERVAL)
            elapsed += _SETUP_POLL_INTERVAL
            status = platform.permissions.check()
            denied = [p for p in status.permissions if p.state != "granted"]
            if not denied:
                break
            print(f"  still waiting for: {[p.key for p in denied]} "
                  f"({elapsed}/{_SETUP_POLL_MAX}s)…")
        else:
            print(
                "yohoho setup: timed out waiting for permissions. "
                "Grant them in System Settings, then re-run `yohoho setup`.",
                file=sys.stderr,
            )
            return

    print("yohoho setup: all permissions granted.")

    # 3. Ensure model is downloaded (load triggers download if needed)
    print("yohoho setup: ensuring model is downloaded…")
    engine = engine_factory(data_dir)
    engine.load()
    print("yohoho setup: model ready.")

    # 4. Record the granted Python interpreter path (for identity_ok check)
    cfg = load_config(cfg_path)
    d = _config_as_dict(cfg)
    d["macos"] = dict(d.get("macos") or {})
    d["macos"]["granted_python_path"] = sys.executable
    cfg = Config(**d)
    save_config(cfg, cfg_path)
    print(f"yohoho setup: recorded interpreter path: {sys.executable}")

    # 5. Enable autostart (unless --no-autostart)
    if not no_autostart:
        if platform.autostart.enable():
            print("yohoho setup: autostart enabled (yohoho starts on login).")
        else:
            print(
                "yohoho setup: could not enable autostart automatically. "
                "You can still run `yohoho start` yourself.",
                file=sys.stderr,
            )
    else:
        print("yohoho setup: skipping autostart (--no-autostart).")

    print("yohoho setup: done. Press your hotkey anywhere to dictate.")


# ---------------------------------------------------------------------------
# start command (Task 12)
# ---------------------------------------------------------------------------


def run_start(data_dir: Path) -> int:
    """Start the daemon: detach from an interactive terminal, else run foreground."""
    pidfile = PidFile(data_dir)
    if pidfile.is_running():
        print(f"yohoho: already running (pid {pidfile.read_pid()})")
        return 0
    if _has_tty():
        pid = get_process_controller().spawn_detached(["yohoho", "_run-daemon"])
        print(f"yohoho: started (pid {pid})")
        return 0
    # No interactive terminal (launchd / pythonw): run the daemon in the foreground.
    return run_daemon(data_dir)


# ---------------------------------------------------------------------------
# stop command (Task 12)
# ---------------------------------------------------------------------------


def run_stop(data_dir: Path, *, grace_s: float = 5.0) -> int:
    """Stop the running daemon. Graceful first (write the stop-sentinel the runner
    polls); if it doesn't exit within grace_s, force-terminate and clean up.

    NOTE (behavior change from M3): stop controls the PROCESS only — it no longer
    touches login autostart. 'stop + don't relaunch at login' would be a future
    --disable-autostart flag (out of scope)."""
    pidfile = PidFile(data_dir)
    # Capture pid BEFORE the is_running() check: if the daemon exits and releases
    # the pidfile between the two reads, read_pid() would return None and we'd
    # print "(pid None)" — or, in the re-acquire case, call terminate(None).
    pid = pidfile.read_pid()
    if pid is None or not pidfile.is_running():
        print("yohoho: not running")
        return 0
    # Graceful: the runner's 200ms poll loop sees this file, removes it, and stops
    # cleanly (the daemon then removes its own pidfile). This is the cross-platform
    # graceful path — a DETACHED_PROCESS Windows child can't receive signals.
    (data_dir / "stop").write_text("1", encoding="utf-8")
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not pidfile.is_running():
            print(f"yohoho: stopped (pid {pid})")
            return 0
        time.sleep(0.1)
    # Force: the daemon didn't exit in time. Kill it, then clean up the files it
    # couldn't (a force-killed / Windows-detached daemon never runs its finally).
    # That includes the "running" crash marker — without this a user-initiated
    # force-stop would leave detect_prior_crash() falsely reporting a crash.
    get_process_controller().terminate(pid, graceful=False)
    for name in ("yohoho.pid", "stop", "state.json", "running"):
        (data_dir / name).unlink(missing_ok=True)
    print(f"yohoho: force-stopped (pid {pid})")
    return 0


# ---------------------------------------------------------------------------
# status command (T9)
# ---------------------------------------------------------------------------


def _format_uptime(seconds: int) -> str:
    """Format an uptime in seconds as 'Xh Ym Zs' (omitting leading zero units).

    Clamps negatives to 0 so clock skew can never render garbage like '-1h 59m'."""
    seconds = max(0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or h:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def run_status(data_dir: Path, *, json_out: bool = False, platform=None) -> int:
    """Assemble and display the daemon status (running, state, hotkey, model, perms…)."""
    import json
    from datetime import datetime, timezone

    from yohoho.core.observability import detect_prior_crash, read_last_error
    from yohoho.core.run_loop import format_hotkey

    data_dir = Path(data_dir)
    pidfile = PidFile(data_dir)

    running = pidfile.is_running()
    pid = pidfile.read_pid()

    # Read state.json (tolerate missing/malformed)
    state_str: Optional[str] = None
    started_at_str = None
    hotkey_spec = None
    state_path = data_dir / "state.json"
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        state_str = raw.get("state")
        started_at_str = raw.get("started_at")
        hotkey_spec = raw.get("hotkey")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Hotkey fallback: config.yaml
    if hotkey_spec is None:
        try:
            hotkey_spec = load_config(data_dir / "config.yaml").hotkey
        except Exception:
            hotkey_spec = None

    # Uptime — tolerate a machine-written-but-corrupt started_at (wrong type or
    # garbage string), and clamp clock skew (started_at in the future) to 0.
    uptime_s: Optional[int] = None
    if running and started_at_str:
        try:
            started = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
            uptime_s = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
        except (ValueError, TypeError, AttributeError):
            uptime_s = None

    # Model ready
    model_ready: bool = (data_dir / "model_ready").exists()

    # Crash / last error
    crashed_last_run: bool = detect_prior_crash(data_dir)
    last_error = read_last_error(data_dir)

    # Permissions — best-effort; never crash status
    permissions_ok: Optional[bool] = None
    try:
        if platform is None:
            from yohoho.core.platform_factory import get_platform  # noqa: PLC0415
            platform = get_platform()
        permissions_ok = platform.permissions.check().ok
    except Exception:
        permissions_ok = None

    result = {
        "running": running,
        "pid": pid,
        "state": state_str,
        "started_at": started_at_str,
        "hotkey": hotkey_spec,
        "uptime_s": uptime_s,
        "model_ready": model_ready,
        "crashed_last_run": crashed_last_run,
        "last_error": last_error,
        "permissions_ok": permissions_ok,
    }

    if json_out:
        print(json.dumps(result))
        return 0

    # Human-readable output
    running_str = f"yes (pid {pid})" if running else "no"
    state_display = state_str or "—"
    uptime_display = _format_uptime(uptime_s) if uptime_s is not None else "—"
    if hotkey_spec:
        # format_hotkey splits on '+', so a non-str hotkey would crash; fall back
        # to the raw value (corrupt state.json must never crash status).
        try:
            hotkey_display = f"{format_hotkey(hotkey_spec)} ({hotkey_spec})"
        except (AttributeError, TypeError):
            hotkey_display = str(hotkey_spec)
    else:
        hotkey_display = "—"
    model_display = "ready" if model_ready else "not downloaded"
    if permissions_ok is True:
        perms_display = "OK"
    elif permissions_ok is False:
        perms_display = "NOT OK"
    else:
        perms_display = "unknown"
    crash_display = "crashed" if crashed_last_run else "clean"
    if last_error:
        err_display = f"{last_error.get('code', '?')} — {last_error.get('message', '')} ({last_error.get('ts', '')})"
    else:
        err_display = "none"

    print("yohoho status:")
    print(f"  running: {running_str}")
    print(f"  state: {state_display}")
    print(f"  uptime: {uptime_display}")
    print(f"  hotkey: {hotkey_display}")
    print(f"  model: {model_display}")
    print(f"  permissions: {perms_display}")
    print(f"  last run: {crash_display}")
    print(f"  last error: {err_display}")
    return 0


# ---------------------------------------------------------------------------
# history command (T9)
# ---------------------------------------------------------------------------


def run_history(data_dir: Path, *, n: int = 20, json_out: bool = False) -> int:
    """Display the most recent dictation history entries."""
    import json

    n = max(0, n)  # negative n with rows[::-1][:n] would drop the newest
    rows = HistoryStore(data_dir, enabled=True).read()
    # Newest-first then take the first n (n=0 → empty)
    rows = rows[::-1][:n]

    if json_out:
        print(json.dumps(rows))
        return 0

    if not rows:
        print("yohoho history: (empty)")
        return 0

    for entry in rows:
        ts = entry.get("ts", "")
        outcome = entry.get("outcome", "")
        word_count = entry.get("word_count", 0)
        text = entry.get("text", "")
        # Truncate long text for the human view
        if len(text) > 80:
            text = text[:77] + "..."
        print(f"{ts} · {outcome} · {word_count} words · {text}")

    return 0


# ---------------------------------------------------------------------------
# logs command (T9)
# ---------------------------------------------------------------------------


def _read_new(path: Path, offset: int) -> tuple[str, int]:
    """Read bytes appended to *path* since *offset*. Returns (text, new_offset).

    Binary mode + tell() gives an exact byte offset (no text-mode seek drift and
    no line-skip race). Resets to 0 if the file shrank (rotation/truncation)."""
    try:
        size = path.stat().st_size
    except OSError:
        return "", offset
    if size < offset:        # rotation/truncation → reread from the start
        offset = 0
    if size <= offset:
        return "", offset
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
            return data.decode("utf-8", errors="replace"), fh.tell()
    except OSError:
        return "", offset


def run_logs(data_dir: Path, *, n: int = 50, follow: bool = False) -> int:
    """Display (and optionally follow) the daemon log file."""
    log_path = data_dir / "logs" / "yohoho.log"
    if not log_path.exists():
        print("yohoho logs: (no log file yet)")
        return 0

    # Read and print the last n lines (n=0 → nothing; guard the lines[-0:] trap).
    n = max(0, n)
    content = log_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    tail = lines[-n:] if n else []
    for line in tail:
        print(line)

    if not follow:
        return 0

    # Follow mode: poll for newly-appended bytes via the pure _read_new helper.
    offset = log_path.stat().st_size  # start following AFTER the tail we just printed
    try:
        while True:
            time.sleep(0.5)
            text, offset = _read_new(log_path, offset)
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser for the ``yohoho`` CLI."""
    parser = argparse.ArgumentParser(
        prog="yohoho",
        description="Free, local voice-to-text for developers.",
    )
    subparsers = parser.add_subparsers(dest="cmd")

    # -- dictate --------------------------------------------------------------
    dictate_p = subparsers.add_parser("dictate", help="Record and transcribe (dev/M1 command)")
    dictate_p.add_argument(
        "--seconds",
        type=int,
        default=5,
        help="Seconds to record (default: 5)",
    )
    dictate_p.add_argument(
        "--device",
        type=int,
        default=None,
        help="PortAudio device index (default: from config)",
    )
    dictate_p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    dictate_p.add_argument(
        "--save",
        type=str,
        default=None,
        metavar="PATH",
        help="Save the captured 16kHz mono clip to this WAV path (for test fixtures)",
    )
    dictate_p.add_argument(
        "--no-panel",
        action="store_true",
        help="Don't show the status panel (plain stdout; for piping/headless)",
    )

    # -- panel-demo -----------------------------------------------------------
    demo_p = subparsers.add_parser(
        "panel-demo",
        help="Drive the status panel through its states with synthetic data (M2)",
    )
    demo_p.add_argument(
        "--cycle",
        action="store_true",
        help="Loop the state sequence forever (Ctrl+C to exit)",
    )
    demo_p.add_argument(
        "--state",
        choices=("recording", "transcribing", "done", "error", "cancelled"),
        default=None,
        help="Hold a single state instead of the full record→transcribe→done pass",
    )
    demo_p.add_argument(
        "--seconds",
        type=int,
        default=4,
        help="Recording duration per cycle in seconds (default: 4)",
    )

    # -- config ---------------------------------------------------------------
    config_p = subparsers.add_parser("config", help="Get, set, list, or reset config values")
    config_p.add_argument(
        "config_key",
        nargs="?",
        default=None,
        help="Config key, or 'list' / 'reset' (e.g. 'sounds.volume')",
    )
    config_p.add_argument(
        "config_value",
        nargs="?",
        default=None,
        help="New value to set (omit to print current value)",
    )
    config_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt for 'config reset all'",
    )

    # -- doctor ---------------------------------------------------------------
    subparsers.add_parser("doctor", help="Show macOS permission status")

    # -- setup ----------------------------------------------------------------
    setup_p = subparsers.add_parser(
        "setup",
        help="First-run: configure hotkey, grant permissions, download model, enable autostart",
    )
    setup_p.add_argument(
        "--no-autostart",
        action="store_true",
        help="Skip enabling the launch-on-login LaunchAgent",
    )
    setup_p.add_argument(
        "--hotkey",
        default=None,
        help=f"Activation hotkey spec (default: {_DEFAULT_HOTKEY!r})",
    )

    # -- start ----------------------------------------------------------------
    subparsers.add_parser(
        "start",
        help="Start the dictation daemon (detaches from the terminal; runs in the background)",
    )

    # -- stop -----------------------------------------------------------------
    subparsers.add_parser("stop", help="Stop the background yohoho agent")

    # -- _run-daemon (hidden: invoked by the detached child process) ----------
    subparsers.add_parser("_run-daemon", help=argparse.SUPPRESS)

    # -- status ---------------------------------------------------------------
    status_p = subparsers.add_parser(
        "status",
        help="Show daemon status (running, state, hotkey, model, permissions)",
    )
    status_p.add_argument(
        "--json",
        action="store_true",
        help="Output the status as JSON",
    )

    # -- history --------------------------------------------------------------
    history_p = subparsers.add_parser("history", help="Show recent dictation history")
    history_p.add_argument(
        "-n",
        type=int,
        default=20,
        help="Number of entries (default: 20)",
    )
    history_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON",
    )

    # -- logs -----------------------------------------------------------------
    logs_p = subparsers.add_parser("logs", help="Show the daemon log")
    logs_p.add_argument(
        "-n",
        type=int,
        default=50,
        help="Number of lines (default: 50)",
    )
    logs_p.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow the log (Ctrl+C to exit)",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv=None) -> int:  # noqa: ANN001
    """Parse arguments and dispatch to the appropriate handler.

    Returns an integer exit code (0 = success, 2 = no command given).
    Declared as the ``yohoho`` console_scripts entry point in pyproject.toml.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return 2

    dd = _default_data_dir()
    verbose = getattr(args, "verbose", False)
    logger = setup_logging(dd, level="debug" if verbose else "info")
    install_crash_net(dd, logger)

    if args.cmd == "dictate":
        run_dictate(args.seconds, args.device, dd, save=args.save, no_panel=args.no_panel)
    elif args.cmd == "panel-demo":
        run_panel_demo(args.cycle, args.state, args.seconds)
    elif args.cmd == "config":
        run_config(args, dd)
    elif args.cmd == "doctor":
        run_doctor(dd)
    elif args.cmd == "setup":
        run_setup(dd, args=args)
    elif args.cmd == "_run-daemon":
        return run_daemon(dd)
    elif args.cmd == "start":
        return run_start(dd)
    elif args.cmd == "stop":
        return run_stop(dd)
    elif args.cmd == "status":
        return run_status(dd, json_out=args.json)
    elif args.cmd == "history":
        return run_history(dd, n=args.n, json_out=args.json)
    elif args.cmd == "logs":
        return run_logs(dd, n=args.n, follow=args.follow)
    else:
        parser.print_help()
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
