def test_get_platform_win32_returns_windows_bundle(monkeypatch):
    import yohoho.core.platform_factory as f
    monkeypatch.setattr(f.sys, "platform", "win32")
    b = f.get_platform()
    assert b.name == "windows"
