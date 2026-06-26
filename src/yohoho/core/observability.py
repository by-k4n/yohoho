"""
Observability utilities for yohoho.

Privacy guarantee: TranscriptText.__str__ and __repr__ NEVER emit the raw
transcribed text.  _ScrubFilter provides defense-in-depth on every log handler
so that even accidental extra= fields or %-style args cannot leak speech.
"""

from __future__ import annotations

import faulthandler
import hashlib
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# TranscriptText — the structural leak-prevention wrapper
# ---------------------------------------------------------------------------


class TranscriptText:
    """Immutable wrapper around a transcribed string.

    .value   — the only way to access the raw text.
    str/repr — return a safe fingerprint: <transcript len=NN sha8=xxxxxxxx>
    """

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        object.__setattr__(self, "_text", text)

    # Prevent mutation
    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        raise AttributeError("TranscriptText is immutable")

    @property
    def value(self) -> str:
        return object.__getattribute__(self, "_text")

    def _safe_repr(self) -> str:
        text = object.__getattribute__(self, "_text")
        sha8 = hashlib.sha256(text.encode()).hexdigest()[:8]
        return f"<transcript len={len(text)} sha8={sha8}>"

    def __repr__(self) -> str:
        return self._safe_repr()

    def __str__(self) -> str:
        return self._safe_repr()

    def __format__(self, format_spec: str) -> str:  # noqa: D105
        return self._safe_repr()


# ---------------------------------------------------------------------------
# _ScrubFilter — defense-in-depth on every handler
# ---------------------------------------------------------------------------


class _ScrubFilter(logging.Filter):
    """Replace any TranscriptText that slips through into log records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        # Neutralise %-style positional / keyword args
        if isinstance(record.args, tuple):
            record.args = tuple(
                repr(a) if isinstance(a, TranscriptText) else a for a in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                k: (repr(v) if isinstance(v, TranscriptText) else v) for k, v in record.args.items()
            }
        elif isinstance(record.args, TranscriptText):
            record.args = repr(record.args)

        # Neutralise extra= fields attached directly to the record
        for attr in list(vars(record).keys()):
            if isinstance(getattr(record, attr), TranscriptText):
                setattr(record, attr, repr(getattr(record, attr)))

        return True


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


def setup_logging(data_dir: Path, level: str = "info") -> logging.Logger:
    """Configure and return the 'yohoho' logger.

    Safe to call multiple times (idempotent — clears handlers on each call).
    Writes to <data_dir>/logs/yohoho.log; falls back to stderr if that fails.
    """
    data_dir = Path(data_dir)
    log_dir = data_dir / "logs"
    log_path = log_dir / "yohoho.log"

    logger = logging.getLogger("yohoho")

    # Remove existing handlers so repeated calls in tests don't duplicate output
    for h in logger.handlers[:]:
        h.close()
        logger.removeHandler(h)

    logger.propagate = False
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    formatter.converter = time.gmtime  # type: ignore[assignment]

    # Probe-write the log directory before attaching the file handler
    handler: logging.Handler
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Attempt to open / append to the log file
        with log_path.open("a"):
            pass
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=5,
        )
    except OSError as exc:
        handler = logging.StreamHandler(sys.stderr)
        # Attach filter before the error log so the error message itself is scrubbed
        handler.addFilter(_ScrubFilter())
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.error("Cannot open log file %s: %s — falling back to stderr", log_path, exc)
        return logger

    handler.setFormatter(formatter)
    handler.addFilter(_ScrubFilter())
    logger.addHandler(handler)
    return logger


# ---------------------------------------------------------------------------
# record_error / read_last_error
# ---------------------------------------------------------------------------


def record_error(data_dir: Path, *, code: str, message: str) -> None:
    """Atomically write the last structured error to <data_dir>/last_error.json."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "ts": _utc_now_iso(),
        "code": code,
        "message": message,
    }
    tmp = data_dir / "last_error.json.tmp"
    dest = data_dir / "last_error.json"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, dest)


def read_last_error(data_dir: Path) -> dict | None:
    """Return the last error dict, or None if missing / malformed."""
    dest = Path(data_dir) / "last_error.json"
    try:
        return json.loads(dest.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# install_crash_net
# ---------------------------------------------------------------------------


def install_crash_net(data_dir: Path, logger: logging.Logger) -> None:
    """Install sys.excepthook, threading.excepthook, and faulthandler."""
    data_dir = Path(data_dir)

    def _excepthook(exc_type, exc_value, exc_tb):  # noqa: ANN001
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))

    def _thread_excepthook(args):  # noqa: ANN001
        logger.critical(
            "Unhandled exception in thread %s",
            args.thread,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook  # type: ignore[attr-defined]

    try:
        # Intentionally left open for the process lifetime: faulthandler writes native
        # crash tracebacks to this fd on SIGSEGV/SIGABRT, so it must stay open until exit.
        crash_log = (data_dir / "logs" / "crash.log").open("a")
        faulthandler.enable(file=crash_log)
    except OSError:
        pass  # Best effort — never raise


# ---------------------------------------------------------------------------
# Crash markers
# ---------------------------------------------------------------------------

_RUNNING_MARKER = "running"
_CLEAN_MARKER = "clean_shutdown"


def mark_running(data_dir: Path) -> None:
    """Write a 'running' marker; indicates the process is alive."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _RUNNING_MARKER).write_text("1", encoding="utf-8")
    # Remove any stale clean marker from a previous run
    (data_dir / _CLEAN_MARKER).unlink(missing_ok=True)


def mark_clean_shutdown(data_dir: Path) -> None:
    """Write a 'clean_shutdown' marker so the next start knows this was clean."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _CLEAN_MARKER).write_text("1", encoding="utf-8")
    # Remove the running marker — process is done
    (data_dir / _RUNNING_MARKER).unlink(missing_ok=True)


def detect_prior_crash(data_dir: Path) -> bool:
    """Return True iff a prior run started but never cleanly stopped."""
    data_dir = Path(data_dir)
    running = (data_dir / _RUNNING_MARKER).exists()
    clean = (data_dir / _CLEAN_MARKER).exists()
    return running and not clean


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
