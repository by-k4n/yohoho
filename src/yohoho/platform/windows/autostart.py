"""Run-on-login via HKCU\\...\\Run. Writes an ABSOLUTE, QUOTED pythonw.exe command (a bare pythonw is
not on the login PATH for the uv-isolated venv; the console_scripts yohoho.exe pops a console).
Real winreg lives behind an injectable registry seam so unit tests never touch the real registry."""
from typing import Optional

_VALUE_NAME = "yohoho"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class _RealRegistry:
    def set_value(self, name: str, data: str) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, data)

    def get_value(self, name: str) -> Optional[str]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as k:
                val, _ = winreg.QueryValueEx(k, name)
                return val
        except FileNotFoundError:
            return None

    def delete_value(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass


def _quote_command(program_args: list[str]) -> str:
    # Quote the interpreter path (profile dirs contain spaces); leave -m yohoho start bare.
    interp, *rest = program_args
    return " ".join([f'"{interp}"', *rest])


class WindowsAutostart:
    def __init__(self, program_args: list[str], *, registry=None) -> None:
        self._cmd = _quote_command(program_args)
        self._reg = registry or _RealRegistry()

    def enable(self) -> bool:
        self._reg.set_value(_VALUE_NAME, self._cmd)
        return self.is_enabled()  # truthful read-back

    def disable(self) -> None:
        self._reg.delete_value(_VALUE_NAME)

    def is_enabled(self) -> bool:
        return self._reg.get_value(_VALUE_NAME) == self._cmd
