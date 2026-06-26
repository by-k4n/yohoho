from contextlib import nullcontext

from yohoho.core.platform_api import FocusToken
from yohoho.platform.macos.clipboard import MacClipboard
from yohoho.platform.macos.inject import MacTextInjector


class FakeCtl:
    """Records pynput Controller calls; pressed() is a no-op context manager."""

    def __init__(self):
        self.events = []

    def pressed(self, *mods):
        self.events.append(("hold", [str(m) for m in mods]))
        return nullcontext()

    def press(self, k):
        self.events.append(("press", k))

    def release(self, k):
        self.events.append(("release", k))


def test_clipboard_get_set_via_seam():
    store = {"v": None}
    cb = MacClipboard(get_fn=lambda: store["v"], set_fn=lambda t: store.__setitem__("v", t),
                      nontext_fn=lambda: False)
    cb.set_text("hi")
    assert cb.get_text() == "hi" and cb.has_nontext() is False


def test_injector_paste_invokes_cmd_v_and_reports_success():
    ctl = FakeCtl()
    inj = MacTextInjector(controller=ctl)
    assert inj.paste() is True
    assert any(e[0] == "press" for e in ctl.events)   # a key was pressed (V)


def test_paste_returns_false_on_controller_error():
    class RaisingCtl:
        def pressed(self, *a): raise RuntimeError("boom")
    inj = MacTextInjector(controller=RaisingCtl())
    assert inj.paste() is False


def test_paste_reactivates_target_app_before_pasting():
    activated = {"app": None}
    front = {"v": "unknown"}  # our panel stole focus

    def activate(app_id):
        activated["app"] = app_id
        front["v"] = app_id  # activation takes effect immediately
        return True

    ctl = FakeCtl()
    inj = MacTextInjector(
        controller=ctl,
        activate_fn=activate,
        frontmost_fn=lambda: front["v"],
        sleep_fn=lambda s: None,
    )
    assert inj.paste(FocusToken(gen=1, app_id="com.acme.app")) is True
    assert activated["app"] == "com.acme.app"          # target was re-activated
    assert any(e[0] == "press" for e in ctl.events)    # then the paste happened


def test_paste_waits_until_target_is_frontmost_then_pastes():
    seq = ["unknown", "unknown", "com.acme.app"]  # activation lands on the 3rd poll
    i = {"n": 0}
    sleeps = {"n": 0}

    def frontmost():
        v = seq[min(i["n"], len(seq) - 1)]
        i["n"] += 1
        return v

    ctl = FakeCtl()
    inj = MacTextInjector(
        controller=ctl,
        activate_fn=lambda a: True,
        frontmost_fn=frontmost,
        sleep_fn=lambda s: sleeps.__setitem__("n", sleeps["n"] + 1),
    )
    assert inj.paste(FocusToken(gen=1, app_id="com.acme.app")) is True
    assert sleeps["n"] == 2  # waited two ticks until the target became frontmost


def test_paste_skips_reactivation_for_unknown_or_missing_target():
    activated = {"called": False}

    def activate(app_id):
        activated["called"] = True
        return True

    inj = MacTextInjector(
        controller=FakeCtl(),
        activate_fn=activate,
        frontmost_fn=lambda: "whatever",
        sleep_fn=lambda s: None,
    )
    assert inj.paste(FocusToken(gen=1, app_id="unknown")) is True
    assert inj.paste(None) is True
    assert activated["called"] is False  # nothing to re-activate
