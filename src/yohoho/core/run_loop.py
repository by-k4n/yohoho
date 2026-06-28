from __future__ import annotations

import sys

from yohoho.core.events import State, Terminal, ErrorCode

# Modifier glyphs for the macOS hotkey hint (e.g. 'ctrl+alt+space' -> '⌃⌥Space').
_MAC_GLYPHS = {
    "ctrl": "⌃", "control": "⌃",
    "alt": "⌥", "option": "⌥", "opt": "⌥",
    "cmd": "⌘", "command": "⌘", "super": "⌘", "win": "⌘",
    "shift": "⇧",
}

_GENERIC_MODS = {"ctrl", "alt", "shift", "cmd"}


def _split_side(p: str):
    """('rcmd') -> ('R', 'cmd'); ('space') -> ('', 'space')."""
    if len(p) > 1 and p[0] in ("l", "r") and p[1:] in _GENERIC_MODS:
        return p[0].upper(), p[1:]
    return "", p


def format_hotkey(spec: str) -> str:
    """Render a normalized hotkey spec for humans.

    On macOS uses the ⌃⌥⇧⌘ glyphs ('ctrl+alt+space' -> '⌃⌥Space'); elsewhere a
    readable '+'-joined form ('Ctrl+Alt+Space'). Side-specific modifiers carry an
    L/R prefix ('rcmd+space' -> 'R⌘Space' / 'RCmd+Space').
    """
    parts = [p for p in spec.split("+") if p]

    def _key(p: str) -> str:
        return "Space" if p == "space" else (p.upper() if len(p) == 1 else p.capitalize())

    if sys.platform == "darwin":
        out = []
        for p in (p.lower() for p in parts):
            side, base = _split_side(p)
            out.append(side + _MAC_GLYPHS.get(base, _key(base)))
        return "".join(out)
    out = []
    for p in (p.lower() for p in parts):
        side, base = _split_side(p)
        out.append(side + _key(base))
    return "+".join(out)


def handle_activation(controller, recorder, put, chimes=None) -> None:
    """One hotkey activation (press-to-toggle). `put` is queue.put (thread-safe)."""
    if controller.state is State.IDLE:
        err = recorder.start()                 # Optional[RecorderError]; never raises
        if err is not None:
            put({"t": "terminal", "kind": Terminal.ERROR, "code": ErrorCode.MIC})
            return
        if chimes is not None:
            chimes.play_start()                # the mic is live — sound the "on" chime
        controller.toggle()                    # IDLE -> RECORDING (only after the mic is live)
    elif controller.state is State.RECORDING:  # RECORDING -> STOP (must NOT call toggle())
        audio = recorder.stop()
        controller.feed_audio_result(audio)
    # else (TRANSCRIBING / INSERTING / CANCELLING): ignore the press — re-stopping
    # would re-feed the recorder and paste the same transcript twice.


