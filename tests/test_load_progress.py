#!/usr/bin/env python3
"""The loading dialog counts finished files: the loader publishes its batch only
when complete, so the image count stayed at 0 until the very end."""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


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

    def spy(gen):
        orig(gen)
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
