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
