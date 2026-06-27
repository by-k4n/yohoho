"""yohoho CLI entry point.

Dispatches subcommands:
  dictate    — record N seconds, transcribe on-device, print transcript (M1 dev command)
  panel-demo — drive the status panel through all states with synthetic data (M2)
  config     — get/set/list/reset config values
  doctor     — show permission status
  setup      — first-run: hotkey + permissions + model download + autostart
  start      — run the hotkey dictation loop (foreground)
  stop       — stop the background agent
  status / history / logs — stubs (M4)

Entry point: ``yohoho.core.cli:main`` (declared in pyproject.toml).
"""

from __future__ import annotations

import argparse
import logging
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
from yohoho.core.observability import (
    install_crash_net,
    mark_clean_shutdown,
    mark_running,
    setup_logging,
)
from yohoho.core.recorder import Recorder

_log = logging.getLogger("yohoho.cli")


# ---------------------------------------------------------------------------
# Thin seams (monkeypatched in tests)
# ---------------------------------------------------------------------------


def _make_engine(data_dir: Path) -> ParakeetEngine:
    """Return a ParakeetEngine rooted in *data_dir*.  Replaced by tests."""
    return ParakeetEngine(data_dir=data_dir)



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
    if v is None:
        return "(default)"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


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


def run_start(data_dir: Path) -> None:
    """Start the hotkey dictation loop (foreground — Tk panel + real bundle)."""
    from yohoho.core.run_loop import run_start_loop
    run_start_loop(data_dir)


# ---------------------------------------------------------------------------
# stop command (Task 12)
#
# M3 design decision: `stop` calls MacAutostart.disable() which issues
# `launchctl bootout` AND removes the plist.  This means `stop` is equivalent
# to "uninstall autostart + stop the running agent."  Re-run `yohoho setup` to
# restore autostart.  A more surgical "pause" (bootout but keep plist) is M4.
# ---------------------------------------------------------------------------


def run_stop(data_dir: Path, platform=None) -> None:
    """Stop the background yohoho agent via launchctl bootout."""
    from yohoho.core.platform_factory import get_platform
    platform = platform or get_platform()
    platform.autostart.disable()
    print("yohoho: stopped (autostart removed). Run `yohoho setup` to re-enable.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_STUB_CMDS = ("status", "history", "logs")


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
    subparsers.add_parser("start", help="Start the hotkey dictation loop (foreground)")

    # -- stop -----------------------------------------------------------------
    subparsers.add_parser("stop", help="Stop the background yohoho agent")

    # -- stubs ----------------------------------------------------------------
    for cmd in _STUB_CMDS:
        subparsers.add_parser(cmd, help=f"{cmd}: not yet implemented (M4)")

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
    mark_running(dd)

    try:
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
        elif args.cmd == "start":
            run_start(dd)
        elif args.cmd == "stop":
            run_stop(dd)
        elif args.cmd in _STUB_CMDS:
            print(f"{args.cmd}: not yet implemented (M4)")
        else:
            parser.print_help()
            return 2
    finally:
        mark_clean_shutdown(dd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
