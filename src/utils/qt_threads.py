"""Background QThreads with a stack big enough for numpy's linear algebra.

macOS gives a secondary thread 512 KB of stack, and a QThread takes that
default unless it is told otherwise. numpy's bundled OpenBLAS solves even a
2x2 system through its parallel LU (``dgetrf_parallel``, behind
``np.linalg.solve`` and ``np.linalg.inv``), which keeps a per-CPU job table
on the stack that is larger than 512 KB. Any such call on a QThread
overflowed the stack and the OS killed the app with SIGBUS ("Thread stack
size exceeded") -- first seen on Convert All, inside BWPointConvertWorker.

Python's own threads get 16 MB on macOS, which is why the same maths was
always fine on the loader's ThreadPoolExecutor threads. Every QThread in the
app therefore asks for the same 16 MB. That is address space, not memory:
pages are only committed as the stack actually grows.

Use ``WorkerThread`` as the base of a QThread subclass, and ``new_thread()``
in place of a bare ``QThread()`` for the worker-object pattern.
``tests/test_qthread_stack.py`` fails if a QThread is created any other way.
See spec/qthread-stack-size.md.
"""

from PySide6.QtCore import QThread

# Matches CPython's own thread stack on macOS (THREAD_STACK_SIZE 0x1000000).
STACK_BYTES = 16 * 1024 * 1024


def with_big_stack(thread):
    """Give ``thread`` at least STACK_BYTES of stack. Must run before start();
    Qt ignores a stack size set on a running thread. Returns ``thread``."""
    current = thread.stackSize()  # 0 = the platform default
    if current == 0 or current < STACK_BYTES:
        thread.setStackSize(STACK_BYTES)
    return thread


def new_thread(parent=None):
    """A plain QThread (for moveToThread workers) with the big stack."""
    thread = QThread(parent) if parent is not None else QThread()
    return with_big_stack(thread)


class WorkerThread(QThread):
    """QThread base class that sets the big stack on construction."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with_big_stack(self)
