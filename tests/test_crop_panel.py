#!/usr/bin/env python3
"""
GUI-level tests (offscreen Qt) for the Crop panel + ImagePreview crop
integration: aspect-ratio-constrained drags, straighten/reset hooks, and the
micro-rotation fold-in on enter/confirm. See spec/crop-panel.md §8.
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout,  # noqa: E402
                               QGraphicsPixmapItem)
from PySide6.QtGui import QPixmap, QColor  # noqa: E402
from PySide6.QtCore import QPointF, QRectF  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from widgets.image_preview import ImagePreview  # noqa: E402
from widgets.crop_panel import CropPanel  # noqa: E402

TOL = 0.02


class _StubPanel:
    def set_sliders_enabled(self, *a):
        pass


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.sliders_panel = _StubPanel()
        self.mid = QWidget(self)


class _RatioPanel:
    """Minimal stand-in for CropPanel that just reports a fixed ratio."""
    def __init__(self, r):
        self._r = r

    def current_effective_ratio(self, rotation, pixmap):
        return self._r

    def on_crop_geometry_changed(self):
        pass


class _Img:
    """CCRImage stand-in carrying just the crop/rotation fields the crop flow
    reads/writes, plus a recording undo stub."""
    def __init__(self, crop_rect=None, crop_angle=0.0, fine=0):
        self.crop_rect = crop_rect
        self.crop_angle = crop_angle
        self.fine_rotation_angle = fine
        self.converted = True
        self.undo = []

    def push_undo_state(self):
        self.undo.append((self.crop_rect, self.crop_angle, self.fine_rotation_angle))

    def update_thumbnail_and_preview(self):
        pass


_HOSTS = []


def _make_preview(w=400, h=300):
    host = _Host()
    _HOSTS.append(host)
    layout = QVBoxLayout(host)
    layout.addWidget(host.mid)
    mid_layout = QVBoxLayout(host.mid)
    ip = ImagePreview(host.mid)
    mid_layout.addWidget(ip)
    pm = QPixmap(w, h)
    pm.fill(QColor(128, 128, 128))
    ip.current_pixmap = pm
    ip.pixmap_item = QGraphicsPixmapItem(pm)
    ip.scene.addItem(ip.pixmap_item)
    ip.current_rotation = 0
    ip.current_horizontal_flip = False
    ip.current_vertical_flip = False
    ip.crop_mode = True
    ip.current_idx = 0
    return ip, host


def _box_ratio(rect):
    return rect.width() / rect.height()


# --------------------------------------------------------------------------
# Aspect-ratio-constrained drags
# --------------------------------------------------------------------------
class TestConstrainedDrags:
    def test_new_selection_holds_ratio(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        ip._update_new_selection(QPointF(50, 50), QPointF(250, 100))
        rect = ip._pending_crop_local
        assert rect is not None
        assert _box_ratio(rect) == pytest.approx(1.5, abs=TOL)
        # Anchored at the start corner.
        assert rect.left() == pytest.approx(50, abs=0.5)
        assert rect.top() == pytest.approx(50, abs=0.5)

    def test_new_selection_clamped_into_image_keeps_ratio(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        # Drag well past the right/bottom edges from near the corner.
        ip._update_new_selection(QPointF(380, 280), QPointF(900, 900))
        rect = ip._pending_crop_local
        assert rect.right() <= 400 + 0.5 and rect.bottom() <= 300 + 0.5
        assert _box_ratio(rect) == pytest.approx(1.5, abs=TOL)

    def test_free_is_unconstrained(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(None))
        ip._update_new_selection(QPointF(10, 10), QPointF(300, 50))
        rect = ip._pending_crop_local
        assert rect.width() == pytest.approx(290, abs=0.5)
        assert rect.height() == pytest.approx(40, abs=0.5)

    def test_corner_drag_holds_ratio(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        drag = {"box0": QRectF(100, 100, 120, 80), "angle0": 0.0}
        ip._drag_corner(drag, QPointF(320, 260), "br")
        rect = ip._pending_crop_local
        assert _box_ratio(rect) == pytest.approx(1.5, abs=TOL)

    def test_edge_drag_holds_ratio(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        drag = {"box0": QRectF(100, 100, 120, 80), "angle0": 0.0}
        ip._drag_edge(drag, QPointF(280, 140), "r")
        rect = ip._pending_crop_local
        assert _box_ratio(rect) == pytest.approx(1.5, abs=TOL)


# --------------------------------------------------------------------------
# Reshape / seed / straighten / reset hooks
# --------------------------------------------------------------------------
class TestCropHooks:
    def test_reapply_reshapes_existing_box(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        ip._pending_crop_local = QRectF(50, 50, 200, 100)  # ratio 2.0
        ip.reapply_crop_ratio()
        assert _box_ratio(ip._pending_crop_local) == pytest.approx(1.5, abs=TOL)

    def test_seed_on_entry_creates_centered_box_when_locked(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        ip._pending_crop_local = None
        ip.seed_crop_ratio_on_entry()
        rect = ip._pending_crop_local
        assert rect is not None
        assert _box_ratio(rect) == pytest.approx(1.5, abs=TOL)
        assert rect.center().x() == pytest.approx(200, abs=0.5)
        assert rect.center().y() == pytest.approx(150, abs=0.5)

    def test_seed_leaves_user_box_untouched(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        ip._pending_crop_local = QRectF(10, 10, 100, 100)  # user box, ratio 1.0
        ip._crop_box_is_seed = False
        ip.seed_crop_ratio_on_entry()
        assert _box_ratio(ip._pending_crop_local) == pytest.approx(1.0, abs=TOL)

    def test_seed_free_leaves_box_empty(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(None))
        ip._pending_crop_local = None
        ip.seed_crop_ratio_on_entry()
        assert ip._pending_crop_local is None

    def test_set_pending_straighten(self):
        ip, _h = _make_preview()
        ip._pending_crop_local = QRectF(50, 50, 100, 100)
        ip.set_pending_straighten(3.5)
        assert ip._pending_crop_angle == pytest.approx(3.5)

    def test_rotate_knob_clamped_to_straighten_range(self):
        # The rotate knob must not drive the box past the ±45° straighten slider
        # range, or the two desync and the extra rotation is silently lost.
        ip, _h = _make_preview()
        # box centered at (150,150); start straight up, drag to +90° raw.
        drag = {"box0": QRectF(100, 100, 100, 100),
                "start": QPointF(150, 50), "angle0": 0.0}
        ip._drag_rotate_box(drag, QPointF(250, 150))
        assert ip._pending_crop_angle == pytest.approx(45.0)   # clamped, not 90
        # Same drag the other way clamps to -45.
        ip._drag_rotate_box(drag, QPointF(50, 150))
        assert ip._pending_crop_angle == pytest.approx(-45.0)

    def test_rotate_knob_preserves_folded_angle_over_45(self):
        # A crop entered with a folded micro-rotation can legitimately start
        # beyond ±45 (committed crop_angle ±45 folded with the canvas rotation
        # ±45 → up to ±90). An incidental knob touch must NOT snap it to 45 (that
        # silently discards rotation) — it may only forbid pushing further out.
        ip, _h = _make_preview()
        drag = {"box0": QRectF(100, 100, 100, 100),
                "start": QPointF(150, 50), "angle0": 60.0}
        ip._drag_rotate_box(drag, QPointF(145, 50))    # tiny nudge
        assert ip._pending_crop_angle > 45.0            # not collapsed to 45
        assert ip._pending_crop_angle <= 60.0 + 1e-6    # not pushed past the start

    def test_reset_pending_crop(self):
        ip, _h = _make_preview()
        ip.set_crop_panel(_RatioPanel(1.5))
        ip._pending_crop_local = QRectF(50, 50, 100, 100)
        ip._pending_crop_angle = 7.0
        ip.reset_pending_crop()
        assert ip._pending_crop_local is None
        assert ip._pending_crop_angle == 0.0


# --------------------------------------------------------------------------
# Micro-rotation fold-in (enter / confirm / cancel)
# --------------------------------------------------------------------------
class TestStraightenFoldIn:
    def _entered(self, fine, crop_rect=None, crop_angle=0.0):
        ip, host = _make_preview()
        ip.crop_mode = False                      # let enter_crop_mode flip it
        img = _Img(crop_rect=crop_rect, crop_angle=crop_angle, fine=fine)
        ccr_backend.images = [img]
        ip.update_preview = lambda *a, **k: None  # skip the heavy re-render
        ok = ip.enter_crop_mode()
        return ip, img, ok

    def test_enter_folds_fine_into_box_angle(self):
        ip, _img, ok = self._entered(fine=200)        # +2.0 deg micro-rotation
        assert ok
        assert ip._crop_fold_fine is True
        assert ip._pending_crop_angle == pytest.approx(-2.0)
        # Seeds a full-frame box so the folded angle is committable.
        assert ip._crop_box_is_seed is True
        rect = ip._pending_crop_local
        assert rect.width() == pytest.approx(400) and rect.height() == pytest.approx(300)

    def test_enter_combines_existing_crop_angle(self):
        ip, _img, ok = self._entered(fine=-150, crop_rect=(0.1, 0.1, 0.9, 0.9),
                                     crop_angle=1.0)
        assert ok
        assert ip._pending_crop_angle == pytest.approx(2.5)   # 1.0 - (-1.5)
        assert ip._crop_box_is_seed is False                  # used the real crop

    def test_no_fine_does_not_fold(self):
        ip, _img, ok = self._entered(fine=0)
        assert ok
        assert ip._crop_fold_fine is False
        assert ip._pending_crop_angle == pytest.approx(0.0)
        assert ip._pending_crop_local is None                 # no crop, no seed

    def test_confirm_clears_fine_rotation(self):
        ip, img, _ok = self._entered(fine=200)
        ip.confirm_crop()
        assert img.fine_rotation_angle == 0                   # folded away
        assert img.crop_angle == pytest.approx(-2.0)          # leveling preserved
        assert img.crop_rect is not None
        assert img.undo                                       # one undo snapshot
        # The snapshot captured the ORIGINAL fine rotation (undoable).
        assert img.undo[-1][2] == 200

    def test_cancel_preserves_fine_rotation(self):
        ip, img, _ok = self._entered(fine=300)
        ip.cancel_crop_mode()
        assert img.fine_rotation_angle == 300                 # untouched on cancel

    def test_reset_then_confirm_clears_committed_crop(self):
        # Regression: entering crop on a cropped image, clicking Reset (Free
        # ratio -> pending box becomes None), then Done must actually clear the
        # committed crop — not silently keep it.
        ip, img, _ok = self._entered(fine=0, crop_rect=(0.1, 0.1, 0.9, 0.9))
        assert ip._pending_crop_local is not None   # seeded from the committed crop
        ip.reset_pending_crop()                     # user clicks Reset (Free)
        assert ip._pending_crop_local is None
        ip.confirm_crop()                           # user clicks Done
        assert img.crop_rect is None                # crop actually removed
        assert img.crop_angle == pytest.approx(0.0)
        assert img.undo                             # one undo snapshot pushed

    def test_confirm_no_box_on_uncropped_is_noop(self):
        # Entering crop on an uncropped Free image and pressing Done with no box
        # must not push a junk undo state or change anything.
        ip, img, _ok = self._entered(fine=0)
        assert ip._pending_crop_local is None
        ip.confirm_crop()
        assert img.crop_rect is None
        assert not img.undo                          # no-op, no undo snapshot

    def test_reset_then_confirm_clears_folded_fine_rotation(self):
        # Entering crop on a cropped image that also has a fine micro-rotation
        # folds the rotation into the (leveled) straighten. Reset zeroes the
        # straighten and the preview shows level, so Done must clear the fine
        # rotation too — not let it snap back on exit (matches the box-present
        # fold path).
        ip, img, _ok = self._entered(fine=500, crop_rect=(0.1, 0.1, 0.9, 0.9))
        assert ip._crop_fold_fine is True
        ip.reset_pending_crop()                      # box -> None, straighten -> 0
        ip.confirm_crop()                            # Done
        assert img.crop_rect is None                 # crop cleared
        assert img.fine_rotation_angle == 0          # leveled, matching straighten 0
        assert img.undo
        assert img.undo[-1][2] == 500                # original fine captured for undo


# --------------------------------------------------------------------------
# CropPanel widget: aspect-ratio resolution + visibility
# --------------------------------------------------------------------------
class TestCropPanelWidget:
    def _panel(self):
        ip, host = _make_preview()
        panel = CropPanel(host, ip)
        ip.set_crop_panel(panel)
        return panel, ip

    @staticmethod
    def _select(panel, key, landscape=True, cw=16, ch=9):
        # _suppress avoids touching real QSettings / triggering reshape.
        panel._suppress = True
        panel.aspect_combo.setCurrentIndex(panel.aspect_combo.findData(key))
        (panel.landscape_radio if landscape else panel.portrait_radio).setChecked(True)
        panel.custom_w_spin.setValue(cw)
        panel.custom_h_spin.setValue(ch)
        panel._update_custom_visibility()
        panel._suppress = False

    def test_free_ratio_none(self):
        panel, ip = self._panel()
        self._select(panel, "free")
        assert panel.current_display_ratio(ip.current_pixmap) is None

    def test_one_to_one(self):
        panel, ip = self._panel()
        self._select(panel, "1:1")
        assert panel.current_display_ratio(ip.current_pixmap) == pytest.approx(1.0)

    def test_three_two_orientation(self):
        panel, ip = self._panel()
        self._select(panel, "3:2", landscape=True)
        assert panel.current_display_ratio(ip.current_pixmap) == pytest.approx(1.5)
        self._select(panel, "3:2", landscape=False)
        assert panel.current_display_ratio(ip.current_pixmap) == pytest.approx(1 / 1.5)

    def test_custom(self):
        panel, ip = self._panel()
        self._select(panel, "custom", cw=16, ch=9)
        assert panel.current_display_ratio(ip.current_pixmap) == pytest.approx(16 / 9)

    def test_original_from_pixmap(self):
        panel, ip = self._panel()                      # pixmap is 400x300
        self._select(panel, "original")
        assert panel.current_display_ratio(ip.current_pixmap) == pytest.approx(400 / 300)

    def test_effective_inverts_on_90(self):
        panel, ip = self._panel()
        self._select(panel, "3:2", landscape=True)
        assert panel.current_effective_ratio(0, ip.current_pixmap) == pytest.approx(1.5)
        assert panel.current_effective_ratio(90, ip.current_pixmap) == pytest.approx(1 / 1.5)

    def test_custom_visibility_and_orientation_gating(self):
        panel, ip = self._panel()
        self._select(panel, "custom")
        assert panel.custom_w_spin.isHidden() is False
        assert panel.landscape_radio.isEnabled() is False   # custom is orientation-fixed
        self._select(panel, "3:2")
        assert panel.custom_w_spin.isHidden() is True
        assert panel.landscape_radio.isEnabled() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# --------------------------------------------------------------------------
# Straighten fine controls: −/+ nudge and the typed angle box
# --------------------------------------------------------------------------
class TestStraightenFineControls:
    def _panel(self, monkeypatch):
        ip, host = _make_preview()
        panel = CropPanel(host, ip)
        ip.set_crop_panel(panel)
        sent = []
        monkeypatch.setattr(ip, "set_pending_straighten", sent.append)
        return panel, ip, sent

    def test_plus_minus_step_a_tenth(self, monkeypatch):
        panel, _ip, sent = self._panel(monkeypatch)
        panel.straighten_plus_btn.click()
        assert panel.straighten_slider.value() == 1
        assert sent[-1] == 0.1
        panel.straighten_minus_btn.click()
        panel.straighten_minus_btn.click()
        assert panel.straighten_slider.value() == -1
        assert abs(panel.straighten_spin.value() - (-0.1)) < 1e-9

    def test_nudge_clamps_at_the_ends(self, monkeypatch):
        panel, _ip, _sent = self._panel(monkeypatch)
        panel.straighten_slider.setValue(450)
        panel.straighten_plus_btn.click()
        assert panel.straighten_slider.value() == 450

    def test_typed_angle_drives_the_slider(self, monkeypatch):
        panel, _ip, sent = self._panel(monkeypatch)
        panel.straighten_spin.setValue(2.3)
        assert panel.straighten_slider.value() == 23
        assert abs(sent[-1] - 2.3) < 1e-9

    def test_slider_mirrors_into_the_box(self, monkeypatch):
        panel, _ip, _sent = self._panel(monkeypatch)
        panel.straighten_slider.setValue(-127)
        assert abs(panel.straighten_spin.value() - (-12.7)) < 1e-9

    def test_canvas_sync_does_not_echo_back(self, monkeypatch):
        panel, ip, sent = self._panel(monkeypatch)
        ip._pending_crop_angle = 4.4
        panel.on_crop_geometry_changed()
        assert panel.straighten_slider.value() == 44
        assert abs(panel.straighten_spin.value() - 4.4) < 1e-9
        assert sent == []


# --------------------------------------------------------------------------
# Horizon tool
# --------------------------------------------------------------------------
class TestHorizonTool:
    def test_angle_math(self):
        from PySide6.QtCore import QPointF as P
        from widgets.image_preview import ImagePreview as IP
        assert IP.horizon_angle(P(0, 0), P(100, 0)) == 0.0
        assert abs(IP.horizon_angle(P(0, 0), P(100, 5)) - 2.862) < 0.01
        # direction of drawing doesn't matter
        assert abs(IP.horizon_angle(P(100, 5), P(0, 0)) - 2.862) < 0.01
        # near-vertical lines level to vertical
        assert abs(IP.horizon_angle(P(0, 0), P(4, 100)) - (-2.29)) < 0.01
        assert abs(IP.horizon_angle(P(0, 0), P(-4, 100)) - 2.29) < 0.01

    def _armed(self):
        ip, host = _make_preview()
        panel = CropPanel(host, ip)
        ip.set_crop_panel(panel)                  # _make_preview: crop mode on
        panel.horizon_btn.setChecked(True)
        assert ip._horizon_tool
        return ip, panel

    def test_line_sets_the_straighten_angle_and_disarms(self):
        ip, panel = self._armed()
        base = ip._base_transform()
        a = base.map(QPointF(10, 20))
        b = base.map(QPointF(110, 25))
        ip.horizon_press(a)
        ip.horizon_move(b)
        assert ip._horizon_line_item is not None
        ip.horizon_release(b)
        assert abs(ip._pending_crop_angle - 2.862) < 0.01
        assert panel.straighten_slider.value() == 29
        assert not ip._horizon_tool and not panel.horizon_btn.isChecked()
        assert ip._horizon_line_item is None

    def test_a_click_is_ignored(self):
        ip, panel = self._armed()
        p = ip._base_transform().map(QPointF(50, 50))
        ip.horizon_press(p)
        ip.horizon_release(p)
        assert ip._pending_crop_angle == 0.0
        assert ip._horizon_tool                      # still armed

    def test_leaving_crop_mode_disarms(self):
        ip, panel = self._armed()
        ip._exit_crop_mode()                       # every crop exit goes here
        assert not ip._horizon_tool and not panel.horizon_btn.isChecked()
