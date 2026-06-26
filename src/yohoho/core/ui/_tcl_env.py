"""Repair the Tcl/Tk library path for python-build-standalone interpreters.

Must run BEFORE `import tkinter`. python-build-standalone bakes a build-machine
TCL_LIBRARY path, so a bare Tk() raises "Can't find a usable init.tcl". We point
TCL_LIBRARY/TK_LIBRARY at the interpreter's bundled lib dirs when they exist.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_tcl_env() -> None:
    if os.environ.get("TCL_LIBRARY"):
        return  # already set (or a correctly-built Python) — leave it
    base = Path(sys.executable).resolve().parent.parent / "lib"
    for ver in ("tcl8.6", "tcl9.0"):
        tcl = base / ver
        tk = base / ver.replace("tcl", "tk")
        if tcl.is_dir():
            os.environ["TCL_LIBRARY"] = str(tcl)
            if tk.is_dir():
                os.environ["TK_LIBRARY"] = str(tk)
            return
