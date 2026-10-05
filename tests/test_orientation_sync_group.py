#!/usr/bin/env python3
"""Tests for the "Orientation" setting group — the coarse 90-degree rotation
and the mirror flags carried by Sync to All.
See spec/orientation-sync-group.md."""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QWidget,  # noqa: E402
                               QVBoxLayout)

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from widgets.sliders_panel import (SlidersPanel, SyncSettingsDialog,  # noqa: E402
                                   SYNC_GROUPS)


# --- harness -----------------------------------------------------------------

def _ccr_image(tmp_path, name="scan.png"):
    """A CCRImage from a tiny uniform on-disk scan so __init__ succeeds."""
    path = str(tmp_path / name)
    base = np.full((40, 60, 3), 20000, np.uint16)
    cv2.imwrite(path, cv2.cvtColor(base, cv2.COLOR_RGB2BGR))
    img = CCRImage(path)
    img.converted = True
    img.adjustment_settings = {}
    return img


class _ImagePreviewStub:
    def __init__(self):
        self.updated = []

    def update_preview(self, idx):
        self.updated.append(idx)


class _Host(QWidget):
    """parent().parent() target carrying image_preview (as MainWindow does)."""
    def __init__(self):
        super().__init__()
        self.image_preview = _ImagePreviewStub()
        self.mid = QWidget(self)


_HOSTS = []


def _panel():
    host = _Host()
    _HOSTS.append(host)               # keep alive for the test's lifetime
    mid_layout = QVBoxLayout(host.mid)
    panel = SlidersPanel(host.mid)
    mid_layout.addWidget(panel)
    return panel


def _set(panel, key, value):
    panel.sliders[panel.adjustment_keys.index(key)].setValue(value)


def _orient(img):
    return (img.rotation_angle, img.horizontal_mirrored, img.vertical_mirrored)


def _set_orient(img, rotation, hflip=False, vflip=False):
    img.rotation_angle = rotation
    img.horizontal_mirrored = hflip
    img.vertical_mirrored = vflip


def _two_images(tmp_path):
    src = _ccr_image(tmp_path, "src.png")
    tgt = _ccr_image(tmp_path, "tgt.png")
    ccr_backend.images = [src, tgt]
    ccr_backend.file_paths = [src.file_path, tgt.file_path]
    return src, tgt


# --- the group itself ---------------------------------------------------------

class TestGroupRegistration:
    def test_orientation_follows_crop(self):
        ids = [gid for gid, _l, _k in SYNC_GROUPS]
        assert "orientation" in ids
        assert ids[ids.index("crop") + 1] == "orientation"

    def test_carries_no_adjustment_keys(self):
        """Whole-image group, like crop/profile/curves — so it cannot disturb
        the SYNC_GROUPS-partitions-ADJUSTMENT_KEYS invariant."""
        keys = dict((gid, k) for gid, _l, k in SYNC_GROUPS)["orientation"]
        assert keys == ()

    def test_offered_by_sync_dialog(self):
        dlg = SyncSettingsDialog(None, None)
        assert "orientation" in dlg._checkboxes
        assert dlg.selection()["orientation"] is True   # checked by default


# (Copy/Paste of rotation, flips and fine rotation is covered by
# tests/test_paste_settings_dialog.py — spec/paste-settings-dialog.md.)


class TestSyncToAll:
    def test_orientation_lands_on_every_image(self, tmp_path):
        src, tgt = _two_images(tmp_path)
        third = _ccr_image(tmp_path, "third.png")
        ccr_backend.images.append(third)
        ccr_backend.file_paths.append(third.file_path)
        _set_orient(src, 90, hflip=True)
        _set_orient(tgt, 180)

        panel = _panel()
        panel.current_idx = 0
        panel._sync_group_selection = {"orientation": True}
        panel._perform_sync_to_all()

        assert _orient(tgt) == (90, True, False)
        assert _orient(third) == (90, True, False)

    def test_matching_images_push_no_undo_state(self, tmp_path):
        src, tgt = _two_images(tmp_path)
        _set_orient(src, 90)
        _set_orient(tgt, 90)

        panel = _panel()
        panel.current_idx = 0
        panel._sync_group_selection = {"orientation": True}
        panel._perform_sync_to_all()

        assert tgt.undo_stack == []      # no dead snapshot for an unchanged image

    def test_sync_leaves_other_groups_alone(self, tmp_path):
        src, tgt = _two_images(tmp_path)
        _set_orient(src, 90)
        src.color_profile = "bw"
        src.crop_rect = (0.1, 0.1, 0.9, 0.9)
        src.adjustment_settings = {"balance_r": 40}
        tgt.color_profile = "color"
        tgt.crop_rect = None
        tgt.adjustment_settings = {"balance_r": -25}

        panel = _panel()
        panel.current_idx = 0
        panel._sync_group_selection = {"orientation": True}
        panel._perform_sync_to_all()

        assert _orient(tgt) == (90, False, False)
        assert tgt.color_profile == "color"
        assert tgt.crop_rect is None
        assert tgt.adjustment_settings["balance_r"] == -25

    def test_orientation_only_sync_does_not_reprocess(self, tmp_path):
        """Rotation/mirroring are display-level transforms applied at paint
        time — they must not trigger a re-render of the preview buffers."""
        src, tgt = _two_images(tmp_path)
        _set_orient(src, 90)
        tgt.update_thumbnail_and_preview()
        before = tgt.histogram_data.tobytes()

        reprocessed = []
        tgt.update_thumbnail_and_preview = lambda: reprocessed.append(True)

        panel = _panel()
        panel.current_idx = 0
        panel._sync_group_selection = {"orientation": True}
        panel._perform_sync_to_all()

        assert reprocessed == []
        assert tgt.histogram_data.tobytes() == before

    def test_fine_rotation_is_never_synced(self, tmp_path):
        """The micro-rotation is measured from each frame's OWN film edge;
        copying it across frames would mis-straighten them. A user-set
        straighten travels under the crop group, as the crop angle."""
        src, tgt = _two_images(tmp_path)
        _set_orient(src, 90)
        src.fine_rotation_angle = 123
        tgt.fine_rotation_angle = -45

        panel = _panel()
        panel.current_idx = 0
        panel._sync_group_selection = {"orientation": True}
        panel._perform_sync_to_all()

        assert tgt.fine_rotation_angle == -45
