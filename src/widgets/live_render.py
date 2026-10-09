"""Live preview renderer for slider / curve / feather edits.

Renders on a worker thread, LATEST WINS: while a render is running, further
requests for the same image collapse into one pending request that starts, with
the settings of that moment, when the running one lands. The GUI thread only
snapshots the inputs and stores the finished pixels. See spec/slider-speed.md.
"""

import atexit
import os
import threading
import time
import weakref

from PySide6.QtCore import QObject, QThread, Signal

from core.ccr_backend import ccr_backend
from core.ccr_image import render_lock

# A drag switches to half-resolution drafts once a full render is slower than
# this (≈ 14 fps); faster machines never see a draft.
DRAFT_THRESHOLD_MS = 70.0
# Requests closer together than this count as one burst (a drag or held key).
BURST_WINDOW_S = 0.30
# Below this long side a draft saves too little to be worth the softness.
MIN_DRAFT_SOURCE = 400


def _timing_on() -> bool:
    return os.environ.get("FREECCR_RENDER_TIMING", "").strip() not in ("", "0")


# Every job that has been started and not yet reaped, so no QThread wrapper can
# be garbage-collected while its thread runs (that aborts the process).
_LIVE_JOBS = set()
_LIVE_JOBS_GUARD = threading.Lock()


def _wait_all_jobs():
    with _LIVE_JOBS_GUARD:
        jobs = list(_LIVE_JOBS)
    for j in jobs:
        j.wait()


atexit.register(_wait_all_jobs)


def _in_backend(img) -> bool:
    return any(x is img for x in ccr_backend.images)


class _LiveJob(QThread):
    done = Signal(object)          # payload dict, delivered on the GUI thread

    def __init__(self, img, snap, draft_long):
        super().__init__()
        self.img = img
        self.snap = snap
        self.draft_long = draft_long

    def run(self):
        t0 = time.perf_counter()
        result, err = None, None
        try:
            with render_lock(self.img):
                result = self.img.render_preview_pixels(self.snap,
                                                        draft_long=self.draft_long)
        except Exception as e:      # never let a render kill the thread silently
            err = e
        self.done.emit({"job": self, "result": result, "error": err,
                        "ms": (time.perf_counter() - t0) * 1000.0})


class LiveRenderer(QObject):
    """Owns the in-flight live job and the pending requests.

    `on_applied(img, draft)` is called on the GUI thread after a result has
    been stored on its image."""

    def __init__(self, parent=None, on_applied=None):
        super().__init__(parent)
        self._on_applied = on_applied
        self._job = None
        self._pending = {}             # img -> draft (False wins), insertion order
        # Weak-keyed so a removed image (and its base) is not kept alive here.
        self._req_epoch = weakref.WeakKeyDictionary()    # epoch at latest request
        self._draft_epoch = weakref.WeakKeyDictionary()  # epoch after a draft landed
        self.last_full_ms = None       # duration of the last FULL render
        self.renders = 0               # results applied (tests / profiling)

    # --- requests ---------------------------------------------------------
    def request(self, img, draft=False):
        self._req_epoch[img] = getattr(img, "_render_epoch", 0)
        if self._job is not None:
            # Latest wins: one pending entry per image; a full request is
            # never downgraded to a draft by a later one.
            self._pending[img] = self._pending.get(img, True) and bool(draft)
            return
        self._pending[img] = self._pending.get(img, True) and bool(draft)
        self._start_next()

    def cancel_pending(self, img=None):
        """Drop queued requests (for `img`, or all). An in-flight render is
        not interrupted; a synchronous render in the meantime voids it."""
        if img is None:
            self._pending.clear()
        else:
            self._pending.pop(img, None)

    def busy(self) -> bool:
        return self._job is not None or bool(self._pending)

    def needs_full(self, img) -> bool:
        """True unless a FULL render of `img` has been stored since its latest
        request (ours or a synchronous one) and nothing newer is queued or
        running. Any request made while a job runs is queued, so a late result
        from an older snapshot can never satisfy this."""
        if img in self._pending:
            return True
        if self._job is not None and self._job.img is img:
            # A full job in flight already carries the latest settings (any
            # later request would be pending); a draft needs a follow-up.
            return self._job.draft_long is not None
        if img not in self._req_epoch:
            return False
        cur = getattr(img, "_render_epoch", 0)
        return cur == self._req_epoch[img] or cur == self._draft_epoch.get(img)

    def shutdown(self):
        """Wait out the in-flight job (app close)."""
        self._pending.clear()
        if self._job is not None:
            self._job.wait()

    # --- internals ----------------------------------------------------------
    def _start_next(self):
        """Start the oldest pending request whose image is still loaded."""
        while self._job is None and self._pending:
            img = next(iter(self._pending))
            draft = self._pending.pop(img)
            if not _in_backend(img):
                continue
            snap = img.preview_render_snapshot()
            base = snap.get("base")
            if base is None:
                continue
            draft_long = None
            long_side = max(base.shape[:2])
            if draft and not snap["has_dust"] and long_side >= MIN_DRAFT_SOURCE:
                draft_long = long_side // 2
            job = _LiveJob(img, snap, draft_long)
            job.done.connect(self._on_done)
            with _LIVE_JOBS_GUARD:
                _LIVE_JOBS.add(job)
            self._job = job
            job.start()

    def _on_done(self, payload):
        job = payload["job"]
        job.wait()                      # run() has returned; reap the thread
        with _LIVE_JOBS_GUARD:
            _LIVE_JOBS.discard(job)
        if self._job is job:
            self._job = None
        img, res = job.img, payload["result"]
        if payload["error"] is not None:
            print(f"Live render failed: {payload['error']}")
        elif (res is not None and _in_backend(img)
              # a synchronous render landed meanwhile: it is at least as new
              and getattr(img, "_render_epoch", 0) == job.snap["epoch"]):
            img._apply_preview_pixels(res)
            self.renders += 1
            draft = bool(res.get("draft"))
            if draft:
                self._draft_epoch[img] = img._render_epoch
            else:
                self.last_full_ms = payload["ms"]
                self._draft_epoch.pop(img, None)
            if _timing_on():
                h, w = job.snap["base"].shape[:2]
                print(f"Live render: {payload['ms']:.0f} ms "
                      f"({'draft' if draft else 'full'}, {w}x{h})")
            if self._on_applied is not None:
                self._on_applied(img, draft)
        self._start_next()
