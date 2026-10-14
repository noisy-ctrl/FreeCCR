# Load progress that moves

## Problem

The loading dialog showed "(0/N)" for most of a load. The batch is only
published when complete, so the dialog counts finished files from the
loader (`get_load_progress`). But files load in PARALLEL (up to 8 at once),
so they finish in waves: with N ≤ 8 files the count sits at 0 until all are
almost done. A 3-way merge is worse: each frame is three RAW decodes, so the
first wave of up to eight frames is three decodes long (measured: two merged
frames, 0/2 for 48 s on a cold disk, then both at once).

## Fix

Count files part-way through as well:

- `core/load_progress.py`: a thread-local callback. The loader binds one per
  file on its worker thread (`with load_progress.bound(cb):`), and the decode
  reports milestones with `load_progress.step(fraction)`. Nothing bound (zoom,
  export, slicing, any other decode) = a no-op.
- Milestones: camera info read 0.05; RAW file read 0.30; decoded 0.70; read
  and resized 0.80; preview made 0.95; for a merge, each of its three RAWs
  decoded 0.28 / 0.52 / 0.75. Steps only move forward (max).
- Backend: `_file_step(gen, token, f)` keeps each unfinished file's fraction
  (capped below 1; dropped when the file is counted done; ignored from a stale
  load generation); `get_load_fraction()` = (done + Σ part-way) / total.
- Dialog: a thin progress bar under the text, driven by the fraction (never
  moving backwards); the text says "(n/N done)", and "Merging RGB frames"
  in 3-way merge mode.

The count is unchanged; the bar is what moves during a wave.

## Tests

- `step` reaches only the bound thread, nests, and is a no-op unbound.
- Fraction: part-way files count, never backwards, a finished file counts
  once, a stale generation is ignored, a new load starts clean.
- A real load reports increasing steps for every file before it finishes and
  ends at 1.0; a merge reports one step per RAW.
- The dialog's bar follows the fraction and never goes back.
