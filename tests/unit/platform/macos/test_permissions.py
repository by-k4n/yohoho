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


def test_request_fires_prompts_and_opens_panes_for_denied():
    fired, opened = [], []
    p = MacPermissions(
        input_monitoring_fn=lambda: "denied",
        accessibility_fn=lambda: False,
        open_fn=lambda url: opened.append(url),
        im_request_fn=lambda: fired.append("im"),
        ax_request_fn=lambda: fired.append("ax"),
    )
    p.request()
    assert "im" in fired and "ax" in fired          # both native OS prompts fired
    assert len(opened) == 2                          # both Settings panes opened as fallback


def test_request_skips_granted_permissions():
    fired, opened = [], []
    p = MacPermissions(
        input_monitoring_fn=lambda: "granted",
        accessibility_fn=lambda: True,
        open_fn=lambda url: opened.append(url),
        im_request_fn=lambda: fired.append("im"),
        ax_request_fn=lambda: fired.append("ax"),
    )
    p.request()
    assert fired == [] and opened == []              # nothing fired/opened when all granted


def test_guide_names_the_responsible_terminal():
    p = MacPermissions(term_program_fn=lambda: "ghostty")
    assert "Ghostty" in p.guide()


def test_responsible_app_name_mapping():
    from yohoho.platform.macos.permissions import responsible_app_name
    assert responsible_app_name("Apple_Terminal") == "Apple Terminal"
    assert responsible_app_name("iTerm.app") == "iTerm"
    assert responsible_app_name("vscode") == "VS Code"
    assert responsible_app_name("WezTerm") == "WezTerm"
    assert responsible_app_name("") == "your terminal app"
    assert responsible_app_name("Some_New_Term") == "Some New Term"
