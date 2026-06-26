import types

from yohoho.platform.macos.input_source import _install


def test_install_replaces_keycode_context_with_a_cached_yielder():
    # Two modules import keycode_context by name; both must be patched so neither
    # the util backend nor the keyboard listener calls the real (TSM) version.
    m1 = types.SimpleNamespace(keycode_context=object())
    m2 = types.SimpleNamespace(keycode_context=object())

    _install(("kbd_type", b"layout_data"), (m1, m2))

    for module in (m1, m2):
        with module.keycode_context() as ctx:
            assert ctx == ("kbd_type", b"layout_data")
