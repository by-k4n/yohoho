# yohoho — cross-platform design spec

> **Status:** approved design, pre-implementation. **Scope of this doc:** the cross-platform
> package architecture, the platform-adapter contracts, the runtime/resilience design, and the
> distribution chain. Product/brand context lives in `CLAUDE.md`; the full decision history is in
> `docs/HANDOFF.md`; the exhaustive failure-mode list is in `docs/edge-cases.md`.
> _Authored 2026-06-24._

---

## 1. What we're building

`yohoho` is a free, open-source, **fully-local** voice dictation tool. A user-chosen global hotkey
toggles recording; speech is transcribed **on-device** (NVIDIA Parakeet TDT 0.6b v2 via `onnx-asr`,
int8 ONNX); the resulting text is inserted into the focused app via the clipboard. No cloud, no API
key, no subscription. It runs as a small background daemon with a minimal always-on-top "dot-matrix"
status panel.

**Hero tagline:** `speak. it types.` (secondary copy: *your voice, on-device.* / *dictation, no
subscription.*)

### Goals
- One portable **core** that is byte-for-byte identical on every OS; all OS-specific code lives behind
  five small adapter contracts.
- Reuse the proven behavior of the Windows reference build (patched WhisperWriter): pynput listener,
  clipboard-paste output, the dot-matrix panel.
- Trustworthy in daily use: never paste into the wrong app, never silently fail, never leak the
  user's speech into logs.

### Non-goals (v1)
- Linux support (designed-for but **deferred** — see §17).
- Cloud/API transcription, streaming/partial results, multi-language models, custom vocabulary.
- A settings GUI beyond the CLI + an optional tray menu.

---

## 2. Platform support matrix

| OS | v1 | Notes |
|----|----|-------|
| Windows 10/11 | ✅ ships | Validated against the known-good reference build. No special permissions. |
| macOS 12+ | ✅ ships | Requires two TCC grants (Input Monitoring + Accessibility) configured during `setup`. The real work is here. |
| Linux X11 | ⏸ deferred | Interface kept Linux-ready; impl in a later milestone. |
| Linux Wayland | ⏸ deferred | Best-effort path documented in §17 (GlobalShortcuts portal + `ydotool`). |

---

## 3. Architecture

```
yohoho/
├─ core/                      # portable — identical on every OS, never imports platform.*
│  ├─ platform_api.py         # the 5 Protocol contracts + PlatformBundle + permission types
│  ├─ platform_factory.py     # get_platform() — the ONLY core module that imports platform.*
│  ├─ controller.py           # state machine, generation-id, cancel, side-effect gating
│  ├─ recorder.py             # sounddevice capture (per-session stream), RMS amplitude, VAD, resample
│  ├─ engine.py               # Engine interface + onnx-asr Parakeet impl (resident model, watchdog)
│  ├─ injector_logic.py       # clipboard critical-section + focus-token policy (OS-agnostic part)
│  ├─ history.py              # outcome-gated JSONL store + recovery bucket
│  ├─ observability.py        # logging setup, crash net, TranscriptText wrapper, last_error.json
│  ├─ ui/                     # Tkinter dot-matrix panel (single Tk owner, queue+after pump)
│  ├─ config.py               # load/validate/migrate config
│  └─ cli.py                  # setup | start | stop | status | history | logs | doctor | config
└─ platform/                  # thin adapters — the ONLY OS-specific code
   ├─ windows.py
   ├─ macos.py
   └─ linux_*.py              # deferred
```

**Dependency rule (enforced):** `core` depends only on the Protocols in `platform_api.py`.
`platform_factory.get_platform()` is the single seam allowed to import the `platform` package.
`cli`/`main` builds the `PlatformBundle` once and injects it into the controller by composition —
no other core module ever calls `get_platform()` or imports an OS module.

---

## 4. Platform-adapter contracts

Five `typing.Protocol` interfaces (runtime-checkable). Adapter conformance is verified by a real
per-adapter test, **not** `isinstance` (runtime_checkable only checks method *names*, not signatures).

