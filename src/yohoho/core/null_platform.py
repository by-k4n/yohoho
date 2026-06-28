from __future__ import annotations
from . import platform_api as pa


class _NullClipboard:
    def __init__(self):
        self._text = None

    def get_text(self):
        return self._text

    def set_text(self, text):
        self._text = text

    def has_nontext(self):
        return False


class _StdoutInjector:
    def __init__(self, clip: _NullClipboard):
        self._clip = clip

    def paste(self, token=None) -> bool:
        print(self._clip.get_text() or "", flush=True)
        return True

    def release_modifiers(self) -> None: ...


class _NullHotkeys:
    def configure(self, spec, on_activate, on_cancel=None):
        self._a = on_activate

    def start(self): ...

    def stop(self): ...

    def is_alive(self):
        return True

    @staticmethod
    def is_valid_spec(spec):
        return bool(spec)


class _NullFocus:
    def snapshot(self):
        return pa.FocusToken(gen=0)

    def unchanged(self, token):
        return True


class _NullAutostart:
    def enable(self):
        return True  # no-op autostart "succeeds" so setup reports honestly

    def disable(self): ...

    def is_enabled(self):
        return False


class _NullPermissions:
    def check(self):
        return pa.PermissionStatus(ok=True, permissions=())

    def request(self): ...

    def guide(self):
        return "No permissions required."


class NullProcessController:
    def __init__(self) -> None:
        self.spawned: list = []
        self.terminated: list = []

    def spawn_detached(self, argv) -> int:
        self.spawned.append(list(argv))
        return 424242

    def is_alive(self, pid: int) -> bool:
        return False

    def terminate(self, pid: int, graceful: bool = True) -> None:
        self.terminated.append((pid, graceful))


def make_null_platform() -> pa.PlatformBundle:
    clip = _NullClipboard()
    return pa.PlatformBundle(
        name="null",
        hotkeys=_NullHotkeys(),
        clipboard=clip,
        injector=_StdoutInjector(clip),
        focus=_NullFocus(),
        autostart=_NullAutostart(),
        permissions=_NullPermissions(),
    )
