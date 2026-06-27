from pathlib import Path

from yohoho.core.ui.fonts import install_font, resolve_family


def test_install_copies_font_idempotently(tmp_path):
    src = tmp_path / "Doto.ttf"
    src.write_bytes(b"ttf-bytes")
    dest_dir = tmp_path / "Fonts"
    p = install_font(src, dest_dir, platform="darwin")
    assert Path(p).exists() and (dest_dir / "Doto.ttf").read_bytes() == b"ttf-bytes"
    install_font(src, dest_dir, platform="darwin")  # idempotent, no error


def test_resolve_family_prefers_doto_then_menlo():
    # Tk registers the static Doto Regular font under family "Doto".
    assert resolve_family(["Doto", "Menlo", "Arial"]) == "Doto"
    # "Doto Black" is no longer in _PREFERRED; falls through to first available.
    assert resolve_family(["Doto Black", "Menlo", "Arial"]) == "Menlo"
    assert resolve_family(["Menlo", "Arial"]) == "Menlo"
    assert resolve_family(["Arial"]) == "Arial"  # last-resort: first available


def test_install_font_win32_registers_instead_of_copying(tmp_path):
    src = tmp_path / "Doto.ttf"
    src.write_bytes(b"ttf-bytes")
    registered = []
    out = install_font(src, platform="win32", register_fn=lambda p: registered.append(p))
    assert registered == [src] and out == src


def test_install_font_posix_still_copies(tmp_path):
    src = tmp_path / "Doto.ttf"
    src.write_bytes(b"ttf-bytes")
    dest_dir = tmp_path / "fonts"
    out = install_font(src, dest_dir=dest_dir, platform="darwin")
    assert out == dest_dir / "Doto.ttf" and out.read_bytes() == b"ttf-bytes"
