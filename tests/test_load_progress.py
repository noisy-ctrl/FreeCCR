#!/usr/bin/env python3
"""The loading dialog counts finished files: the loader publishes its batch only
when complete, so the image count stayed at 0 until the very end. And files
load in parallel, so they finish in waves: the bar also counts files part-way
through (spec/load-progress.md)."""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

# A load on this (GUI) thread makes QPixmaps: needs the app, also when this
# file runs on its own.
_app = QApplication.instance() or QApplication(sys.argv[:1])


def test_progress_counts_files_as_they_finish(tmp_path, monkeypatch):
    import cv2
    from core.ccr_backend import ccr_backend
    import core.catalog as catalog
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {})
    paths = []
    for i in range(4):
        p = str(tmp_path / f"f{i}.png")
        cv2.imwrite(p, np.full((30, 40, 3), 9000 + i, np.uint16))
        paths.append(p)
    seen = []
    orig = ccr_backend._bump_load_progress

    def spy(gen, token=None):
        orig(gen, token)
        seen.append(ccr_backend.get_load_progress())
    monkeypatch.setattr(ccr_backend, "_bump_load_progress", spy)
    saved = (ccr_backend.images, ccr_backend.file_paths, ccr_backend.rgb_merge_mode)
    try:
        ccr_backend.rgb_merge_mode = False
        ccr_backend.load_images_from_files(paths)
        assert [d for d, _t in seen] == [1, 2, 3, 4]
        assert all(t == 4 for _d, t in seen)
        assert ccr_backend.get_load_progress() == (4, 4)
    finally:
        ccr_backend.images, ccr_backend.file_paths, ccr_backend.rgb_merge_mode = saved


def test_stale_loader_cannot_move_a_newer_count():
    from core.ccr_backend import ccr_backend
    ccr_backend._load_generation += 1
    old = ccr_backend._load_generation
    ccr_backend._set_load_progress(old, 0, 9)
    ccr_backend._load_generation += 1            # a newer load started
    new = ccr_backend._load_generation
    ccr_backend._set_load_progress(new, 0, 3)
    ccr_backend._bump_load_progress(old)         # the abandoned thread finishes a file
    assert ccr_backend.get_load_progress() == (0, 3)
    ccr_backend._bump_load_progress(new)
    assert ccr_backend.get_load_progress() == (1, 3)
    ccr_backend.reset_load_progress()
    assert ccr_backend.get_load_progress() == (0, 0)


# --- progress within a file (spec/load-progress.md) ------------------------------

def test_step_reaches_only_the_bound_thread():
    import threading
    from core import load_progress
    got, other = [], []
    load_progress.step(0.5)                      # nothing bound: a no-op
    with load_progress.bound(got.append):
        load_progress.step(0.3)
        t = threading.Thread(target=lambda: load_progress.step(0.9))
        t.start(); t.join()                      # another thread reports to no one
        with load_progress.bound(other.append):
            load_progress.step(0.6)
        load_progress.step(0.7)                  # the outer binding is back
    load_progress.step(0.8)
    assert got == [0.3, 0.7] and other == [0.6]


def test_fraction_counts_files_part_way_through():
    from core.ccr_backend import ccr_backend
    ccr_backend._load_generation += 1
    gen = ccr_backend._load_generation
    ccr_backend._set_load_progress(gen, 0, 4)
    assert ccr_backend.get_load_fraction() == 0.0
    ccr_backend._file_step(gen, "a", 0.5)
    ccr_backend._file_step(gen, "b", 0.25)
    ccr_backend._file_step(gen, "b", 0.1)        # never goes backwards
    assert ccr_backend.get_load_fraction() == (0.5 + 0.25) / 4
    ccr_backend._bump_load_progress(gen, "a")    # a finished: counted once, as done
    assert ccr_backend.get_load_progress() == (1, 4)
    assert ccr_backend.get_load_fraction() == (1 + 0.25) / 4
    ccr_backend._file_step(gen - 1, "c", 0.9)    # a stale loader: ignored
    assert ccr_backend.get_load_fraction() == (1 + 0.25) / 4
    ccr_backend._set_load_progress(gen, 0, 2)    # a new count starts clean
    assert ccr_backend.get_load_fraction() == 0.0
    ccr_backend.reset_load_progress()


def test_a_real_load_reports_steps_before_each_file_finishes(tmp_path, monkeypatch):
    import cv2
    from core.ccr_backend import ccr_backend
    import core.catalog as catalog
    monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {})
    paths = []
    for i in range(3):
        p = str(tmp_path / f"g{i}.png")
        cv2.imwrite(p, np.full((30, 40, 3), 9000 + i, np.uint16))
        paths.append(p)
    steps = []
    orig = ccr_backend._file_step

    def spy(gen, token, f):
        orig(gen, token, f)
        steps.append((token, f))
    monkeypatch.setattr(ccr_backend, "_file_step", spy)
    saved = (ccr_backend.images, ccr_backend.file_paths, ccr_backend.rgb_merge_mode)
    try:
        ccr_backend.rgb_merge_mode = False
        ccr_backend.load_images_from_files(paths)
        for p in paths:
            mine = [f for t, f in steps if t == p]
            assert mine and mine == sorted(mine) and mine[-1] >= 0.8   # read, preview
        assert ccr_backend.get_load_fraction() == 1.0
    finally:
        ccr_backend.images, ccr_backend.file_paths, ccr_backend.rgb_merge_mode = saved


def test_a_merge_reports_each_of_its_three_raws(monkeypatch):
    from core import ccr_merge, load_progress
    monkeypatch.setattr(ccr_merge.os.path, "exists", lambda p: True)
    monkeypatch.setattr(ccr_merge, "_decode_frame_plane",
                        lambda path, pos, preview, demosaic=False, mono=False:
                        (np.full((4, 6), 100, np.uint16), 16383.0, False, (8, 12)))
    got = []
    with load_progress.bound(got.append):
        ccr_merge.merge_raw_channels(["r.raf", "g.raf", "b.raf"], preview=True)
    assert len(got) == 3 and got == sorted(got) and got[-1] <= 0.8


def test_dialog_bar_follows_the_fraction(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from core.ccr_backend import ccr_backend
    from widgets.thumbnail_list import LoadingDialog
    monkeypatch.setattr(ccr_backend, "get_load_progress", lambda: (2, 8))
    monkeypatch.setattr(ccr_backend, "get_load_fraction", lambda: 0.4)
    d = LoadingDialog()
    d.update_dots()
    assert d.bar.value() == 400 and "(2/8 done)" in d.label.text()
    monkeypatch.setattr(ccr_backend, "get_load_fraction", lambda: 0.3)
    d.update_dots()
    assert d.bar.value() == 400                  # the bar never goes back
    d.timer.stop()
