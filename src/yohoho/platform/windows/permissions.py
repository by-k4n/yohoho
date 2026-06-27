"""Windows has no TCC-style gating → permissions are effectively a no-op."""
from yohoho.core.platform_api import PermissionStatus


class WindowsPermissions:
    def check(self) -> PermissionStatus:
        return PermissionStatus(ok=True, permissions=(), identity_ok=True)

    def request(self) -> None:
        pass

    def guide(self) -> str:
        return "No special permissions are required on Windows."
