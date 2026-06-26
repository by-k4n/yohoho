from pathlib import Path

from yohoho.core.ui.fonts import install_font, resolve_family


def test_install_copies_font_idempotently(tmp_path):
    src = tmp_path / "Doto.ttf"
    src.write_bytes(b"ttf-bytes")
    dest_dir = tmp_path / "Fonts"
    p = install_font(src, dest_dir)
    assert Path(p).exists() and (dest_dir / "Doto.ttf").read_bytes() == b"ttf-bytes"
    install_font(src, dest_dir)  # idempotent, no error


def test_resolve_family_prefers_doto_then_menlo():
    # Tk registers the static Doto Regular font under family "Doto".
    assert resolve_family(["Doto", "Menlo", "Arial"]) == "Doto"
    # "Doto Black" is no longer in _PREFERRED; falls through to first available.
    assert resolve_family(["Doto Black", "Menlo", "Arial"]) == "Menlo"
    assert resolve_family(["Menlo", "Arial"]) == "Menlo"
    assert resolve_family(["Arial"]) == "Arial"  # last-resort: first available
