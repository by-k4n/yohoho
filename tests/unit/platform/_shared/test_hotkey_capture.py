from yohoho.platform._shared.hotkey_capture import HoldCapture, PynputHotkeyCapturer


def test_hold_three_seconds_commits():
    sm = HoldCapture(started_at=0.0, hold_seconds=3.0)
    sm.key_down("ctrl_l", 0.0)
    sm.key_down("space", 0.0)
    assert sm.poll(2.9)[0] == "progress"
    assert sm.poll(3.0) == ("commit", "lctrl+space")


def test_changing_set_resets_timer():
    sm = HoldCapture(started_at=0.0, hold_seconds=3.0)
    sm.key_down("ctrl_l", 0.0)
    assert sm.poll(2.0)[0] == "progress"
    sm.key_down("alt_l", 2.0)                      # set changed -> reset at t=2
    kind, frac = sm.poll(4.0)
    assert kind == "progress" and frac < 0.7       # only 2s of the new hold elapsed
    assert sm.poll(5.0) == ("commit", "lctrl+lalt")


def test_release_resets_and_modifier_only_allowed():
    sm = HoldCapture(started_at=0.0, hold_seconds=3.0)
    sm.key_down("cmd_l", 0.0)
    sm.key_up("cmd_l", 1.0)                         # back to empty
    assert sm.poll(1.0)[0] == "idle"
    sm.key_down("cmd_r", 1.0)
    assert sm.poll(4.0) == ("commit", "rcmd")       # modifier-only chord


def test_idle_timeout_when_nothing_held():
    sm = HoldCapture(started_at=0.0, hold_seconds=3.0, idle_timeout=8.0)
    assert sm.poll(7.9)[0] == "idle"
    assert sm.poll(8.0) == ("timeout", None)


def test_active_hold_defers_timeout():
    sm = HoldCapture(started_at=0.0, hold_seconds=3.0, idle_timeout=8.0)
    sm.key_down("ctrl_l", 7.0)
    assert sm.poll(9.0)[0] == "progress"            # holding -> no timeout


def test_key_repeat_does_not_reset():
    sm = HoldCapture(started_at=0.0, hold_seconds=3.0)
    sm.key_down("ctrl_l", 0.0)
    sm.key_down("ctrl_l", 1.5)                      # OS auto-repeat: ignored
    assert sm.poll(3.0) == ("commit", "lctrl")


class _FakeListener:
    """Replays a scripted list of (kind, key_name) events on start(), via the callbacks."""

    def __init__(self, on_press, on_release, script):
        self._on_press, self._on_release, self._script = on_press, on_release, script

    def start(self):
        for kind, name in self._script:
            key = type("K", (), {"name": name, "char": None})()
            (self._on_press if kind == "down" else self._on_release)(key)

    def stop(self):
        pass


def _clock_seq(values):
    vals = list(values)
    state = {"i": 0}

    def _next():
        i = min(state["i"], len(vals) - 1)
        state["i"] += 1
        return vals[i]

    return _next


def test_capturer_commits_scripted_chord():
    script = [("down", "cmd_r"), ("down", "space")]
    # clock: listener-start events all stamped 0.0, then poll sees t>=hold
    times = [0.0, 0.0, 0.0, 0.0, 0.0, 5.0]
    cap = PynputHotkeyCapturer(
        listener_factory=lambda p, r: _FakeListener(p, r, script),
        clock=_clock_seq(times), poll_interval=0.0,
    )
    assert cap.capture(seconds=3.0) == "rcmd+space"


def test_capturer_returns_none_on_timeout():
    cap = PynputHotkeyCapturer(
        listener_factory=lambda p, r: _FakeListener(p, r, []),     # no events
        clock=_clock_seq([0.0, 8.0]), poll_interval=0.0,
    )
    assert cap.capture(seconds=3.0) is None


def test_capturer_returns_none_when_listener_init_fails():
    def _boom(p, r):
        raise RuntimeError("backend not initialised (no Input-Monitoring permission)")

    cap = PynputHotkeyCapturer(listener_factory=_boom, poll_interval=0.0)
    assert cap.capture(seconds=3.0) is None         # degrades instead of raising
