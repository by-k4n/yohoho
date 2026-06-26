"""Marshal platform side effects onto the Tk main thread.

macOS is the forcing case.  AppKit/CoreGraphics surfaces — NSPasteboard
(clipboard), Quartz keyboard-event posting (paste / modifier release), and
NSWorkspace (focus) — must be touched on the same thread that owns the UI
run-loop.  A worker thread that posts a Cmd+V while the main thread is mid-render
of the (borderless, transparent, accessory-policy) status panel aborts the whole
process with a SIGTRAP — there is no Python exception to catch.

The M2 architecture already says "off-main threads only ``queue.put``; only the
main thread touches Tk/native".  This module extends that rule to calls that need
a *return value*: a worker submits a callable and blocks on a thread-safe reply
queue while the runner's main-thread drain loop :meth:`MainThreadExecutor.pump`\\ s
and runs it.  No Tk or AppKit call is ever made off the main thread — the worker
only ever touches plain :class:`queue.Queue` objects — so this introduces no new
thread-safety hazard.

``engine.recognize`` is deliberately NOT marshalled: CoreML inference is safe off
the main thread and is long-running, so it must stay on the worker or it would
freeze the UI.  Only the :class:`PlatformBundle` side-effect surfaces are wrapped.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import replace
from typing import Any, Callable

from yohoho.core.platform_api import PlatformBundle


class MainThreadExecutor:
    """Run callables on the main thread from worker threads, blocking for the result.

    Lifecycle:
      * Workers call :meth:`submit` — runs inline if already on the main thread,
        otherwise enqueues the job and blocks until the main thread runs it.
      * The runner calls :meth:`pump` from its main-thread drain loop to execute
        all pending jobs.
      * The runner calls :meth:`shutdown` on teardown so any worker still waiting
        is released (with an error) instead of hanging.
    """

    def __init__(self) -> None:
        self._jobs: "queue.Queue[tuple[Callable[[], Any], queue.Queue]]" = queue.Queue()
        self._closed = False
        # Guards the (check-closed + enqueue) in submit against shutdown's
        # (set-closed + drain) so a job can never be enqueued after the final drain
        # and strand its worker forever on reply.get() (TOCTOU).
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[], Any]) -> Any:
        """Run *fn* on the main thread; return its result or re-raise its exception.

        Runs inline when called on the main thread (no pump required, no deadlock).
        Raises :class:`RuntimeError` if the executor has been shut down so callers
        fall back to a safe default rather than blocking forever.
        """
        if threading.current_thread() is threading.main_thread():
            return fn()
        reply: "queue.Queue[tuple[bool, Any]]" = queue.Queue(maxsize=1)
        with self._lock:
            if self._closed:
                raise RuntimeError("MainThreadExecutor is shut down")
            self._jobs.put((fn, reply))
        ok, value = reply.get()
        if ok:
            return value
        raise value

    def pump(self) -> None:
        """Run every pending job.  MUST be called only from the main thread."""
        while True:
            try:
                fn, reply = self._jobs.get_nowait()
            except queue.Empty:
                return
            try:
                reply.put((True, fn()))
            except BaseException as exc:  # noqa: BLE001 — relay to the waiting worker
                reply.put((False, exc))

    def shutdown(self) -> None:
        """Stop accepting work and release any waiting workers (main thread)."""
        with self._lock:
            self._closed = True
            # Drain UNDER the lock so submit() can't enqueue after this point.
            while True:
                try:
                    _fn, reply = self._jobs.get_nowait()
                except queue.Empty:
                    return
                reply.put((False, RuntimeError("MainThreadExecutor is shut down")))


# ---------------------------------------------------------------------------
# Bundle marshalling: wrap the native side-effect surfaces so the controller —
# which calls them from its transcribe worker — transparently runs them on the
# main thread.  hotkeys / autostart / permissions are NOT wrapped: they are armed
# once at startup or used only on the CLI path, never from the transcribe worker.
# ---------------------------------------------------------------------------


class _MarshalledClipboard:
    def __init__(self, real, executor: MainThreadExecutor) -> None:
        self._real = real
        self._x = executor

    def get_text(self):
        return self._x.submit(self._real.get_text)

    def set_text(self, text: str) -> None:
        self._x.submit(lambda: self._real.set_text(text))

    def has_nontext(self) -> bool:
        return self._x.submit(self._real.has_nontext)


class _MarshalledInjector:
    def __init__(self, real, executor: MainThreadExecutor) -> None:
        self._real = real
        self._x = executor

    def paste(self, token=None) -> bool:
        try:
            return self._x.submit(lambda: self._real.paste(token))
        except Exception:  # executor shut down mid-teardown — treat as "not pasted"
            return False

    def release_modifiers(self) -> None:
        try:
            self._x.submit(self._real.release_modifiers)
        except Exception:
            pass


class _MarshalledFocus:
    def __init__(self, real, executor: MainThreadExecutor) -> None:
        self._real = real
        self._x = executor

    def snapshot(self):
        return self._x.submit(self._real.snapshot)

    def unchanged(self, token) -> bool:
        try:
            return self._x.submit(lambda: self._real.unchanged(token))
        except Exception:  # executor shut down — fail safe to "changed" (-> COPIED)
            return False


def marshal_bundle(bundle: PlatformBundle, executor: MainThreadExecutor) -> PlatformBundle:
    """Return a copy of *bundle* whose clipboard / injector / focus run on the main
    thread via *executor*.  Everything else is left untouched."""
    return replace(
        bundle,
        clipboard=_MarshalledClipboard(bundle.clipboard, executor),
        injector=_MarshalledInjector(bundle.injector, executor),
        focus=_MarshalledFocus(bundle.focus, executor),
    )
