# QThread stack size (crash fix)

## Problem

On macOS the app died with `zsh: bus error` on **Convert All**, CPU-only
included. The three crash reports were identical:

- `EXC_BAD_ACCESS / SIGBUS`, "Thread stack size exceeded";
- crashed thread `BWPointConvertWorker`, stack region about 544 KB;
- frames `___chkstk_darwin ← libopenblas64_ dgetrf_parallel ← dgesv_64_ ←
  _umath_linalg solve1<double> ← … ← QThreadWrapper::run`.

A QThread with no explicit stack size gets the platform's default for a
secondary pthread, which is **512 KB on macOS** (8 MB on Linux, which is why
it never showed in development). numpy's bundled OpenBLAS sends every
`np.linalg.solve` / `np.linalg.inv` through its parallel LU, even for a 2×2
system, and `dgetrf_parallel` keeps a per-CPU job table on the stack that is
larger than 512 KB. Convert All reaches a 2×2 solve through the auto-crop
frame geometry (`auto_crop.py`), and the camera-profile maths
(`dcp_profile.py`) calls `inv` on decode, so other workers (hi-res zoom,
slices, export) were exposed to the same failure.

Reproduced on Linux with numpy 1.26.4 (OpenBLAS 0.3.23) by giving a QThread a
512 KB stack: a 2×2 `solve` segfaults; at 1 MB and above it runs. CPython's
own threads get 16 MB on macOS, so the loader's `ThreadPoolExecutor` threads
were never affected.

## Goals / non-goals

- Every QThread the app creates runs with a 16 MB stack, matching CPython's
  macOS thread stack.
- Not a goal: changing OpenBLAS threading (`OPENBLAS_NUM_THREADS`) or
  rewriting the individual solves. Either would hide this one call site and
  leave the next one exposed, and the thread count also governs the large
  matrix products in the render path.

## Design

`src/utils/qt_threads.py`:

- `STACK_BYTES = 16 MiB`;
- `with_big_stack(thread)` sets it before `start()` (Qt ignores a stack size
  set on a running thread);
- `WorkerThread(QThread)` sets it in `__init__`, used as the base of every
  QThread subclass: `SliceWorker`, `HiResDetailWorker`, `AutoFrameWorker`,
  `ExportItemsWorker`, `_LiveJob`, `BWPointConvertWorker`;
- `new_thread(parent=None)` replaces a bare `QThread()` for the
  worker-object pattern: the loader, merge-replace and tether threads in
  `main_window.py`, and the three dust threads in `dust_panel.py`.

The 16 MB is reserved address space, not memory: pages are committed only as
the stack grows, so a dozen threads cost nothing measurable.

## Test plan (`tests/test_qthread_stack.py`)

1. Source guard (AST): no class in `src/` subclasses `QThread` directly and
   nothing calls `QThread()` outside `utils/qt_threads.py`, so a new worker
   cannot silently fall back to 512 KB.
2. Every worker class named above is a `WorkerThread`.
3. `WorkerThread().stackSize()` and `new_thread().stackSize()` equal
   `STACK_BYTES`; an existing larger size is left alone.
4. The real OS thread gets the stack: inside `run()`, read the running
   thread's stack size (`pthread_getattr_np` on Linux,
   `pthread_get_stacksize_np` on macOS) and require at least 16 MB.
5. In a subprocess, `np.linalg.solve` and `np.linalg.inv` complete inside a
   `WorkerThread`.
