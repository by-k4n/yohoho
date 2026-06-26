import pytest

pytestmark = pytest.mark.gui


def test_apply_chrome_does_not_raise():
    import tkinter

    import yohoho.core.ui  # noqa: F401  — triggers the Tcl env shim
    from yohoho.platform.macos_window import apply_chrome, place_bottom_center, set_accessory_policy

    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        pytest.skip(f"no Tk/display: {e}")
    root.withdraw()
    assert set_accessory_policy() in (True, False)  # bool either way, never raises
    top = tkinter.Toplevel(root)
    apply_chrome(root, top, alpha=0.96)
    place_bottom_center(root, top, 296, 80)
    top.update_idletasks()
    root.destroy()
