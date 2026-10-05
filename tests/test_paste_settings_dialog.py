#!/usr/bin/env python3
"""Tests for Copy / Paste Settings: copy snapshots everything, and the paste
dialog lists (and applies) only what differs from default on the copied
image — one tick per slider, plus single ticks for curves, the Subtractive
Saturations bands, crop, 90° rotation, each flip and the fine rotation.
See spec/paste-settings-dialog.md."""

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
from core.ccr_processor import BAND_ADJUSTMENT_KEYS  # noqa: E402
from widgets.sliders_panel import (SlidersPanel, SyncSettingsDialog,  # noqa: E402
                                   PasteSettingsDialog, paste_options,
                                   adjustment_label, PASTE_SECTIONS,
                                   SYNC_GROUPS)


# --- harness -----------------------------------------------------------------

def _ccr_image(tmp_path, name="scan.png"):
    path = str(tmp_path / name)
    base = np.full((40, 60, 3), 20000, np.uint16)
    cv2.imwrite(path, cv2.cvtColor(base, cv2.COLOR_RGB2BGR))
    img = CCRImage(path)
    img.converted = True
    img.adjustment_settings = {}
    return img


def _images(tmp_path, n=2):
    imgs = [_ccr_image(tmp_path, f"img{i}.png") for i in range(n)]
    ccr_backend.images = imgs
    ccr_backend.file_paths = [i.file_path for i in imgs]
    return imgs


class _ImagePreviewStub:
    def __init__(self):
        self.updated = []
        self.zoom_resets = 0

    def update_preview(self, idx):
        self.updated.append(idx)

    def _reset_zoom(self):
        self.zoom_resets += 1


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.image_preview = _ImagePreviewStub()
        self.mid = QWidget(self)


_HOSTS = []


def _panel():
    host = _Host()
    _HOSTS.append(host)
    QVBoxLayout(host.mid).addWidget(panel := SlidersPanel(host.mid))
    return panel


def _set(panel, key, value):
    panel.sliders[panel.adjustment_keys.index(key)].setValue(value)


def _slider(panel, key):
    return panel.sliders[panel.adjustment_keys.index(key)].value()


def _val(img, key):
    return img.adjustment_settings.get(key)


def _paste(panel, monkeypatch, chosen=None, accepted=True, indices=None,
           seen=None):
    """Run a paste with the modal stubbed. chosen=None keeps the dialog's own
    ticks (everything offered, minus remembered un-ticks); a set overrides.
    `seen` collects the dialog instances for inspection."""
    def _exec(self):
        if seen is not None:
            seen.append(self)
        return QDialog.Accepted if accepted else QDialog.Rejected

    monkeypatch.setattr(PasteSettingsDialog, "exec_", _exec, raising=False)
    if chosen is not None:
        monkeypatch.setattr(PasteSettingsDialog, "selection",
                            lambda self: set(chosen))
    if indices is None:
        panel.paste_adjustment_settings()
    else:
        panel.paste_settings_to_indices(indices)


def _default(key):
    return SlidersPanel.SLIDER_DEFAULTS.get(key, 0)


def _clip(**over):
    adj = {k: _default(k) for k in SlidersPanel.ADJUSTMENT_KEYS}
    adj.update(over.pop("adj", {}))
    clip = {"adjustments": adj, "curves": None, "cineon_log": False,
            "profile": "color", "crop": (None, 0.0), "rotation": 0,
            "flip_h": False, "flip_v": False, "fine_rotation": 0}
    clip.update(over)
    return clip


# --- paste_options (pure) -----------------------------------------------------

