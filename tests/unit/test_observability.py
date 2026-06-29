from yohoho.core.observability import (
    TranscriptText,
    setup_logging,
    record_error,
    read_last_error,
)


def test_transcript_repr_never_reveals_text():
    t = TranscriptText("my secret password is hunter2")
    assert "hunter2" not in repr(t)
    assert "hunter2" not in str(t)
    assert "len=" in repr(t) and "sha8=" in repr(t)
    assert t.value == "my secret password is hunter2"  # real text only via .value


def test_no_handler_emits_transcript_text(tmp_path):
    log = setup_logging(tmp_path, level="debug")
    secret = "the eagle lands at midnight"
    log.info("transcribed", extra={"transcript": TranscriptText(secret)})
    log.info("oops %s", TranscriptText(secret))  # even if someone interpolates it
    for h in log.handlers:
        h.flush()
    contents = (tmp_path / "logs" / "yohoho.log").read_text()
    assert secret not in contents
    assert "transcribed" in contents


def test_log_file_is_utf8_so_unicode_records_round_trip(tmp_path):
    """The daemon log handler must be UTF-8. On Windows a default (cp1252) handler would DROP a
    record containing `→` (U+2192 → UnicodeEncodeError, swallowed by logging.handleError) and would
    garble `·`/`—` when `yohoho logs` reads the file back as UTF-8. Regression for the Windows
    logging fix (observability.setup_logging RotatingFileHandler encoding=)."""
    log = setup_logging(tmp_path, level="debug")
    log.info("arrow %s dot %s dash %s", "→", "·", "—")
    for h in log.handlers:
        h.flush()
    # Read exactly the way `yohoho logs` does (cli.run_logs → read_text(encoding="utf-8")).
    contents = (tmp_path / "logs" / "yohoho.log").read_text(encoding="utf-8")
    assert "→" in contents                       # not dropped (it would be, under a cp1252 handler)
    assert "·" in contents and "—" in contents   # not mojibled on the utf-8 readback


def test_record_and_read_last_error(tmp_path):
    record_error(tmp_path, code="PASTE", message="focus changed")
    e = read_last_error(tmp_path)
    assert e["code"] == "PASTE" and "focus changed" in e["message"]


def test_clean_shutdown_marker_roundtrip(tmp_path):
    from yohoho.core.observability import mark_clean_shutdown, detect_prior_crash, mark_running

    mark_running(tmp_path)
    assert detect_prior_crash(tmp_path) is True  # running marker present, no clean marker => crash
    mark_clean_shutdown(tmp_path)
    assert detect_prior_crash(tmp_path) is False