```python
# core/platform_api.py
from __future__ import annotations
from typing import Protocol, runtime_checkable, Callable, Literal, Optional
from dataclasses import dataclass

# Normalized OS-agnostic hotkey, stored in config: lowercase, '+'-joined, modifiers first.
HotkeySpec = str                       # e.g. 'ctrl+alt+space', 'f14'
ActivateCallback = Callable[[], None]

@runtime_checkable
class HotkeyListener(Protocol):
    def configure(self, spec: HotkeySpec, on_activate: ActivateCallback,
                  on_cancel: Optional[ActivateCallback] = None) -> None: ...
    def start(self) -> None: ...       # listens on its own thread; non-blocking
    def stop(self) -> None: ...
    def is_alive(self) -> bool: ...     # for the supervisor heartbeat (P7)
    @staticmethod
    def is_valid_spec(spec: HotkeySpec) -> bool: ...   # parse-check during `setup`

@runtime_checkable
class Clipboard(Protocol):
    def get_text(self) -> Optional[str]: ...           # None if empty/non-text
    def set_text(self, text: str) -> None: ...
    def has_nontext(self) -> bool: ...                 # informs the no-clobber policy

@runtime_checkable
class TextInjector(Protocol):
    # Clipboard-paste strategy; returns whether the paste chord was synthesized.
    # Honors the no-restore-by-default policy; restore (if enabled) is the caller's job.
    def paste(self) -> bool: ...                       # synthesize Cmd/Ctrl+V into focused app
    def release_modifiers(self) -> None: ...           # defensive: clear stuck modifiers

@runtime_checkable
class FocusProbe(Protocol):
    # One snapshot at record-stop, reused for BOTH paste verification and panel placement.
    def snapshot(self) -> "FocusToken": ...
    def unchanged(self, token: "FocusToken") -> bool: ...

@runtime_checkable
class AutostartManager(Protocol):
    def enable(self) -> None: ...      # idempotent
    def disable(self) -> None: ...     # idempotent
    def is_enabled(self) -> bool: ...

PermState = Literal['granted', 'denied', 'not_applicable', 'unknown']

@dataclass(frozen=True)
class Permission:
    key: str            # 'accessibility' | 'input_monitoring'
    state: PermState
    label: str
    fix_hint: str
    deep_link: str = '' # e.g. an x-apple.systempreferences: URL

@dataclass(frozen=True)
class PermissionStatus:
    ok: bool                              # True iff every required perm is granted/not_applicable
    permissions: tuple[Permission, ...]
    identity_ok: bool = True              # macOS: granted interpreter path == current (P7)

@runtime_checkable
class PermissionsManager(Protocol):
    def check(self) -> PermissionStatus: ...   # never prompts; pure read (live probe)
    def request(self) -> None: ...             # may open OS prompt / deep-link to Settings
    def guide(self) -> str: ...                # multiline instructions for `setup`

@dataclass(frozen=True)
class PlatformBundle:
    name: str                                  # 'windows' | 'macos'
    hotkeys: HotkeyListener
    clipboard: Clipboard
    injector: TextInjector
    focus: FocusProbe
    autostart: AutostartManager
    permissions: PermissionsManager
```

`FocusToken` is an opaque per-OS value carrying app-id + window handle + display-id + the session
**generation-id** + a "no sleep boundary crossed and permissions still granted" assertion (§10, P5/P7).

---

## 5. Controller state machine

```
idle → STARTING → recording → transcribing → inserting → (done | error)
                     │            │              │
                     └────────────┴──────────────┴──────► CANCELLING → idle
```