class TestPasteOptions:
    def test_all_default_offers_nothing(self):
        assert paste_options(_clip(), _default) == []
        assert paste_options(None, _default) == []

    def test_only_changed_sliders_listed_with_values(self):
        rows = paste_options(_clip(adj={"saturation": 12, "vibrance": -5}), _default)
        ids = [r[1] for r in rows]
        assert ids == ["adj:saturation", "adj:vibrance"]
        assert rows[0][2].startswith("Saturation") and "+12" in rows[0][2]
        assert "-5" in rows[1][2]

    def test_band_keys_collapse_to_one_row(self):
        k1, k2 = list(BAND_ADJUSTMENT_KEYS)[:2]
        rows = paste_options(_clip(adj={k1: 10, k2: -20}), _default)
        assert [r[1] for r in rows] == ["bands"]

    def test_band_feather_default_is_not_a_change(self):
        assert paste_options(_clip(adj={"band_feather": 10}), _default) == []
        assert [r[1] for r in paste_options(_clip(adj={"band_feather": 30}), _default)] \
            == ["bands"]

    def test_geometry_rows(self):
        clip = _clip(crop=((0.1, 0.2, 0.6, 0.9), 1.5), rotation=90,
                     flip_h=True, flip_v=False, fine_rotation=-40)
        rows = paste_options(clip, _default)
        ids = [r[1] for r in rows]
        assert ids == ["crop", "rotation", "flip_h", "fine_rotation"]
        crop_label = rows[0][2]
        assert "50%" in crop_label and "70%" in crop_label and "+1.5°" in crop_label
        assert "90°" in rows[1][2]
        assert "-0.40°" in rows[3][2]

    def test_sections_in_order(self):
        clip = _clip(adj={"temperature": 5, "ch_r_gain": 3, "balance_g": 2},
                     curves={"rgb": [[0, 0], [128, 150], [255, 255]]},
                     cineon_log=True, profile="bw", rotation=180)
        sections = [r[0] for r in paste_options(clip, _default)]
        order = [PASTE_SECTIONS.index(s) for s in sections]
        assert order == sorted(order)
        assert {"Adjustments", "Channel Levels", "Channel Balance",
                "Colour", "Geometry"} <= set(sections)

    def test_labels(self):
        assert adjustment_label("ch_r_gain") == "Red Gain"
        assert adjustment_label("balance_b") == "Balance Blue"
        assert adjustment_label("sub_saturation") == "Subtracted Sat"


# --- the dialog -------------------------------------------------------------

class TestDialog:
    OPTS = [("Adjustments", "adj:contrast", "Contrast (+5)"),
            ("Geometry", "crop", "Crop"),
            ("Geometry", "flip_h", "Flip horizontal")]

    def test_rows_ticked_by_default(self):
        dlg = PasteSettingsDialog(None, self.OPTS)
        assert dlg.windowTitle() == "Paste Settings"
        assert dlg.selection() == {"adj:contrast", "crop", "flip_h"}

    def test_remembered_unticks_seed_the_dialog(self):
        dlg = PasteSettingsDialog(None, self.OPTS, unticked={"crop"})
        assert dlg.selection() == {"adj:contrast", "flip_h"}

    def test_select_and_deselect_all(self):
        dlg = PasteSettingsDialog(None, self.OPTS)
        dlg._set_all(False)
        assert dlg.selection() == set()
        dlg._set_all(True)
        assert len(dlg.selection()) == 3

    def test_sync_dialog_unchanged(self):
        dlg = SyncSettingsDialog(None, None)
        assert dlg.windowTitle() == "Sync to All"
        assert list(dlg._checkboxes) == [gid for gid, _l, _k in SYNC_GROUPS]


# --- copy -----------------------------------------------------------------------

