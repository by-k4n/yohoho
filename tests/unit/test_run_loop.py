import yohoho.core.run_loop as run_loop
from yohoho.core.run_loop import format_hotkey, handle_activation
from yohoho.core.events import State, Terminal, ErrorCode


class FakeCtl:
    def __init__(self):
        self.state = State.IDLE
        self.toggled = 0
        self.fed = None

    def toggle(self):
        self.toggled += 1
        self.state = State.RECORDING

    def feed_audio_result(self, a):
        self.fed = a


class FakeRec:
    def __init__(self, err=None, audio="AUDIO"):
        self._err, self._audio = err, audio
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1
        return self._err

    def stop(self):
        self.stopped += 1
        return self._audio


def test_first_activation_starts_mic_then_toggles():
    c, r, q = FakeCtl(), FakeRec(), []
    handle_activation(c, r, q.append)
    assert r.started == 1 and c.toggled == 1 and c.state == State.RECORDING and q == []


def test_mic_failure_emits_error_and_does_not_toggle():
    c, r, q = FakeCtl(), FakeRec(err=object()), []
    handle_activation(c, r, q.append)
    assert c.toggled == 0 and c.state == State.IDLE
    assert q and q[0]["t"] == "terminal"
    assert q[0]["kind"] == Terminal.ERROR and q[0]["code"] == ErrorCode.MIC


def test_second_activation_stops_and_feeds_without_toggling():
    c, r, q = FakeCtl(), FakeRec(), []
    handle_activation(c, r, q.append)          # start
    handle_activation(c, r, q.append)          # stop
    assert r.stopped == 1 and c.fed == "AUDIO" and c.toggled == 1   # toggled only once (start)


class FakeChimes:
    def __init__(self):
        self.start = 0
        self.end = 0

    def play_start(self):
        self.start += 1

    def play_end(self):
        self.end += 1


def test_start_chime_plays_only_when_recording_starts():
    c, r, q, ch = FakeCtl(), FakeRec(), [], FakeChimes()
    handle_activation(c, r, q.append, ch)      # start -> chime
    assert ch.start == 1 and c.toggled == 1
    handle_activation(c, r, q.append, ch)      # stop -> NO chime
    assert ch.start == 1


def test_no_chime_when_the_mic_fails_to_start():
    c, r, q, ch = FakeCtl(), FakeRec(err=object()), [], FakeChimes()
    handle_activation(c, r, q.append, ch)
    assert ch.start == 0 and c.toggled == 0    # never claimed "on" when the mic didn't open


def test_activation_ignored_while_transcribing_does_not_restop_or_refeed():
    # Pressing the hotkey during transcription must be a no-op — re-stopping would
    # re-feed the recorder and paste the same transcript twice.
    c, r, q = FakeCtl(), FakeRec(), []
    c.state = State.TRANSCRIBING
    handle_activation(c, r, q.append)
    assert r.stopped == 0 and c.fed is None and q == []


def test_format_hotkey_uses_mac_glyphs_on_darwin(monkeypatch):
    monkeypatch.setattr(run_loop.sys, "platform", "darwin")
    assert format_hotkey("ctrl+alt+space") == "⌃⌥Space"
    assert format_hotkey("cmd+shift+f14") == "⌘⇧F14"
    assert format_hotkey("ctrl+alt+a") == "⌃⌥A"


def test_format_hotkey_plus_joined_off_darwin(monkeypatch):
    monkeypatch.setattr(run_loop.sys, "platform", "linux")
    assert format_hotkey("ctrl+alt+space") == "Ctrl+Alt+Space"
