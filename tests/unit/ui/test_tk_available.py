import pytest

pytestmark = pytest.mark.gui


def test_tk_initialises():
    import tkinter

    import yohoho.core.ui  # noqa: F401  — triggers the env shim

    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()
    assert root.tk.call("tk", "windowingsystem") in ("aqua", "x11", "win32")
    root.destroy()
