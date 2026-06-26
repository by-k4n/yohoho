def test_make_macos_bundle_has_all_six():
    from yohoho.platform.macos import make_macos_platform
    b = make_macos_platform()
    assert b.name == "macos"
    for attr in ("hotkeys", "clipboard", "injector", "focus", "autostart", "permissions"):
        assert getattr(b, attr) is not None


def test_factory_selects_macos_on_darwin(monkeypatch):
    import yohoho.core.platform_factory as pf
    monkeypatch.setattr(pf.sys, "platform", "darwin")
    assert pf.get_platform().name == "macos"


def test_factory_falls_back_to_null_off_darwin(monkeypatch):
    import yohoho.core.platform_factory as pf
    monkeypatch.setattr(pf.sys, "platform", "linux")
    assert pf.get_platform().name != "macos"   # null bundle


def test_factory_autostart_has_runnable_program_args():
    from yohoho.platform.macos import make_macos_platform
    from yohoho.platform.macos.autostart import render_plist
    a = make_macos_platform().autostart
    assert a._args and any("start" in x for x in a._args)     # not empty; runs `start`
    xml = render_plist(a._label, a._args)
    assert "<key>ProgramArguments</key>" in xml and "<string>start</string>" in xml


def test_macos_bundle_has_mac_window_chrome():
    from yohoho.platform.macos import make_macos_platform
    from yohoho.platform.macos.chrome import MacWindowChrome
    assert isinstance(make_macos_platform().window_chrome, MacWindowChrome)