def run_start_loop(data_dir, state_writer=None, record_error=None) -> None:
    """Wire the panel (main thread) + hotkey listener + controller (real bundle) + recorder.

    Reuses the M2 inverted run-structure: Tk mainloop owns the main thread; a
    daemon worker loads the engine; the pynput hotkey listener thread is the only
    caller of handle_activation; all three communicate via queue.put only (never
    touching Tk). PanelRunner.run() blocks on the main thread until a {"t":"quit"}
    sentinel or the window is closed.

    GUI/manual-only path — verified by the G-paste/G-autostart manual gates.
    No unit test opens a Tk window for this function.
    """
    # Lazy imports: Tk and pynput must NOT be imported at module level.
    import queue
    import threading

    # Tk MUST be imported inside this function (never at module top) to avoid
    # contaminating headless test environments and to honour the AppKit-after-Tk
    # rule (M2): pyobjc is imported lazily inside the adapter functions.
    import tkinter as tk

    import yohoho.core.ui  # noqa: F401 — applies the Tcl env shim on import
    from yohoho.core.ui.main_thread import MainThreadExecutor, marshal_bundle
    from yohoho.core.ui.panel import StatusPanel
    from yohoho.core.ui.panel_model import PanelModel, level_from_raw
    from yohoho.core.ui.runner import PanelRunner

    from yohoho.core.config import load_config
    from yohoho.core.controller import Controller
    from yohoho.core.history import HistoryStore
    from yohoho.core.recorder import Recorder
    from yohoho.core.platform_factory import get_platform

    from pathlib import Path
    data_dir = Path(data_dir)

    cfg = load_config(data_dir / "config.yaml")
    resolved_device = cfg.audio["device_index"]
    hotkey_spec = cfg.hotkey

    # ------------------------------------------------------------------ Tk root
    root = tk.Tk()
    root.withdraw()
    model = PanelModel(columns=44, rows=7)
    plat = get_platform()                                          # one lookup, reused below
    panel = StatusPanel(root, model, window_chrome=plat.window_chrome)
    q: "queue.Queue[dict]" = queue.Queue()

    # ------------------------------------------------------------------ worker
    # Engine load AND the controller/recorder/hotkey wiring happen on a daemon
    # thread so the panel is immediately responsive (mirrors the run_dictate
    # worker, M2).  The WHOLE body is guarded: any failure surfaces an ERROR
    # terminal rather than leaving the loop running with no listener.
    # The transcribe worker calls native side effects (clipboard / paste / focus)
    # off the main thread; on macOS, posting a Quartz key event while the panel is
    # rendering aborts the process (SIGTRAP). marshal_bundle routes those calls
    # through the executor, which the runner's drain loop runs on THIS main thread.
    executor = MainThreadExecutor()
    bundle = marshal_bundle(plat, executor)                        # reuse plat (was get_platform())
    # On/off chimes (synth built once here; playback is non-blocking + best-effort).
    from yohoho.core.sounds import ChimePlayer
    chimes = ChimePlayer(enabled=cfg.sounds["enabled"], volume=cfg.sounds["volume"])
    # Shared so the MAIN thread can cleanly stop the listener after the runner
    # exits (the listener is started here, on the worker thread).
    state: dict = {"hotkey": None}
    hk_display = format_hotkey(hotkey_spec)

    def _worker() -> None:
        """Load the engine, wire controller + recorder + hotkey, arm the listener.

        ONLY communicates via q.put — never touches Tk.
        """
        try:
            from yohoho.core.cli import _make_engine
            if state_writer is not None:
                state_writer.set("loading")
            engine = _make_engine(data_dir)
            engine.load()
            warmup = getattr(engine, "warmup", None)
            if callable(warmup):
                warmup()
            if state_writer is not None:
                state_writer.set("idle")
            hist = HistoryStore(
                data_dir,
                enabled=cfg.history["enabled"],
                max_entries=cfg.history["max_entries"],
                max_age_days=cfg.history["max_age_days"],
            )
            def _on_terminal(e) -> None:
                q.put({"t": "terminal", "kind": e.kind, "code": e.code})
                if e.kind is Terminal.DONE:
                    chimes.play_end()  # dictation finished (text inserted) — "off" chime
                elif e.kind is Terminal.ERROR and record_error is not None:
                    try:
                        code_str = e.code.value if e.code is not None else "UNKNOWN"
                        record_error(code_str, f"terminal error: {code_str}")
                    except Exception:  # noqa: BLE001
                        pass  # observability is best-effort; never break the flow

            def _on_status(s) -> None:
                q.put({"t": "state", "state": s})  # panel first — always safe
                if state_writer is not None:
                    try:
                        state_writer.set(s)
                    except OSError:
                        pass  # status file is observability-only; never break dictation

            controller = Controller(
                engine=engine,
                bundle=bundle,
                history=hist,
                on_terminal=_on_terminal,
                on_status=_on_status,
            )
            recorder = Recorder(
                device_index=resolved_device,
                on_amplitude=lambda raw: q.put({"t": "amp", "level": level_from_raw(raw)}),
            )
            hotkey = bundle.hotkeys
            hotkey.configure(
                hotkey_spec,
                on_activate=lambda: handle_activation(controller, recorder, q.put, chimes),
            )
            hotkey.start()
            state["hotkey"] = hotkey  # expose to the main thread for clean teardown
            # The model is loaded and the listener is armed — tell the terminal so
            # the silence after the noisy CoreML load isn't mistaken for a crash.
            print(
                f"yohoho is ready. Press {hk_display} to start/stop dictation  ·  Ctrl-C to quit.",
                flush=True,
            )
        except Exception as exc:
            # NOTE: on a worker (engine-load) failure no listener is armed.  The panel
            # shows ERROR, hides, and the runner stays alive.  Ctrl+C (the SIGINT poller)
            # is the only exit at this point; a supervisor-level retry is deferred to M4.
            print(
                "yohoho: startup failed — dictation is unavailable. "
                "Press Ctrl-C to quit, then run `yohoho setup` to check the model and permissions.",
                file=sys.stderr,
                flush=True,
            )
            q.put({"t": "terminal", "kind": Terminal.ERROR, "code": ErrorCode.MODEL})
            # Flip state to "error" so `yohoho status` doesn't stay stuck at "loading".
            if state_writer is not None:
                try:
                    state_writer.set("error")
                except OSError:
                    pass
            # Persist the last error so `yohoho status` can surface it later.
            if record_error is not None:
                try:
                    record_error("model", f"startup failed: {exc}")
                except Exception:  # noqa: BLE001
                    pass  # observability is best-effort; never break the flow

    # The start loop is PERSISTENT: after each dictation the panel hides and the
    # listener stays armed for the NEXT hotkey press.  on_done is left at its
    # default (None) so a terminal does NOT stop the runner.
    # Exit paths:
    #   Ctrl+C (SIGINT) — ends cleanly: the `finally` block runs hk.stop().
    #   `yohoho stop` — writes data_dir/stop; the poll loop detects and removes
    #     it, stops the runner cleanly, and the `finally` block runs hk.stop().
    #   launchd bootout (SIGTERM) — now handled gracefully: the signal handler
    #     flips the stop flag; the poll loop stops the runner; `finally` runs.
    stop_sentinel = data_dir / "stop"
    stop_sentinel.unlink(missing_ok=True)  # clear any stale sentinel from a prior run
    runner = PanelRunner(root, panel, model, q, executor=executor,
                         window_chrome=plat.window_chrome,
                         stop_sentinel=stop_sentinel)

    # Main-thread platform prep BEFORE the worker arms the listener: on macOS the
    # pynput listener thread calls a main-thread-only keyboard API, which SIGTRAPs
    # while the panel draws — the platform pre-warms it here, on THIS main thread.
    # Optional hook; the null platform (and others) simply don't define it.
    prepare = getattr(bundle.hotkeys, "prepare", None)
    if callable(prepare):
        prepare()

    print("yohoho — loading the speech model…  (the first run can take a moment)", flush=True)
    threading.Thread(target=_worker, daemon=True).start()
    try:
        runner.run()  # blocks the main thread until the runner stops (SIGINT / quit)
    finally:
        hk = state.get("hotkey")
        if hk is not None:
            hk.stop()  # clean listener teardown on exit
