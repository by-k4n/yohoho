from contextlib import nullcontext

from yohoho.platform.windows.inject import WindowsTextInjector


class FakeCtl:
    def __init__(self):
        self.events = []

    def pressed(self, *mods):
        self.events.append(("hold", [str(m) for m in mods]))
        return nullcontext()

    def press(self, k):
        self.events.append(("press", k))

    def release(self, k):
        self.events.append(("release", k))


def test_paste_presses_ctrl_v_and_reports_success():
    ctl = FakeCtl()
    inj = WindowsTextInjector(controller=ctl)
    assert inj.paste() is True
    assert any(e[0] == "press" for e in ctl.events)


def test_paste_does_not_reactivate():
    # Windows panel is WS_EX_NOACTIVATE; paste must issue ONLY the Ctrl+V chord —
    # no activate/foreground/reactivation step. This pins that invariant explicitly.
    ctl = FakeCtl()
    inj = WindowsTextInjector(controller=ctl)
    from yohoho.core.platform_api import FocusToken
    assert inj.paste(FocusToken(gen=1, app_id="123")) is True
    assert [e[0] for e in ctl.events] == ["hold", "press", "release"]
