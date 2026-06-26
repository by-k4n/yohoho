from yohoho.platform.macos.autostart import MacAutostart, render_plist


def _run(calls, codes=None):
    """Fake launchctl: records argv, returns exit code by first matching keyword."""
    codes = codes or {}

    def run(argv):
        calls.append(argv)
        for keyword, code in codes.items():
            if keyword in argv:
                return code
        return 0

    return run


def test_render_plist_has_label_args_runatload_keepalive():
    xml = render_plist("pro.bykc.yohoho", ["/py", "-m", "yohoho", "start"])
    assert "pro.bykc.yohoho" in xml and "<key>RunAtLoad</key>" in xml
    assert "<key>KeepAlive</key>" in xml and "<string>start</string>" in xml


def test_render_plist_escapes_xml_special_chars():
    import xml.etree.ElementTree as ET
    xml = render_plist("com.test&label", ["/usr/bin/py&3", "-m", "yohoho", "start"])
    ET.fromstring(xml)              # raises ParseError if not well-formed
    assert "&amp;" in xml


def test_render_plist_includes_log_paths_when_given():
    import xml.etree.ElementTree as ET
    xml = render_plist("L", ["/py"], stdout_path="/tmp/out.log", stderr_path="/tmp/err.log")
    ET.fromstring(xml)
    assert "<key>StandardOutPath</key>" in xml and "/tmp/out.log" in xml
    assert "<key>StandardErrorPath</key>" in xml and "/tmp/err.log" in xml


def test_enable_writes_plist_bootouts_then_bootstraps(tmp_path):
    calls = []
    plist = tmp_path / "agent.plist"
    a = MacAutostart(label="pro.bykc.yohoho", program_args=["/py", "-m", "yohoho", "start"],
                     plist_path=plist, uid=501, run=_run(calls))
    assert a.enable() is True                            # print returns 0 -> loaded
    assert plist.exists() and "pro.bykc.yohoho" in plist.read_text()
    bootout_idx = next(i for i, c in enumerate(calls) if "bootout" in c)
    bootstrap_idx = next(i for i, c in enumerate(calls) if "bootstrap" in c)
    assert bootout_idx < bootstrap_idx                   # bootout must precede bootstrap


def test_enable_kickstarts_when_bootstrap_fails(tmp_path):
    calls = []
    plist = tmp_path / "agent.plist"
    # bootstrap returns EIO (5) — agent still loaded from a quick re-setup.
    a = MacAutostart(label="L", program_args=[], plist_path=plist, uid=501,
                     run=_run(calls, {"bootstrap": 5}))
    a.enable()
    assert any("kickstart" in c for c in calls)          # recovered via kickstart -k


def test_enable_reports_false_when_not_loaded(tmp_path):
    calls = []
    plist = tmp_path / "agent.plist"
    a = MacAutostart(label="L", program_args=[], plist_path=plist, uid=501,
                     run=_run(calls, {"print": 1}))      # print != 0 -> not loaded
    assert a.enable() is False


def test_enable_writes_log_paths_into_plist(tmp_path):
    plist = tmp_path / "agent.plist"
    a = MacAutostart(label="L", program_args=["/py"], plist_path=plist, uid=501,
                     log_dir=tmp_path, run=_run([]))
    a.enable()
    text = plist.read_text()
    assert "StandardOutPath" in text and "yohoho.out.log" in text
    assert "StandardErrorPath" in text and "yohoho.err.log" in text


def test_is_enabled_true_when_plist_present(tmp_path):
    plist = tmp_path / "agent.plist"
    plist.write_text("x")
    a = MacAutostart(label="L", program_args=[], plist_path=plist, uid=501, run=_run([]))
    assert a.is_enabled() is True


def test_is_enabled_false_when_plist_absent(tmp_path):
    a = MacAutostart(label="L", program_args=[], plist_path=tmp_path / "absent.plist",
                     uid=501, run=_run([]))
    assert a.is_enabled() is False


def test_disable_bootouts_then_removes_plist(tmp_path):
    calls = []
    plist = tmp_path / "agent.plist"
    plist.write_text("x")
    a = MacAutostart(label="pro.bykc.yohoho", program_args=[], plist_path=plist, uid=501,
                     run=_run(calls))
    a.disable()
    assert any("bootout" in c for c in calls) and not plist.exists()
