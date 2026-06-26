from yohoho.platform.windows.autostart import WindowsAutostart


class FakeRegistry:
    """In-memory HKCU...\\Run; records set/delete/read."""
    def __init__(self):
        self.values = {}
    def set_value(self, name, data):
        self.values[name] = data
    def get_value(self, name):
        return self.values.get(name)
    def delete_value(self, name):
        self.values.pop(name, None)


def test_enable_writes_quoted_pythonw_command_and_reads_back_true():
    reg = FakeRegistry()
    a = WindowsAutostart(program_args=[r"C:\venv\Scripts\pythonw.exe", "-m", "yohoho", "start"],
                         registry=reg)
    assert a.enable() is True
    val = reg.values["yohoho"]
    assert val.startswith('"') and val.endswith('-m yohoho start') and "pythonw.exe" in val
    assert a.is_enabled() is True


def test_disable_removes_value():
    reg = FakeRegistry()
    a = WindowsAutostart(program_args=["pythonw.exe", "-m", "yohoho", "start"], registry=reg)
    a.enable()
    a.disable()
    assert a.is_enabled() is False
