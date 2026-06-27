# @by-k4n/yohoho

Free, fully-local voice dictation for developers — hotkey → speak → on-device transcription → text pasted into the focused app. No subscription, no cloud, no audio ever leaves your machine.

This npm package is a thin, dependency-free **installer**: it bootstraps [`uv`](https://docs.astral.sh/uv/) (which brings its own Python) and installs the real `yohoho` tool under the hood. You do **not** need Python pre-installed.

## Install

```bash
npm install -g @by-k4n/yohoho
```

This puts a `yohoho` command on your PATH. Then:

```bash
yohoho setup     # pick a hotkey, grant permissions, download the model (first run)
yohoho start     # press your hotkey anywhere to dictate
yohoho config    # interactive settings menu — record a new hotkey, tweak chimes, and more
```

macOS and Windows are supported (Linux is best-effort). On macOS, `setup` walks you through the required Accessibility / Input-Monitoring permissions.

## Alternatives

If you already have the Python toolchain:

```bash
uv tool install yohoho     # from PyPI
pipx install yohoho        # from PyPI
```

## Links

- Source, docs, and issues: https://github.com/by-k4n/yohoho
- License: MIT
