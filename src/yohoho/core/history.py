"""Transcript history store — local, capped, clearable.

Privacy guarantee: writes are outcome-gated; all data stays in data_dir only.
Never synced, never logged outside this file.

# TODO(M4): bounded background writer queue for non-blocking hot-path appends
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yohoho.core.events import Outcome

_WRITABLE_OUTCOMES = {Outcome.PASTED, Outcome.COPIED, Outcome.DISCARDED}


class HistoryStore:
    """Append-only JSONL history with compaction and a discarded-entry recovery bucket."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        enabled: bool,
        max_entries: int = 1000,
        max_age_days: int = 30,
        capture_app_id: bool = False,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._enabled = enabled
        self._max_entries = max_entries
        self._max_age_days = max_age_days
        self._capture_app_id = capture_app_id

    @property
    def _main_file(self) -> Path:
        return self._data_dir / "history.jsonl"

    @property
    def _discarded_file(self) -> Path:
        return self._data_dir / "history-discarded.jsonl"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(
        self,
        text: str,
        *,
        outcome: Outcome,
        dur_s: float,
        app_id: str | None = None,
    ) -> None:
        """Append one entry, gated on enabled / non-empty / valid outcome."""
        if not self._enabled:
            return
        if not text.strip():
            return
        if outcome not in _WRITABLE_OUTCOMES:
            return

        self._data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        record: dict = {
            "v": 1,
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "dur_s": dur_s,
            "len": len(text),
            "word_count": len(text.split()),
            "outcome": outcome.value,
            "text": text,
        }
        if self._capture_app_id and app_id is not None:
            record["app_id"] = app_id

        # TODO(M4): cap the recovery bucket to ~20 entries (DESIGN §11) so accidental
        # cancels stay recoverable without the discarded file growing unbounded.
        target = self._discarded_file if outcome is Outcome.DISCARDED else self._main_file
        self._atomic_append(target, record)

    def read(self) -> list[dict]:
        """Return main-timeline entries, tolerant of malformed/partial lines."""
        return self._read_file(self._main_file)

    def read_discarded(self) -> list[dict]:
        """Return recovery-bucket entries, tolerant of malformed/partial lines."""
        return self._read_file(self._discarded_file)

    def compact(self) -> None:
        """Drop entries older than max_age_days, then cap to max_entries.

        Writes atomically via a .tmp file; never runs on the hot path — caller decides when.
        """
        rows = self._read_file(self._main_file)

        cutoff_ts = _age_cutoff_ts(self._max_age_days)
        if cutoff_ts:
            kept = []
            for r in rows:
                ts = r.get("ts", "")
                try:
                    if ts >= cutoff_ts:
                        kept.append(r)
                except Exception:
                    kept.append(r)  # unparseable ts: keep it
            rows = kept

        # Keep last max_entries (already sorted by ts from _read_file)
        if len(rows) > self._max_entries:
            rows = rows[-self._max_entries :]

        self._write_file_atomic(self._main_file, rows)

    def clear(self) -> None:
        """Remove both history files if they exist."""
        for path in (self._main_file, self._discarded_file):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_file(path: Path) -> list[dict]:
        if not path.exists():
            return []

        rows: list[dict] = []
        raw = path.read_bytes().decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except (json.JSONDecodeError, ValueError):
                pass  # malformed or partial line — skip

        rows.sort(key=lambda r: r.get("ts", ""))
        return rows

    @staticmethod
    def _atomic_append(path: Path, record: dict) -> None:
        """Append a single JSON line with mode 0o600, O_APPEND for atomicity."""
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    @staticmethod
    def _write_file_atomic(path: Path, rows: list[dict]) -> None:
        """Write rows to a .tmp file then rename, preserving 0o600."""
        tmp = path.with_suffix(".jsonl.tmp")
        content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(path))


def _age_cutoff_ts(max_age_days: int) -> str | None:
    """Return an ISO-8601 string representing the earliest allowed timestamp, or None."""
    if max_age_days <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return cutoff.isoformat()
