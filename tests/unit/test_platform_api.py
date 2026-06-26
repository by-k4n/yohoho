from yohoho.core import platform_api as pa
from yohoho.core.null_platform import make_null_platform


def test_null_platform_is_a_bundle():
    b = make_null_platform()
    assert isinstance(b, pa.PlatformBundle)
    assert b.name == "null"


def test_null_permissions_ok_and_not_applicable():
    b = make_null_platform()
    st = b.permissions.check()
    assert st.ok is True
    assert all(p.state == "not_applicable" for p in st.permissions)


def test_null_injector_records_pastes(capsys):
    b = make_null_platform()
    b.clipboard.set_text("hello world")
    assert b.injector.paste() is True
    assert "hello world" in capsys.readouterr().out
