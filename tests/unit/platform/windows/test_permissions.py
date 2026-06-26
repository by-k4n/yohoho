from yohoho.platform.windows.permissions import WindowsPermissions


def test_check_all_granted():
    st = WindowsPermissions().check()
    assert st.ok is True and st.identity_ok is True and st.permissions == ()


def test_guide_mentions_no_permissions():
    assert "no special permissions" in WindowsPermissions().guide().lower()
