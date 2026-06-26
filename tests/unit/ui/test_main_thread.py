"""Tests for MainThreadExecutor + marshal_bundle.

These are the heart of the M3 macOS dictation-crash fix: native side effects
(clipboard / paste / focus) MUST run on the UI main thread, never on the
transcribe worker, or posting a Quartz key event races the panel render and the
process aborts with SIGTRAP.  The executor lets a worker submit a callable and
block for its result while the main thread runs it.

No Tk here: the test's own thread plays the role of the "main" (pumping) thread.
"""

from __future__ import annotations

import threading
import time

from yohoho.core.platform_api import PlatformBundle
from yohoho.core.ui.main_thread import MainThreadExecutor, marshal_bundle


def _pump_until(executor: MainThreadExecutor, thread: threading.Thread, timeout: float = 2.0) -> None:
    """Pump on THIS (main) thread until *thread* finishes (or we time out)."""
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        executor.pump()
        time.sleep(0.002)
    thread.join(1.0)


# ---------------------------------------------------------------------------
# MainThreadExecutor
# ---------------------------------------------------------------------------


def test_submit_runs_fn_on_the_pumping_thread_and_returns_result():
    ex = MainThreadExecutor()
    out: dict = {}

    def worker():
        # fn returns the thread it ran on — proving WHERE it executed.
        out["result"] = ex.submit(lambda: threading.current_thread())

    t = threading.Thread(target=worker)
    t.start()
    _pump_until(ex, t)

    assert out["result"] is threading.main_thread()  # ran on the pump thread, not the worker


def test_submit_runs_inline_on_main_thread_without_any_pump():
    ex = MainThreadExecutor()
    # On the main thread there is no one else to pump; submit must run inline.
    assert ex.submit(lambda: 42) == 42


def test_submit_propagates_the_fn_exception_to_the_caller():
    ex = MainThreadExecutor()
    captured: dict = {}

    def boom():
        raise ValueError("boom")

    def worker():
        try:
            ex.submit(boom)
        except Exception as exc:  # noqa: BLE001
            captured["exc"] = exc

    t = threading.Thread(target=worker)
    t.start()
    _pump_until(ex, t)

    assert isinstance(captured.get("exc"), ValueError)
    assert str(captured["exc"]) == "boom"


def test_shutdown_releases_a_blocked_worker_with_runtimeerror():
    ex = MainThreadExecutor()
    started = threading.Event()
    captured: dict = {}

    def worker():
        started.set()
        try:
            ex.submit(lambda: 1)  # nobody pumps -> blocks until shutdown
        except RuntimeError as exc:
            captured["exc"] = exc

    t = threading.Thread(target=worker)
    t.start()
    started.wait(1.0)
    time.sleep(0.05)  # let the job enqueue and the worker block on the reply
    ex.shutdown()
    t.join(1.0)

    assert isinstance(captured.get("exc"), RuntimeError)


def test_submit_after_shutdown_raises_from_a_worker_thread():
    ex = MainThreadExecutor()
    ex.shutdown()
    captured: dict = {}

    def worker():
        try:
            ex.submit(lambda: 1)
        except RuntimeError as exc:
            captured["exc"] = exc

    t = threading.Thread(target=worker)
    t.start()
    t.join(1.0)

    assert isinstance(captured.get("exc"), RuntimeError)


# ---------------------------------------------------------------------------
# marshal_bundle
# ---------------------------------------------------------------------------


class _NullClip:
    def get_text(self):
        return None

    def set_text(self, text):
        pass

    def has_nontext(self):
        return False


class _NullFocus:
    def snapshot(self):
        return "tok"

    def unchanged(self, token):
        return True


def _bundle(clipboard, injector, focus) -> PlatformBundle:
    sentinel_hk, sentinel_as, sentinel_pm = object(), object(), object()
    return PlatformBundle(
        name="fake",
        hotkeys=sentinel_hk,
        clipboard=clipboard,
        injector=injector,
        focus=focus,
        autostart=sentinel_as,
        permissions=sentinel_pm,
    )


def test_marshal_bundle_wraps_native_surfaces_and_leaves_the_rest():
    ex = MainThreadExecutor()
    b = _bundle(_NullClip(), object(), _NullFocus())
    m = marshal_bundle(b, ex)

    # The three native side-effect surfaces are wrapped.
    assert m.clipboard is not b.clipboard
    assert m.injector is not b.injector
    assert m.focus is not b.focus
    # hotkeys / autostart / permissions are NOT invoked from the worker — untouched.
    assert m.hotkeys is b.hotkeys
    assert m.autostart is b.autostart
    assert m.permissions is b.permissions
    assert m.name == "fake"


def test_marshalled_injector_paste_runs_on_the_pump_thread():
    ex = MainThreadExecutor()
    ran_on: dict = {}

    class Inj:
        def paste(self, token=None):
            ran_on["thread"] = threading.current_thread()
            return True

        def release_modifiers(self):
            pass

    m = marshal_bundle(_bundle(_NullClip(), Inj(), _NullFocus()), ex)
    out: dict = {}

    def worker():
        out["result"] = m.injector.paste()

    t = threading.Thread(target=worker)
    t.start()
    _pump_until(ex, t)

    assert out["result"] is True
    assert ran_on["thread"] is threading.main_thread()


def test_marshalled_injector_paste_returns_false_after_shutdown():
    ex = MainThreadExecutor()
    ex.shutdown()

    class Inj:
        def paste(self, token=None):
            return True

        def release_modifiers(self):
            pass

    m = marshal_bundle(_bundle(_NullClip(), Inj(), _NullFocus()), ex)
    out: dict = {}

    def worker():
        # submit raises RuntimeError post-shutdown; the wrapper must swallow it
        # and report "not pasted" so the controller falls back to COPIED.
        out["result"] = m.injector.paste()

    t = threading.Thread(target=worker)
    t.start()
    t.join(1.0)

    assert out["result"] is False
