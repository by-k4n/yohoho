# yohoho

> **speak. it types.** — free, fully-local voice dictation for developers.

`yohoho` turns speech into text entirely on your machine. Hit a hotkey, talk, and an on-device model
(NVIDIA Parakeet) transcribes your speech and pastes the text into whatever app is focused. No cloud,
no API key, no subscription — your voice never leaves your laptop.

It's a free, open-source alternative to Wispr Flow and VoiceInk, for people who'd rather own their
tools than rent them. (The name is Brook's laugh from *One Piece* crossed with the "yo ho ho" shanty
— a laugh is a voice, after all.)

## Status

**Working on macOS today.** Press the hotkey, speak, press again — your words transcribe on-device and
paste at the cursor, with a live dot-matrix panel and on/off chimes. Windows and a one-line installer
are next.

| | |
|---|---|
| ✅ Working (macOS / Apple Silicon) | global hotkey, on-device transcription (Parakeet int8), live dot-matrix status panel, auto-paste, on/off chimes, run-on-login |
| 🚧 Next | smoother permission setup, background-daemon supervisor, Windows adapter |

## Install & set up (macOS)

Install with whichever you have:

```bash
npm i -g @by-k4n/yohoho    # Node users — bootstraps Python via uv under the hood
uv tool install yohoho     # uv users
pipx install yohoho        # pipx users
```

Bleeding edge / no PyPI: `uv tool install 'git+https://github.com/by-k4n/yohoho.git@vX.Y.Z'`.

Then:

```bash
yohoho setup     # pick a hotkey, grant permissions, download the model (~660 MB, first run)
yohoho start     # press your hotkey anywhere to dictate
```

`setup` walks you through it, opens the right System Settings panes, and installs a launch-on-login
agent so yohoho is ready whenever you are; the default hotkey is **⌃⌥Space** (Control-Option-Space).
`start` runs the dictation loop in the foreground now (Ctrl-C to quit).

**To dictate:** press **⌃⌥Space** (you'll hear the "on" chime), speak, then press **⌃⌥Space** again —
the text transcribes on-device and pastes at your cursor (the "off" chime confirms it). Run
`yohoho doctor` any time to check permissions and your hotkey.

## Permissions (macOS) — please read

macOS gates the hotkey and the paste behind three privacy permissions. **Grant them to the terminal
app you launch yohoho from** — Terminal, iTerm, Warp, Ghostty, … — *not* to "python":

| Permission | Why it's needed | System Settings ▸ Privacy & Security ▸ |
|---|---|---|
| **Microphone** | record your voice | Microphone |
| **Input Monitoring** | detect the global hotkey | Input Monitoring |
| **Accessibility** | paste into the focused app | Accessibility |

> **Why your terminal, not python?** macOS attributes these grants to the *responsible process* — the
> app that launched yohoho — which is your terminal, not the Python interpreter. `yohoho setup` opens
> the right panes; add your terminal app under each one and toggle it on. If you later launch from a
> *different* terminal, grant it there too.

**Known rough edge:** if dictation transcribes but doesn't paste (you have to press ⌘V yourself), your
terminal is missing **Accessibility** — add it there and restart the terminal. This terminal-by-terminal
grant is the price of shipping as a dev script today; a future version will ship a small signed app so
you grant once and forget it. For now, that's a known trade-off we've chosen on purpose.

## Why

- **Private** — audio is transcribed locally and never touches a server. Transcripts are never written
  to logs, and history stays on your machine.
- **Fast** — Parakeet runs several times faster than realtime on CPU; on Apple Silicon it offloads to
  the Neural Engine via CoreML. Text lands in ~1–2 s for a short clip.
- **Free** — MIT licensed. No subscription, ever.

## Architecture

A portable **core** (identical on every OS) sits behind six small platform-adapter contracts —
hotkey · clipboard · inject · focus · autostart · permissions — the only OS-specific code, selected at
runtime by `platform_factory`. Engine: NVIDIA Parakeet TDT 0.6b v2 (int8 ONNX) via `onnx-asr`. UI: a
Tkinter dot-matrix panel. Output: clipboard paste (lossless, unlike per-key typing).

The full design is in [`docs/DESIGN.md`](docs/DESIGN.md); the 149-case failure-mode matrix is in
[`docs/edge-cases.md`](docs/edge-cases.md); deferred review follow-ups are in
[`docs/m4-followups.md`](docs/m4-followups.md).

## Roadmap

- [x] **M1** — portable core (engine, recorder, controller, config, observability, history) + `yohoho dictate`
- [x] **M2** — dot-matrix status panel (Tkinter)
- [x] **M3** — macOS adapter: global hotkey, TCC permissions, auto-paste, on/off chimes, run-on-login
- [x] **M4 (install)** — PyPI + npm wrapper install (this ship); daemon/signed-app/tray are later M4 pieces
- [ ] **M4** — background-daemon supervisor, smoother permission flow (signed app), full `status`/`history`/`logs`
- [ ] **M5** — Windows adapter
- [ ] **M6** — standalone per-OS binaries

Linux is on the map but deferred from v1; the adapter layer is kept Linux-ready.

## Development

```bash
uv sync --extra dev
uv run pytest                       # unit suite
uv run pytest -m "gui or not gui"   # include the Tk panel tests
uv run pytest -m integration        # real-model test (needs the model cached + tests/fixtures/hello.wav)
uv run ruff check .
```

## Design

Terminal / dot-matrix aesthetic — brand color `#39BFC6` on near-black,
[Doto](https://fonts.google.com/specimen/Doto) wordmark, everything rendered in dots.

## License

MIT — see [LICENSE](LICENSE). If it saves you a subscription, buy yourself a coffee.