class TestCopy:
    def test_no_image_shows_hint(self):
        panel = _panel()
        panel.current_idx = None
        panel.copy_adjustment_settings()
        assert panel.clipboard is None
        assert "No image selected" in panel.hint_label.text()

    def test_snapshot_of_current_image(self, tmp_path):
        src, _tgt = _images(tmp_path)
        src.crop_rect, src.crop_angle = (0.1, 0.1, 0.9, 0.9), 2.0
        src.rotation_angle, src.vertical_mirrored = 270, True
        src.fine_rotation_angle = 0
        panel = _panel()
        panel.current_idx = 0
        _set(panel, "saturation", 15)
        panel.copy_adjustment_settings()
        clip = panel.clipboard
        assert clip["adjustments"]["saturation"] == 15
        assert set(clip["adjustments"]) == set(SlidersPanel.ADJUSTMENT_KEYS)
        assert clip["crop"] == ((0.1, 0.1, 0.9, 0.9), 2.0)
        assert clip["rotation"] == 270 and clip["flip_v"] and not clip["flip_h"]
        assert "Copied settings" in panel.hint_label.text()

    def test_copy_from_another_index_reads_its_global_layer(self, tmp_path):
        src, tgt = _images(tmp_path)
        tgt.adjustment_settings = {"contrast": 33, "cineon_log": True}
        tgt.fine_rotation_angle = 75
        panel = _panel()
        panel.current_idx = 0
        _set(panel, "contrast", -10)             # live UI of image 0
        panel.copy_settings_from_index(1)
        assert panel.clipboard["adjustments"]["contrast"] == 33
        assert panel.clipboard["cineon_log"] is True
        assert panel.clipboard["fine_rotation"] == 75

    def test_copy_of_untouched_image_says_so(self, tmp_path):
        _images(tmp_path)
        panel = _panel()
        panel.current_idx = 0
        panel.copy_adjustment_settings()
        assert panel.clipboard is not None
        assert "no changes from default" in panel.hint_label.text()


# --- paste: adjustments ---------------------------------------------------------

