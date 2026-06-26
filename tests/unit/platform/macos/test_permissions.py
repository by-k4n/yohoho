from yohoho.platform.macos.permissions import MacPermissions


def _perm(probes, recorded="/cur/python", current="/cur/python"):
    return MacPermissions(
        input_monitoring_fn=lambda: probes["im"],
        accessibility_fn=lambda: probes["ax"],
        recorded_path_fn=lambda: recorded, current_path=current)


def test_check_all_granted_ok():
    st = _perm({"im": "granted", "ax": True}).check()
    assert st.ok is True and st.identity_ok is True
    keys = {p.key: p.state for p in st.permissions}
    assert keys == {"input_monitoring": "granted", "accessibility": "granted"}


def test_check_denied_not_ok_with_fix_hints():
    st = _perm({"im": "denied", "ax": False}).check()
    assert st.ok is False
    im = next(p for p in st.permissions if p.key == "input_monitoring")
    assert im.state == "denied" and im.deep_link and im.fix_hint


def test_identity_mismatch_flips_identity_ok():
    st = _perm({"im": "granted", "ax": True}, recorded="/old/python", current="/new/python").check()
    assert st.identity_ok is False
