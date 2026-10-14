"""Progress WITHIN one file's load (spec/load-progress.md).

Files load in parallel, so they finish in waves: with eight decoding at once
(or eight 3-way merges, three RAWs each) the "done" count can sit at 0 for the
whole first wave. The loader binds a callback on its worker thread; the
decode reports milestones (file read, decoded, preview made, each RAW of a
merge) as the fraction of that file done. Anything decoding on a thread with
nothing bound (zoom, export, slicing) reports to no one: `step` is a no-op.
"""

import threading

_local = threading.local()


class bound:
    """`with bound(callback):` route this thread's steps to `callback(f)`."""

    def __init__(self, callback):
        self.callback = callback

    def __enter__(self):
        self._prev = getattr(_local, "callback", None)
        _local.callback = self.callback
        return self

    def __exit__(self, *exc):
        _local.callback = self._prev
        return False


def step(fraction):
    """This thread's file is `fraction` (0..1) loaded."""
    cb = getattr(_local, "callback", None)
    if cb is None:
        return
    try:
        cb(float(fraction))
    except Exception:            # progress must never break a load
        pass
