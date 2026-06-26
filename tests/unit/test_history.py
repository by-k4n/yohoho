from yohoho.core.events import Outcome
from yohoho.core.history import HistoryStore


def test_writes_pasted_entry(tmp_path):
    h = HistoryStore(tmp_path, enabled=True)
    h.add("hello world", outcome=Outcome.PASTED, dur_s=1.2)
    rows = h.read()
    assert rows[0]["text"] == "hello world" and rows[0]["outcome"] == "pasted"
    assert rows[0]["word_count"] == 2 and "id" in rows[0] and "ts" in rows[0]


def test_disabled_writes_nothing(tmp_path):
    h = HistoryStore(tmp_path, enabled=False)
    h.add("secret", outcome=Outcome.PASTED, dur_s=1)
    assert not (tmp_path / "history.jsonl").exists()
    assert h.read() == []


def test_empty_or_whitespace_not_written(tmp_path):
    h = HistoryStore(tmp_path, enabled=True)
    h.add("   ", outcome=Outcome.PASTED, dur_s=1)  # P2: whitespace-only suppressed
    assert h.read() == []


def test_discarded_goes_to_recovery_bucket(tmp_path):
    h = HistoryStore(tmp_path, enabled=True)
    h.add("oops", outcome=Outcome.DISCARDED, dur_s=1)
    assert h.read() == []  # main timeline clean
    assert h.read_discarded()[0]["text"] == "oops"


def test_copied_goes_to_main_timeline(tmp_path):
    # outcome=copied means the paste failed and text was left on the clipboard —
    # still the user's real dictation, so it belongs on the main timeline (not discarded).
    h = HistoryStore(tmp_path, enabled=True)
    h.add("left on clipboard", outcome=Outcome.COPIED, dur_s=1)
    assert h.read()[0]["text"] == "left on clipboard"
    assert h.read()[0]["outcome"] == "copied"
    assert h.read_discarded() == []


def test_reader_tolerates_malformed_and_partial_lines(tmp_path):
    p = tmp_path / "history.jsonl"
    p.write_text(
        '{"v":1,"text":"ok","outcome":"pasted","ts":"2026-01-01T00:00:00Z"}\n'
        "GARBAGE NOT JSON\n"
        '{"v":1,"text":"partial"'  # no closing brace, no newline
    )
    h = HistoryStore(tmp_path, enabled=True)
    rows = h.read()
    assert [r["text"] for r in rows] == ["ok"]  # malformed + partial skipped


def test_retention_caps_entries(tmp_path):
    h = HistoryStore(tmp_path, enabled=True, max_entries=3)
    for i in range(10):
        h.add(f"line {i}", outcome=Outcome.PASTED, dur_s=1)
    h.compact()
    assert len(h.read()) == 3 and h.read()[-1]["text"] == "line 9"


def test_clear_removes_both_files(tmp_path):
    h = HistoryStore(tmp_path, enabled=True)
    h.add("a", outcome=Outcome.PASTED, dur_s=1)
    h.add("b", outcome=Outcome.DISCARDED, dur_s=1)
    h.clear()
    assert h.read() == [] and h.read_discarded() == []
