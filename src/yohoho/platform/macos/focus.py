from __future__ import annotations
from typing import Callable
from yohoho.core.platform_api import FocusToken
from yohoho.platform.macos import _appkit


class MacFocusProbe:
    def __init__(
        self,
        frontmost_fn: Callable[[], str] = _appkit.frontmost_bundle_id,
        self_ids: tuple[str, ...] | None = None,
    ) -> None:
        self._frontmost = frontmost_fn
        # App ids that mean "our own panel is frontmost", NOT "the user switched
        # apps".  Showing the accessory panel can briefly make this process the
        # active app, which surfaces as 'unknown' (a bare interpreter has no bundle
        # id).  We treat those as unchanged so the paste still targets the user's app.
        #
        # NOTE: do NOT resolve our real bundle id here — that touches AppKit, and
        # eagerly loading AppKit at factory time (before Tk's run loop is up) crashes
        # the process with a SIGTRAP.  'unknown' covers the python deployment; a
        # bundled .app can add its id via self_ids later.
        self._self_ids = tuple(dict.fromkeys(self_ids if self_ids is not None else ("unknown",)))
        self._gen = 0

    def snapshot(self) -> FocusToken:
        self._gen += 1
        return FocusToken(gen=self._gen, app_id=self._frontmost())

    def unchanged(self, token: FocusToken) -> bool:
        fm = self._frontmost()
        if fm == token.app_id:
            return True
        # Our own panel stole activation (fm is us/unknown) — the user did not
        # switch to a different real app, so the target is still valid.
        return fm in self._self_ids