- `STARTING` covers the async window while the audio stream opens (so a cancel mid-open is handled).
- `CANCELLING` is reachable from STARTING/recording/transcribing/inserting.
- Every transition is serialized through one thread-safe controller queue guarded by the state
  machine; illegal transitions (e.g. a toggle that doesn't match current state) are ignored. The
  toggle is debounced ~250–300 ms to swallow key-repeat/bounce.
- **Terminal events are typed:** `done` (success) vs `error` vs `cancelled`. Only `done` drives the
  100% finish in the UI (§13). This corrects HANDOFF §5, where "fill to 100% on idle" faked success
  on every failure path.

The controller is the single owner of the **generation-id** (P1) and gates every side effect on it.

---

## 6. Hotkey model

- Config stores an OS-agnostic `HotkeySpec` string. Each adapter parses it into an **internal,
  OS-agnostic key enum + chord matcher** driven by `pynput.keyboard.Listener(on_press/on_release)` —
  the approach proven in the reference build (`InputBackend` / `KeyChord` / `parse_key_combination`),
  **not** pynput's higher-level `GlobalHotKeys`. Both pynput and (later) evdev keys translate *into*
  the internal enum, so the matcher is identical across OSes and supports left/right modifier
  variants and key-release events (needed for future hold-to-talk / VAD).
- v1 recording mode is **press-to-toggle** (uses `on_activate` only). `Esc` (and a configurable
  second channel) drives `on_cancel`.
- **Suggested default hotkey: `Ctrl+Alt+Space`** (Alt = Option on macOS). Avoids Spotlight
  (`Cmd+Space`), the macOS input-source switch (`Ctrl+Space`), and dictation (Globe/Fn). `setup`
  validates the chosen spec is bindable before saving.
- **F14** is offered only as a Windows power-user option (paired with the optional AHK `Win+H→F14`
  remap); it is **not** the cross-platform default because pynput cannot listen for F13–F20 on
  Linux/X11 (relevant when Linux lands).

---

## 7. Recorder

- **Per-recording stream (P8):** open a `sounddevice` stream bound to an **explicit device index**
  at record-start, close it in a `finally` at record-stop. No resident stream — this fixes
  mic-held-blocking-other-apps, stale-stream-after-sleep, device-switched, and unplugged-mid-record.
- Emits three signals to the controller/UI: `status` (recording/transcribing/idle/error),
  `amplitude` (normalized RMS per block → waveform), `duration` (seconds → progress estimate).
- **Sample-rate correctness:** built-in and Bluetooth mics are frequently 44.1/48 kHz-only. The
  recorder resamples explicitly to 16 kHz mono float32 at the recorder→engine seam. A wrong/skipped
  resample produces confident garbage that passes every RMS/silence guard, so this is mandatory.
- Optional `voice_activity_detection` mode (`webrtcvad`) auto-stops on silence; in that mode the
  **controller** owns the `Esc`/cancel-listener lifetime (the machine, not the user, drives stop).
- Errors (PortAudio device errors) are caught: stay in `idle`, flash a `MIC` error state, keep the
  listener alive, log device enumeration + error.

---

## 8. Engine

- `Engine` is a small interface (`load()`, `recognize(audio16k) -> str`, `unload()`). The v1 impl
  loads `nemo-parakeet-tdt-0.6b-v2` int8 via `onnx_asr.load_model(name, quantization='int8')` and
  keeps the model **resident** in the daemon. `recognize()` runs on a worker thread; the model is
  used by a single serialized worker so concurrent `recognize()` calls can't contend.
- `recognize()` is a single blocking call with no cancellation token. We **never** kill the worker
  thread. Cancel is cooperative (P1): the result is dropped if the generation-id moved (§9).
- A **monotonic-clock watchdog** ceilings transcribe time (`max(8s, duration × k)`); on breach the
  controller forces an `error` (`TIMEOUT`) terminal state. The worker thread is marked
  `daemon=True` so a wedged `recognize()` can never hang `stop`.
- **Subprocess isolation is deferred** (per decision): the `Engine` boundary is kept clean so a
  killable subprocess engine — which would let cancel/quit free CPU mid-`recognize()` and would
  isolate OOM/native crashes — can drop in later without a controller rewrite. v1 mitigates with
  short clips + the discard token + the watchdog.
- **Warmup:** the cold-start warmup `recognize()` on a silent buffer runs **outside** the
  controller pipeline — it must never touch the clipboard, panel, history, or insert path.
- Load failures (missing/corrupt model, onnxruntime native init error, missing int8 files) surface
  as a `MODEL` error state with a `status`/`doctor` explanation, not a crash.

---

## 9. Resilience — the nine load-bearing primitives

The 149-case sweep (`docs/edge-cases.md`) collapses onto nine primitives; building these closes
~90% of cases.

1. **P1 — generation-id + cooperative discard token.** A per-session monotonic id stamped at
   record-start; the controller holds `current_gen`. Checked at **every** side-effect site
   (enter-inserting, save-clipboard, synth-paste, write-history, UI terminal event). Cancel/new-
   session bump `current_gen`; an in-flight job whose `gen != current_gen` is dropped on return.
2. **P2 — one empty/silence/zero-length guard.** RMS floor pre-recognize + `result.strip()`
   post-recognize gate all side effects. No paste/history/flash on silence or empty/hallucinated text.
3. **P3 — clipboard critical section.** `save → set → paste → (restore)` as a single
   controller-owned `try/finally`, entered only with non-empty text after the abort check.
4. **P4 — no clipboard auto-restore by default.** Leave the transcript on the clipboard. This
   eliminates the restore-too-soon race (the most damaging clipboard bug), non-text clobber, and
   clipboard-history pollution in one move. A config flag `clipboard.restore_previous` (default
   `false`) re-enables restore-with-delay for users who want it.
5. **P5 — paste is best-effort; the transcript is never lost.** A **focus-token captured at
   record-stop** is verified before paste. On focus change / no editable field / macOS
   `EnableSecureEventInput` / Windows UIPI elevated target / Tahoe synthetic-event filtering, we
   **degrade to "left on clipboard + recorded in history"** (`outcome=copied`) and flash a `PASTE`
   notice — never paste into the wrong app.
6. **P6 — distinct error/aborted UI terminal states**, all marshaled through a single Tk
   `queue + after()` pump. No worker thread ever calls a Tk method (fixes cross-thread crashes and
   focus-steal).
7. **P7 — listener + permission supervisor.** A heartbeat detects a dead/disabled pynput listener
   and re-arms it; on macOS it live-probes the two TCC grants and checks the granted interpreter
   path still matches. A lost grant/listener becomes a **visible degraded state**, never "healthy
   but dead."
8. **P8 — per-recording audio stream** bound to an explicit device index (see §7).
9. **P9 — structural transcript-leak prevention.** All transcript text flows through a
   `TranscriptText` wrapper whose `__repr__`/`__str__` is `<transcript len=NN sha8=…>`; a
   `logging.Filter` scrubs every handler; a unit test asserts no emitted log line contains a fixture
   transcript. Real text only behind an off-by-default `--debug-transcripts` with a loud warning.

---

## 10. Cancel & clean shutdown

**Cancel (soft, cooperative — decision: no thread-kill in v1):**
- `Esc` cancels any live session (registered only while a session exists, in STARTING/recording/
  transcribing/inserting; torn down on a terminal state). A configurable second channel
  (panel-click or a cancel-hotkey) covers apps where `Esc` collides (vim/modals); precedence is moot
  because every channel just bumps `current_gen` (idempotent).
- Cancel: bump `current_gen` → flip UI to a muted `cancelled` state (not success, not 100%) and hide
  → stop/close the audio stream in `finally` → set the discard flag. On `recognize()` return the
  worker sees `gen != current_gen` and drops the result: no insert, no history (main timeline), no
  clipboard. Cancelled-after-recognize text goes to the **recovery bucket** (§11).
- Cancel arriving in the post-paste/pre-restore window is a no-op on the already-pasted text but
  still runs the `finally` restore (if enabled) + modifier release. Contract: cancel is guaranteed
  only up to the moment the paste chord is synthesized.

**Clean shutdown (`stop` / SIGTERM / SIGINT / tray-quit):** deterministic teardown bounded by a
timeout, `os._exit()` as last resort so `stop` never hangs:
1. transition to CANCELLING; 2. abort+close the audio stream; 3. stop the pynput listener;
4. set discard flag (and rely on `daemon=True` engine thread so a wedged `recognize()` can't block);
5. quit the Tk mainloop via its queue; 6. **release stuck modifiers** (defensive — a crash during
the paste chord otherwise leaves Cmd/Ctrl logically held system-wide); 7. release the single-instance
lock / PID file; 8. restore clipboard only if mid-paste and restore is enabled. A startup
modifier-clear runs defensively too.

---

## 11. History (default ON — local, capped, clearable)

- **Storage:** append-only `history.jsonl` in the **pinned-local** data dir (`%LOCALAPPDATA%`, *not*
  Roaming; `~/Library/Application Support/yohoho`). Created `0600` inside a `0700` dir. A startup
  check warns if the resolved path sits in a **cloud-sync** location (OneDrive/Dropbox/iCloud/reparse
  point).
- **Record:** `{v, id(uuid — not the volatile gen-id), ts(UTC ISO-8601), dur_s(monotonic), len,
  word_count, outcome, app_id?, text}`. `app_id` is a coarse bundle/exe basename, **opt-in, off by
  default**, never a window title.
- **Write policy** (gated on gen-id + P2): write only when the session is still current **and** text
  is non-empty **and** `outcome ∈ {pasted, copied}` (`copied` = best-effort paste failed, text left
  on clipboard — still the user's real dictation). Never write empty/silence/error/superseded.
- **Recovery bucket:** cancelled-after-recognize transcripts go to a separate
  `history-discarded.jsonl` (last ~20) so accidental cancels are recoverable without polluting "what
  I dictated."
- **Hot path:** the paste fires first; the history write happens after, on a bounded background
  writer queue (drop-with-warning if full — never block the paste). Each write is one `os.write` of a
  `< PIPE_BUF`, newline-terminated `json.dumps(ensure_ascii=False)` line in `O_APPEND` mode (atomic).
- **Reader:** parse line-by-line, skip+count malformed lines, tolerate a trailing partial line,
  default missing fields by schema version, sort by `ts` defensively.
- **Retention:** `min(1000 entries, 30 days)`, with an `unlimited` opt-in; compaction (write `.tmp` +
  atomic rename) on startup and every N appends, never on the hot path.
- **Opt-out:** a single `history.enabled` gate at the one write site. When off, nothing
  transcript-related touches disk.
- **CLI:** `yohoho history` (view), `history clear` (wipes both files; honestly scoped — does not
  claim to erase OS backups/snapshots). A full disk sets `last_error` and flashes once but the paste
  still happens. *(A "re-paste last transcript" utility is out of v1 scope; the history/recovery view
  covers recovery.)*

---

## 12. Error logging & observability — three layers

The dominant real-world failures are *silent* (lost permission, blocked paste). The transient panel
has already eased away by the time the user notices, so logging to disk alone is useless — the panel
must become an error channel and `status` must work after the fact.

- **L1 — durable file log.** `RotatingFileHandler` (1 MB × 5) at `<datadir>/logs/yohoho.log`, single
  owner (the locked daemon), INFO default, DEBUG behind `--verbose`. **Probe-write the data dir
  before configuring logging** (so "why did the daemon die" can always be recorded; on failure fall
  back to stderr + temp dir and exit loudly). Install the crash net **before detaching**:
  `sys.excepthook` + `threading.excepthook` (full tracebacks), `faulthandler.enable()` to a crash
  file (native onnxruntime/sounddevice `SIGSEGV/SIGABRT`), and redirect detached stdout/stderr to
  `startup.log` (import-time/native-loader failures). Write a **clean-shutdown marker** so the next
  `start` can report "recovered from a crash — see logs." Log all lifecycle/health/permission/
  stop-reason/timing metadata — **never content** (P9). Stop-reason is enumerated:
  `manual | vad-silence | max-duration | device-error | cancel | shutdown | sleep | timeout`.
- **L2 — the panel as a first-class error channel.** An `error` terminal state, marshaled through the
  same Tk pump (P6). On error the panel does **not** fill to 100%/green — it freezes the bar, shows a
  short code (`PERM | PASTE | MODEL | MIC | TIMEOUT`) in an on-brand amber (red `#ff5454` stays
  reserved for the REC dot), holds ~2–3 s, says "run `yohoho status`." For errors with no active
  panel (paste blocked after the panel hid), force-show the panel briefly.
- **L3 — CLI self-service.** Persist `last_error.json` so `yohoho status` works after the panel is
  gone. `status` shows: running/pid/uptime, model on-disk vs loaded, **live-checked Input Monitoring
  + Accessibility as two distinct rows on macOS**, granted-interpreter-path-matches-current, last N
  errors, data-dir path, history on/off + count + size, log path + size. `yohoho logs`
  (path/tail/`--follow` opened read-only-shared so it can't race rotation/`--open`). `yohoho doctor`
  produces a **scrubbed, transcript-free** shareable bundle (home→`~`, username→`<user>`, no window
  titles, no env dump) that states it is safe to share.

All elapsed/duration math uses `time.monotonic`; timestamps are UTC ISO-8601 and sorted defensively
so a clock step cannot wedge the watchdog or corrupt history ordering/retention.

---

## 13. UI — the dot-matrix panel

Port HANDOFF §5 verbatim to **Tkinter Canvas**, with these design-level rules layered on:

- **Single Tk owner.** Tk is created on the **main thread**; the audio and engine threads never touch
  Tk. All updates flow through one `queue + after()` pump (P6). The blocking `recognize()` and any
  paste/sleep never run on the Tk thread.
- **No focus stealing.** Frameless, always-on-top, **does-not-accept-focus**, bottom-center,
  ~296×80. It must never take keyboard focus from the app being dictated into.
- **Typed terminal states.** `done` eases to 100% then hides; `error` shows a code + holds ~2–3 s;
  `cancelled` shows a muted hide. A dropped/never-emitted terminal event can't strand the panel — a
  fallback timer hides it.
- **Display robustness.** Re-resolve target display from the record-stop focus snapshot; tolerate
  monitor unplug / resolution / DPI change while shown; if the Doto font fails to load, fall back
  gracefully (panel still functions). Multi-monitor placement reuses the same focus snapshot used for
  paste verification, so panel and paste can never disagree about "the target."
- **Waveform/progress** exactly as §5 (column-dot RMS waveform; transcribing eases toward 90% then
  `done` finishes to 100%).

---

## 14. macOS adapter

The only hard adapter. Two **distinct** TCC permissions (different panes; both fail *silently*):

| Need | Permission (TCC service) | Probe API |
|------|--------------------------|-----------|
| Hotkey listener (Quartz event tap) | Input Monitoring (`kTCCServiceListenEvent`) | `IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)` / `CGPreflightListenEventAccess()` |
| Synthetic `Cmd+V` paste | Accessibility (`kTCCServicePostEvent`, shown under Accessibility) | `AXIsProcessTrusted()` |

- **Setup flow:** `check()` each → if denied, deep-link the exact pane
  (`x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility` /
  `?Privacy_ListenEvent`) and **poll until granted** — on Sequoia (15) / Tahoe (26) the prompts are
  **non-modal** and may never appear on their own. `check()` returns a structured `PermissionStatus`
  so `setup`/`status` render a per-permission checklist.
- **Live revoke handling (P7):** the supervisor re-probes both grants; a mid-session revoke flips to a
  visible degraded state instead of silent death.
- **Code-identity / signing strategy:** TCC binds grants to a process's code identity (signature +
  path). With the npm→uv→python path the granted identity is the uv-managed interpreter
  (`~/.local/share/uv/python/cpython-3.11.x-macos-<arch>-none/bin/python3.11`), which changes when uv
  bumps Python → grant silently drops. **Tahoe (26)** additionally filters synthetic key events from
  unsigned binaries for hotkey/Carbon matchers (confirmed *not* to break delivering `Cmd+V` into a
  focused text field, so v1 paste still works — but it is the top macOS risk).
  - **Phase 1 (v1):** ship the bare uv CLI; **pin the uv Python version** to minimize path churn;
    call `responsibility_spawnattrs_setdisclaim` so the grant attaches to our interpreter rather than
    Terminal; `status` checks granted-path-matches-current and tells the user to re-grant after a uv
    Python bump. No Apple Developer account required.
  - **Phase 2:** ship a codesigned **Developer ID `.app`** with a stable bundle id (`pro.bykc.yohoho`)
    hosting the Python runtime → durable grants, immune to the Tahoe filter. Requires an Apple
    Developer account ($99/yr) + notarization.
- **Other:** native `NSPasteboard` (pyobjc) for clipboard get/set (faster than pbcopy/pbpaste
  subprocess; cleaner save/restore). Autostart via `~/Library/LaunchAgents/pro.bykc.yohoho.plist`
  loaded with `launchctl bootstrap gui/$(id -u)` (not the deprecated `launchctl load`); `bootout` to
  remove. App must **not** be sandboxed.

---

## 15. Windows adapter

Generalized from the reference build; no permissions needed.

- **Hotkey:** pynput `Listener` + the internal chord matcher for the user-chosen hotkey (its
  `WH_KEYBOARD_LL` hook needs no admin). **AutoHotkey is dropped** from the default path; it ships
  only as an opt-in for users who insist on the OS-reserved `Win+H`, and the remap uses **AHK v2**
  syntax (`#h::F14`, not v1 `Send {F14}`).
- **Inject:** clipboard + `Ctrl+V` via pynput `Controller` (per-character typing is forbidden — it
  dropped spaces in the reference). Layout-robust `KeyCode.from_vk(0x56)` for `V`.
- **Clipboard:** `pyperclip` (ctypes/Win32, no pywin32). Pin with `pyperclip.set_clipboard('windows')`
  (the valid string is `'windows'`, **not** `'windll'`).
- **Autostart:** `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` via stdlib `winreg`, launched
  with `pythonw.exe` (no console window). Preferred over a Startup `.lnk` (OneDrive-redirected Startup
  folders and `WScript.Shell` brittleness).
- **Permissions:** none — `PermissionsManager` returns all `not_applicable`. Documented hard limit:
  a normal-integrity process cannot paste into an elevated/admin foreground window (UIPI) → degrades
  to clipboard (P5). The onnxruntime-before-Qt DLL gotcha is **moot** under Tkinter.

---

## 16. Distribution & bootstrap (Phase 1)

npm's role is deliberately tiny: the package ships **only a JS bin shim** — no Python, no model.
**There is no npm package for `uv`** (the registry name 404s), so the shim shells out to the official
installer.

- `package.json`: `"bin": {"yohoho": "bin/yohoho.js"}`, `"engines": {"node": ">=18"}`, minimal
  `"files"` (KB-sized tarball). Postinstall is absent or **crash-proof + advisory** (`node
  scripts/postinstall.js || true`, wrapped in try/catch, never `exit(non-zero)`) so it survives
  `--ignore-scripts`, `EACCES`, and offline/CI installs.
- **First run / `setup`** (network required, clearly messaged): ensure `uv` (curl
  `https://astral.sh/uv/install.sh` / PowerShell `irm …/install.ps1`, **pinned version** via the
  versioned URL) → `uv python install 3.11` → `uv tool install --python 3.11 <pypi-name>` (the dist
  **must** declare `[project.scripts] yohoho = …`) → **lazily** download the int8 Parakeet model
  (~660 MB) with the dot-panel progress UI. **Never** in postinstall. Pin `--python 3.11` explicitly
  (ML wheels lack cp312+ wheels and would compile from source; `uv tool install` ignores
  `.python-version`).
- **Everything in per-user dirs**, relocated under the yohoho data dir via `UV_TOOL_DIR` /
  `UV_TOOL_BIN_DIR` / `UV_PYTHON_INSTALL_DIR` / `UV_CACHE_DIR` + `HF_HOME` (so `status`/uninstall find
  and remove it cleanly; note `UV_INSTALL_DIR` relocates only the uv *binary*, not its data dirs).
  Force `quantization='int8'` or you'd pull the 2.44 GB fp32 weights.
- **Model-download vs daemon-start race:** one owner + a lock on the shared HF cache /
  `model_ready` marker; `start` loads with `HF_HUB_OFFLINE=1` so it never silently triggers a 660 MB
  download. `setup`-download and `start` must not run concurrently against the cache.
- **Shim resolution:** resolve absolute binary paths (don't rely on PATH — `~/.local/bin` may be
  absent, especially under detached login-autostart). On Windows spawn the resolved `.exe` with
  `shell: false`.
- **Update:** `npm update -g` refreshes the shim, which records its expected Python-package version
  and runs `uv tool upgrade` on mismatch. **Uninstall:** a documented teardown runs
  `uv tool uninstall` and removes the data dir.

---

## 17. Run model, CLI & config

- **Run model:** a **detached background daemon** + an **optional minimal tray** (status / settings /
  quit via `pystray`; the tray loop and Tk loop coexist with one Tk main-thread owner). The dot-panel
  appears only during dictation. A **held single-instance lock / PID file** is the load-bearing
  primitive that prevents double-start (two listeners + double paste + interleaved history). `start`
  detects a stale PID (dead/reused) and recovers; `stop`/`status` handle "not running" cleanly;
  autostart waits for the GUI/session/TCC to be ready.
- **CLI surface:**

  | Command | Purpose |
  |---------|---------|
  | `yohoho setup` | choose hotkey, grant permissions (macOS), download model, enable autostart |
  | `yohoho start` / `stop` | spawn / kill the detached daemon (via the recorded PID) |
  | `yohoho status` | daemon + permissions + model + history + log health; last N errors |
  | `yohoho history [clear]` | view / wipe local transcript history |
  | `yohoho logs [--follow|--open]` | tail / open the rotating log (read-only-shared) |
  | `yohoho doctor` | scrubbed, transcript-free diagnostics bundle |
  | `yohoho config` | open the config file |

- **Config** (YAML in the data dir; validated + version-migrated on load):

  ```yaml
  model: nemo-parakeet-tdt-0.6b-v2
  device: cpu
  compute_type: int8            # onnx-asr quantization
  language: en
  hotkey: ctrl+alt+space        # normalized HotkeySpec
  cancel_channel: esc           # esc | panel-click | <hotkey spec>
  recording_mode: press_to_toggle   # | voice_activity_detection
  input_method: clipboard
  clipboard:
    restore_previous: false     # P4 default: leave transcript on clipboard
    restore_delay_ms: 150       # only used when restore_previous is true (conservative default)
  history:
    enabled: true
    capture_app_id: false       # coarse app id only, opt-in
    max_entries: 1000
    max_age_days: 30
  audio:
    device_index: null          # null = system default; set explicitly to pin
  log_level: info
  ```

- **Data/file layout** (under the pinned-local data dir):
  ```
  config.yaml  history.jsonl  history-discarded.jsonl  last_error.json
  model_ready  yohoho.pid  logs/{yohoho.log, startup.log, crash.log}
  hf/                       # HF_HOME (the ~660 MB model cache)
  ```

---

## 18. Brand (carried forward, locked)

`#39BFC6` cyan on near-pure black; red `#ff5454` **only** for the REC dot; error states use on-brand
amber. Wordmark font **Doto** (dot-matrix). "Everything is dots" — wordmark, waveform, and progress
share one dot vocabulary on a faint off-LED grid. Tagline `speak. it types.`

---

## 19. Deferred — Linux (designed-for, not in v1)

Captured so a future impl drops behind the same five contracts:
- **Session detection:** prefer `WAYLAND_DISPLAY` / `XDG_SESSION_TYPE` over `DISPLAY` (both are set
  under Xwayland).
- **X11:** pynput/python-xlib hotkey; `Ctrl+V` via pynput; clipboard via xclip/xsel (pyperclip).
  Fully solved.
- **Wayland (best-effort):** global hotkey via the XDG **GlobalShortcuts** portal
  (`org.freedesktop.portal.GlobalShortcuts`: `CreateSession`→`BindShortcuts`→`Activated`) — works on
  GNOME 48+/KDE/Hyprland, not wlroots/Sway. Paste via `wl-copy` + **`ydotool`** (`ydotoold` + a
  `/dev/uinput` udev rule + `input` group; raw keycode syntax `29:1 47:1 47:0 29:0` on ydotool ≥1.0,
  key-name syntax on 0.1.x). Clipboard via `wl-clipboard`. Autostart via XDG
  `~/.config/autostart/yohoho.desktop` (inherits session env) over systemd-user.

---

## 20. Open items (track during implementation)

- Final PyPI dist name + confirming the `yohoho` console-scripts entry point.
- Whether to vendor a pinned `uv` vs always curl the pinned-version installer (reproducibility vs
  tarball size).
- macOS bundle id / LaunchAgent Label string (`pro.bykc.yohoho` proposed) — confirm.
- Whether `npm update -g` auto-runs `uv tool upgrade` silently vs prompts.
- Apple Developer account availability (would let Phase-2 signing land earlier).

---

## References
- `CLAUDE.md` — product, brand, stack, conventions.
- `docs/HANDOFF.md` — full decision history; §5 = the panel visual spec (amended here re success vs
  error); §6 = the working Windows reference build.
- `docs/edge-cases.md` — the full 149-case resilience matrix mapped to the nine primitives.