class TestPasteAdjustments:
    def _copy_from_0(self, panel, **sliders):
        panel.current_idx = 0
        for k, v in sliders.items():
            _set(panel, k, v)
        panel.copy_adjustment_settings()
        for k in sliders:                        # leave image 1's sliders clean
            _set(panel, k, 0)
        panel.current_idx = 1

    def test_unticked_sliders_keep_target_values(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        self._copy_from_0(panel, temperature=40, contrast=25)
        _set(panel, "contrast", -30)
        _paste(panel, monkeypatch, chosen={"adj:temperature"})
        assert _val(tgt, "temperature") == 40
        assert _val(tgt, "contrast") == -30

    def test_default_source_values_do_not_reset_target(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        self._copy_from_0(panel, saturation=10)
        _set(panel, "contrast", -30)             # source contrast is default (0)
        _paste(panel, monkeypatch)               # everything offered
        assert _val(tgt, "saturation") == 10
        assert _val(tgt, "contrast") == -30

    def test_pasted_dict_carries_every_key(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        self._copy_from_0(panel, temperature=40)
        _paste(panel, monkeypatch)
        for key in SlidersPanel.ADJUSTMENT_KEYS:
            assert key in tgt.adjustment_settings, key
        assert _val(tgt, "band_feather") == panel._default_for("band_feather")

    def test_sliders_reflect_the_merge(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        self._copy_from_0(panel, temperature=40, contrast=25)
        _set(panel, "contrast", -30)
        _paste(panel, monkeypatch, chosen={"adj:temperature"})
        assert _slider(panel, "temperature") == 40
        assert _slider(panel, "contrast") == -30

    def test_one_undo_state(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        self._copy_from_0(panel, temperature=40)
        before = len(tgt.undo_stack)
        _paste(panel, monkeypatch)
        assert len(tgt.undo_stack) == before + 1

    def test_cancel_and_empty_selection_change_nothing(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        self._copy_from_0(panel, temperature=40)
        snapshot = dict(tgt.adjustment_settings)
        _paste(panel, monkeypatch, accepted=False)
        assert tgt.adjustment_settings == snapshot
        _paste(panel, monkeypatch, chosen=set())
        assert tgt.adjustment_settings == snapshot
        assert "Nothing selected" in panel.hint_label.text()

    def test_paste_without_a_copy(self, tmp_path):
        src, tgt = _images(tmp_path)
        panel = _panel()
        panel.current_idx = 1
        panel.paste_adjustment_settings()
        assert tgt.adjustment_settings == {}
        assert "No settings to paste" in panel.hint_label.text()

    def test_bands_tick_applies_every_band_key(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        k1, k2 = list(BAND_ADJUSTMENT_KEYS)[:2]
        panel = _panel()
        self._copy_from_0(panel, **{k1: 20, k2: -15})
        _paste(panel, monkeypatch, chosen={"bands"})
        assert _val(tgt, k1) == 20 and _val(tgt, k2) == -15

    def test_unticked_items_are_remembered(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        self._copy_from_0(panel, temperature=40, contrast=25)
        _paste(panel, monkeypatch, chosen={"adj:temperature"})
        seen = []
        monkeypatch.undo()                       # back to the real selection()
        _paste(panel, monkeypatch, seen=seen)
        assert seen and seen[0].selection() == {"adj:temperature"}


class TestPasteCurvesAndCineon:
    CURVE = {"rgb": [[0.0, 0.0], [128.0, 200.0], [255.0, 255.0]]}
    OTHER = {"rgb": [[0.0, 0.0], [128.0, 60.0], [255.0, 255.0]]}

    def test_curves_ticked_replace_target(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        panel.current_idx = 0
        panel.curve_editor.set_curves(self.CURVE)
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        tgt.adjustment_settings = {"curves": self.OTHER}
        panel.curve_editor.set_curves(self.OTHER)
        _paste(panel, monkeypatch, chosen={"curves"})
        assert tgt.adjustment_settings["curves"]["rgb"][1][1] == 200.0

    def test_unticked_curves_survive(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        panel.current_idx = 0
        panel.curve_editor.set_curves(self.CURVE)
        _set(panel, "contrast", 10)
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        tgt.adjustment_settings = {"curves": self.OTHER}
        _paste(panel, monkeypatch, chosen={"adj:contrast"})
        assert tgt.adjustment_settings["curves"]["rgb"][1][1] == 60.0

    def test_cineon_flag_carried(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        src.adjustment_settings = {"cineon_log": True}
        panel = _panel()
        panel.current_idx = 0
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        _paste(panel, monkeypatch, chosen={"cineon_log"})
        assert tgt.adjustment_settings.get("cineon_log") is True

    def test_target_flag_survives_unrelated_paste(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        panel = _panel()
        panel.current_idx = 0
        _set(panel, "temperature", 40)
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        tgt.adjustment_settings = {"cineon_log": True}
        _paste(panel, monkeypatch)
        assert tgt.adjustment_settings.get("cineon_log") is True

    def test_cineon_never_written_into_an_area_layer(self, tmp_path, monkeypatch):
        src, tgt = _images(tmp_path)
        src.adjustment_settings = {"cineon_log": True}
        panel = _panel()
        panel.current_idx = 0
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        tgt.area_layers = [{"id": "a1", "kind": "circle", "enabled": True,
                            "feather": 0.2, "angle": 0,
                            "geometry": {"cx": 0.5, "cy": 0.5, "rx": 0.3, "ry": 0.3},
                            "settings": {}}]
        tgt.active_area_id = "a1"
        _paste(panel, monkeypatch, chosen={"cineon_log"})
        assert "cineon_log" not in tgt.get_area("a1")["settings"]
        assert tgt.adjustment_settings.get("cineon_log") is not True


# --- paste: whole-image settings -----------------------------------------------

class TestPasteWholeImage:
    def _copy(self, panel, src_setup, tmp_path, n=2):
        imgs = _images(tmp_path, n)
        src_setup(imgs[0])
        panel.current_idx = 0
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        return imgs

    def test_profile(self, tmp_path, monkeypatch):
        panel = _panel()
        src, tgt = self._copy(panel, lambda s: setattr(s, "color_profile", "bw"), tmp_path)
        _paste(panel, monkeypatch, chosen={"profile"})
        assert tgt.color_profile == "bw"

    def test_crop(self, tmp_path, monkeypatch):
        panel = _panel()

        def setup(s):
            s.crop_rect, s.crop_angle = (0.0, 0.0, 0.5, 1.0), 1.0
        src, tgt = self._copy(panel, setup, tmp_path)
        _paste(panel, monkeypatch, chosen={"crop"})
        assert tgt.crop_rect == (0.0, 0.0, 0.5, 1.0)
        assert tgt.crop_angle == 1.0

    def test_rotation_and_flips_are_separate(self, tmp_path, monkeypatch):
        panel = _panel()

        def setup(s):
            s.rotation_angle, s.horizontal_mirrored, s.vertical_mirrored = 90, True, True
        src, tgt = self._copy(panel, setup, tmp_path)
        _paste(panel, monkeypatch, chosen={"rotation", "flip_v"})
        assert tgt.rotation_angle == 90
        assert tgt.vertical_mirrored is True
        assert tgt.horizontal_mirrored is False

    def test_reorienting_the_current_image_resets_zoom(self, tmp_path, monkeypatch):
        panel = _panel()
        src, tgt = self._copy(panel, lambda s: setattr(s, "rotation_angle", 180), tmp_path)
        _paste(panel, monkeypatch, chosen={"rotation"})
        assert panel.parent().parent().image_preview.zoom_resets == 1

    def test_fine_rotation(self, tmp_path, monkeypatch):
        panel = _panel()
        src, tgt = self._copy(panel, lambda s: setattr(s, "fine_rotation_angle", 123),
                              tmp_path)
        tgt.fine_rotation_angle = -45
        _paste(panel, monkeypatch, chosen={"fine_rotation"})
        assert tgt.fine_rotation_angle == 123

    def test_fine_rotation_skipped_on_cropped_target(self, tmp_path, monkeypatch):
        panel = _panel()
        src, tgt = self._copy(panel, lambda s: setattr(s, "fine_rotation_angle", 123),
                              tmp_path)
        tgt.crop_rect, tgt.fine_rotation_angle = (0.1, 0.1, 0.9, 0.9), 0
        _paste(panel, monkeypatch, chosen={"fine_rotation"})
        assert tgt.fine_rotation_angle == 0
        assert "skipped" in panel.hint_label.text()

    def test_unticked_geometry_untouched(self, tmp_path, monkeypatch):
        panel = _panel()

        def setup(s):
            s.rotation_angle = 90
            s.crop_rect, s.crop_angle = (0.2, 0.2, 0.8, 0.8), 0.0
        src, tgt = self._copy(panel, setup, tmp_path)
        _set(panel, "contrast", 0)
        panel.clipboard["adjustments"]["contrast"] = 12
        _paste(panel, monkeypatch, chosen={"adj:contrast"})
        assert tgt.rotation_angle == 0 and tgt.crop_rect is None


# --- paste to several images -----------------------------------------------------

class TestPasteToSelection:
    def test_every_selected_image_gets_the_paste(self, tmp_path, monkeypatch):
        imgs = _images(tmp_path, 3)
        imgs[2].adjustment_settings = {"contrast": -20}
        panel = _panel()
        panel.current_idx = 0
        _set(panel, "saturation", 18)
        imgs[0].horizontal_mirrored = True
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        _set(panel, "saturation", 0)
        before = [len(i.undo_stack) for i in imgs]
        _paste(panel, monkeypatch, indices=[1, 2])
        for i in (1, 2):
            assert imgs[i].adjustment_settings["saturation"] == 18
            assert imgs[i].horizontal_mirrored is True
            assert len(imgs[i].undo_stack) == before[i] + 1
        assert imgs[2].adjustment_settings["contrast"] == -20    # own value kept
        assert "to 2 images" in panel.hint_label.text()
