#!/usr/bin/env python3
"""
Tests for multi-select duplicate/remove: instant duplication with full state
copy and independence, multi-removal, and catalog round trip of duplicates.
"""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import cv2  # noqa: E402
from core import catalog  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402


def _scan_png(tmp_path, name="negative.png", w=600, h=400, seed=21):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 20000 + 25000 * (xx / w) + 10000 * (yy / h)
    img = np.stack([base * 1.2, base, base * 0.7], axis=-1)
    img += rng.normal(0, 1500, img.shape)
    img = np.clip(img, 1000, 64000).astype(np.uint16)
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path, img


class TestDuplicate:
    def test_duplicate_copies_full_state(self, tmp_path):
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.reference_frame = (20, 20, 580, 380)
        ccr_backend.images = [img]
        ccr_backend.file_paths = [path]
        ccr_backend.convert_negative_by_index(0)
        img.adjustment_settings = {"temperature": 15}
        img.crop_rect = (0.1, 0.1, 0.9, 0.9)
        img.crop_angle = 2.0

        created = ccr_backend.duplicate_images_by_indices([0])
        assert created == 1
        assert ccr_backend.get_image_count() == 2
        dup = ccr_backend.images[1]  # inserted right after the source
        assert dup.display_name == "negative_copy1.png"
        assert dup.converted
        assert dup.conversion_inputs == img.conversion_inputs
        assert dup.adjustment_settings == {"temperature": 15}
        assert dup.crop_rect == (0.1, 0.1, 0.9, 0.9)
        assert dup.crop_angle == 2.0
        assert dup.reference_frame == img.reference_frame
        np.testing.assert_array_equal(dup.resized_raw, img.resized_raw)
        assert ccr_backend.file_paths == [path, path]

    def test_duplicate_is_independent(self, tmp_path):
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        img.adjustment_settings = {"contrast": 5}
        ccr_backend.images = [img]
        ccr_backend.duplicate_images_by_indices([0])
        dup = ccr_backend.images[1]
        # Mutating the copy must not touch the original (and vice versa)
        dup.adjustment_settings["contrast"] = 99
        dup.crop_rect = (0.2, 0.2, 0.8, 0.8)
        dup.resized_raw[0, 0] = 0
        assert img.adjustment_settings == {"contrast": 5}
        assert img.crop_rect is None
        # The dup pixel write must not alias the original (scan pixels >= 1000)
        assert img.resized_raw[0, 0, 0] != 0
        assert dup.resized_raw.base is None
        assert dup.resized_raw is not img.resized_raw

    def test_duplicate_names_unique(self, tmp_path):
        path, _ = _scan_png(tmp_path)
        ccr_backend.images = [CCRImage(path)]
        ccr_backend.duplicate_images_by_indices([0])
        ccr_backend.duplicate_images_by_indices([0])
        names = [im.display_name for im in ccr_backend.images]
        assert names[0] is None  # original keeps file basename
        assert sorted(n for n in names if n) == \
               ["negative_copy1.png", "negative_copy2.png"]

    def test_duplicate_multiple_selection(self, tmp_path):
        path_a, _ = _scan_png(tmp_path, name="a.png")
        path_b, _ = _scan_png(tmp_path, name="b.png")
        ccr_backend.images = [CCRImage(path_a), CCRImage(path_b)]
        created = ccr_backend.duplicate_images_by_indices([0, 1])
        assert created == 2
        assert ccr_backend.get_image_count() == 4
        # Each copy sits right after its source
        basenames = [os.path.basename(im.file_path) for im in ccr_backend.images]
        assert basenames == ["a.png", "a.png", "b.png", "b.png"]

    def test_duplicates_survive_catalog_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        img = CCRImage(path)
        ccr_backend.images = [img]
        ccr_backend.duplicate_images_by_indices([0])
        ccr_backend.images[1].adjustment_settings = {"tint": 7}
        catalog.update_for_images(ccr_backend.images, path=cat)
        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 2
        assert restored[1].display_name == "negative_copy1.png"
        # with the sharpening it rendered with written down (spec/details-defaults.md)
        from core import detail_defaults
        assert restored[1].adjustment_settings == detail_defaults.written_down({"tint": 7})


