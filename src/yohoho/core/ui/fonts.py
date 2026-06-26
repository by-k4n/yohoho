"""Bundled Doto dot-matrix font: install (so Tk can resolve it by family name)
plus a graceful family-fallback chain."""

from __future__ import annotations

import shutil
from pathlib import Path

# src/yohoho/core/ui/fonts.py -> parents[2] == src/yohoho
PANEL_FONT_ASSET = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Doto.ttf"

# Tk registers the bundled static font (Doto Regular, wght=400) under family
# "Doto".  Fall back to Menlo as a last resort; it is NOT the intended look.
_PREFERRED = ("Doto", "Menlo")


def install_font(src: Path, dest_dir: Path | None = None) -> Path:
    """Copy the bundled font into the user font dir so Tk resolves it by family
    name. Idempotent: skips the copy if an identical file is already there.
    macOS uses ~/Library/Fonts (writable, no admin)."""
    if dest_dir is None:
        dest_dir = Path.home() / "Library" / "Fonts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists() or dest.read_bytes() != src.read_bytes():
        shutil.copyfile(src, dest)
    return dest


def resolve_family(available: list[str], preferred: tuple[str, ...] = _PREFERRED) -> str:
    """Return the first preferred family present in `available`; else the first
    available family; else 'TkDefaultFont'. (Menlo is only a last-resort fallback
    — NOT the intended dot-matrix look.)"""
    for fam in preferred:
        if fam in available:
            return fam
    return available[0] if available else "TkDefaultFont"
