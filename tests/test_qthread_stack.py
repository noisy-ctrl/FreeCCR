#!/usr/bin/env python3
"""Every QThread gets a 16 MB stack (spec/qthread-stack-size.md).

macOS gives a QThread 512 KB unless told otherwise, and numpy's OpenBLAS
overflows that on any np.linalg.solve/inv (parallel LU keeps its job table
on the stack), so Convert All died with SIGBUS. These tests pin the fix:
nothing in src/ may create a QThread except through utils.qt_threads, and
the stack really is 16 MB on the running OS thread.
"""
import ast
import ctypes
import ctypes.util
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from utils.qt_threads import (STACK_BYTES, WorkerThread, new_thread,  # noqa: E402
                              with_big_stack)

HELPER = SRC / "utils" / "qt_threads.py"


def _is_qthread(node):
    return ast.unparse(node).split(".")[-1] == "QThread"


# --- 1. source guard ------------------------------------------------------

def _offences():
    bad = []
    for path in sorted(SRC.rglob("*.py")):
        if path == HELPER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(_is_qthread(b) for b in node.bases):
                bad.append(f"{rel}:{node.lineno} class {node.name}(QThread)")
            if isinstance(node, ast.Call) and _is_qthread(node.func):
                bad.append(f"{rel}:{node.lineno} QThread()")
    return bad


def test_no_qthread_created_outside_helper():
    bad = _offences()
    assert not bad, (
        "QThreads must come from utils.qt_threads (WorkerThread / new_thread) "
        "so they get a 16 MB stack; found:\n  " + "\n  ".join(bad))


def test_guard_sees_the_patterns_it_forbids():
    # The guard must actually recognise both forms, or test 1 proves nothing.
    sample = ast.parse("from PySide6 import QtCore\n"
                       "class A(QThread): pass\n"
                       "class B(QtCore.QThread): pass\n"
                       "t = QThread()\n")
    hits = [n for n in ast.walk(sample)
            if (isinstance(n, ast.ClassDef) and any(_is_qthread(b) for b in n.bases))
            or (isinstance(n, ast.Call) and _is_qthread(n.func))]
    assert len(hits) == 3


# --- 2. the app's workers -------------------------------------------------

def test_app_workers_are_worker_threads():
    from widgets import image_preview, live_render, sliders_panel
    for cls in (image_preview.SliceWorker, image_preview.HiResDetailWorker,
                image_preview.AutoFrameWorker, image_preview.ExportItemsWorker,
                live_render._LiveJob, sliders_panel.BWPointConvertWorker):
        assert issubclass(cls, WorkerThread), cls.__name__


# --- 3. the Qt-side setting -----------------------------------------------

def test_worker_thread_and_new_thread_set_the_stack():
    assert STACK_BYTES == 16 * 1024 * 1024
    assert WorkerThread().stackSize() == STACK_BYTES
    assert new_thread().stackSize() == STACK_BYTES


def test_larger_stack_is_left_alone():
    t = QThread()
    t.setStackSize(2 * STACK_BYTES)
    assert with_big_stack(t).stackSize() == 2 * STACK_BYTES


# --- 4. the OS thread really gets it --------------------------------------

def _running_thread_stack_bytes():
    """Stack size of the calling OS thread, or None where unsupported."""
    libc = ctypes.CDLL(ctypes.util.find_library("c") or None)
    libc.pthread_self.restype = ctypes.c_void_p
    me = ctypes.c_void_p(libc.pthread_self())
    if sys.platform == "darwin":
        libc.pthread_get_stacksize_np.restype = ctypes.c_size_t
        libc.pthread_get_stacksize_np.argtypes = [ctypes.c_void_p]
        return int(libc.pthread_get_stacksize_np(me))
    if sys.platform.startswith("linux"):
        attr = ctypes.create_string_buffer(256)  # >= sizeof(pthread_attr_t)
        if libc.pthread_getattr_np(me, attr) != 0:
            return None
        size = ctypes.c_size_t()
        try:
            if libc.pthread_attr_getstacksize(attr, ctypes.byref(size)) != 0:
                return None
        finally:
            libc.pthread_attr_destroy(attr)
        return int(size.value)
    return None


class _Probe(WorkerThread):
    def __init__(self):
        super().__init__()
        self.seen = None

    def run(self):
        self.seen = _running_thread_stack_bytes()


def test_running_worker_thread_has_the_big_stack():
    if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
        pytest.skip("no portable way to read a thread's stack size here")
    probe = _Probe()
    probe.start()
    assert probe.wait(10_000)
    assert probe.seen is not None
    assert probe.seen >= STACK_BYTES


# --- 5. the crashing call completes ---------------------------------------

def test_linalg_solve_and_inv_on_a_worker_thread():
    # In a child process so a regression shows up as a failed test rather
    # than taking the whole test run down with it.
    code = textwrap.dedent(f"""
        import os, sys
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, {str(SRC)!r})
        import numpy as np
        from PySide6.QtCore import QCoreApplication
        from utils.qt_threads import WorkerThread
        app = QCoreApplication([])
        out = {{}}
        class T(WorkerThread):
            def run(self):
                A = np.array([[2.0, 1.0], [1.0, 3.0]])
                out["solve"] = np.linalg.solve(A, np.array([1.0, 2.0])).tolist()
                out["inv"] = float(np.linalg.inv(np.eye(3) * 2.0)[0, 0])
        t = T(); t.start(); t.wait()
        assert abs(out["solve"][0] - 0.2) < 1e-12 and abs(out["solve"][1] - 0.6) < 1e-12
        assert out["inv"] == 0.5
        print("ok")
    """)
    preexec = None
    if sys.platform.startswith("linux"):
        # Make Linux behave like macOS: glibc sizes a new thread's default
        # stack from RLIMIT_STACK, so 512 KB here is what a QThread gets on a
        # Mac. Only the helper's explicit 16 MB can then keep the solve alive
        # (with numpy's OpenBLAS a bare QThread segfaults under this limit).
        import resource

        def preexec():
            hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
            soft = 512 * 1024
            if hard != resource.RLIM_INFINITY:
                soft = min(soft, hard)
            resource.setrlimit(resource.RLIMIT_STACK, (soft, hard))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, preexec_fn=preexec)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().endswith("ok")
