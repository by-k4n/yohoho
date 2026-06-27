from yohoho.core.ui._dpi import ensure_dpi_awareness


def test_win32_path_invokes_set_awareness():
    called = {"n": 0}
    ensure_dpi_awareness(platform="win32", set_awareness=lambda: called.__setitem__("n", called["n"] + 1))
    assert called["n"] == 1


def test_non_win32_is_noop():
    called = {"n": 0}
    ensure_dpi_awareness(platform="darwin", set_awareness=lambda: called.__setitem__("n", called["n"] + 1))
    assert called["n"] == 0


def test_set_awareness_failure_is_swallowed():
    def boom():
        raise OSError("no shcore")
    ensure_dpi_awareness(platform="win32", set_awareness=boom)  # must not raise