class TestRemoveMultiple:
    def test_remove_indices(self, tmp_path):
        paths = []
        for name in ("a.png", "b.png", "c.png", "d.png"):
            p, _ = _scan_png(tmp_path, name=name)
            paths.append(p)
        ccr_backend.images = [CCRImage(p) for p in paths]
        removed = ccr_backend.remove_images_by_indices([0, 2])
        assert removed == 2
        remaining = [os.path.basename(im.file_path) for im in ccr_backend.images]
        assert remaining == ["b.png", "d.png"]
        assert len(ccr_backend.file_paths) == 2

    def test_next_selection_target(self):
        from widgets.thumbnail_list import ThumbnailList
        target = ThumbnailList._next_selection_target
        # Removing index 1 of (then) 4: the old index-2 image now sits at 1
        assert target([1], 3) == 1
        # Removing the last image: land on the new last one
        assert target([2], 2) == 1
        # Multi-removal: land where the first removed image was
        assert target([0, 2], 2) == 0
        assert target([1, 3], 2) == 1
        # Removing the tail block clamps to the new last image
        assert target([2, 3], 2) == 1
        # Everything removed: nothing to select
        assert target([0], 0) is None

    def test_remove_out_of_range_ignored(self, tmp_path):
        p, _ = _scan_png(tmp_path)
        ccr_backend.images = [CCRImage(p)]
        assert ccr_backend.remove_images_by_indices([5, -1]) == 0
        assert ccr_backend.get_image_count() == 1

    def test_removed_duplicate_entry_is_deleted(self, tmp_path, monkeypatch):
        """Removing a DUPLICATE deletes its catalog entry: a discarded copy
        must not resurrect on the next open."""
        cat = str(tmp_path / "catalog.json")
        monkeypatch.setattr(catalog, "default_catalog_path", lambda: cat)
        path, _ = _scan_png(tmp_path)
        ccr_backend.images = [CCRImage(path)]
        ccr_backend._catalog_preserved = {}
        ccr_backend.duplicate_images_by_indices([0])
        catalog.update_for_images(ccr_backend.images, path=cat)
        entries = catalog.entries_for_path(path, path=cat)
        assert len(entries) == 2
        ccr_backend.remove_images_by_indices([1])  # remove only the dup
        entries = catalog.entries_for_path(path, path=cat)
        assert len(entries) == 1
        assert not entries[0]["is_duplicate"]

    def test_removed_actual_keeps_catalog_info(self, tmp_path, monkeypatch):
        """Removing an ACTUAL image keeps its stored edits — even across a
        later save_catalog (the preserved state is merged back)."""
        cat = str(tmp_path / "catalog.json")
        monkeypatch.setattr(catalog, "default_catalog_path", lambda: cat)
        path_a, _ = _scan_png(tmp_path, name="a.png")
        path_b, _ = _scan_png(tmp_path, name="b.png")
        img_a = CCRImage(path_a)
        img_a.adjustment_settings = {"temperature": 33}
        ccr_backend.images = [img_a, CCRImage(path_b)]
        ccr_backend.file_paths = [path_a, path_b]
        ccr_backend._catalog_preserved = {}
        catalog.update_for_images(ccr_backend.images, path=cat)
        # Remove the actual image a.png, then save (as close/convert would)
        ccr_backend.remove_images_by_indices([0])
        ccr_backend.save_catalog()
        entries = catalog.entries_for_path(path_a, path=cat)
        assert entries is not None
        from core import detail_defaults
        assert entries[0]["adjustment_settings"] == \
            detail_defaults.written_down({"temperature": 33})

    def test_remove_original_and_duplicate_keeps_only_original(
            self, tmp_path, monkeypatch):
        cat = str(tmp_path / "catalog.json")
        monkeypatch.setattr(catalog, "default_catalog_path", lambda: cat)
        path, _ = _scan_png(tmp_path)
        ccr_backend.images = [CCRImage(path)]
        ccr_backend._catalog_preserved = {}
        ccr_backend.duplicate_images_by_indices([0])
        catalog.update_for_images(ccr_backend.images, path=cat)
        ccr_backend.remove_images_by_indices([0, 1])
        ccr_backend.save_catalog()
        entries = catalog.entries_for_path(path, path=cat)
        assert entries is not None and len(entries) == 1
        assert not entries[0]["is_duplicate"]

    def test_duplicate_flag_round_trips_catalog(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path, _ = _scan_png(tmp_path)
        ccr_backend.images = [CCRImage(path)]
        ccr_backend.duplicate_images_by_indices([0])
        assert ccr_backend.images[1].is_duplicate
        catalog.update_for_images(ccr_backend.images, path=cat)
        restored = catalog.create_images_for_path(path, path=cat)
        assert [im.is_duplicate for im in restored] == [False, True]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
