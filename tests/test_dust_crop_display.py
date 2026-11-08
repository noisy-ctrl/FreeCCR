#!/usr/bin/env python3
"""
Dust mode displays the CROPPED frame (the normal display framing) and maps
strokes back to full-frame coordinates.

Spots are stored normalized against the full un-cropped frame — the heal
replays on the un-cropped buffer before the crop at every resolution
(preview / zoom / export) — so with a confirmed crop on screen the canvas
mapping must go displayed → full via _crop_display_transform:

- update_preview keeps the crop display in dust mode (no full-frame swap);
- _dust_scene_to_norm returns full-frame normalized coords;
- the live stroke overlay and brush ring map back into display space and are
  sized by the FULL frame width (the crop extraction is isometric);
- a committed stroke stores full-frame normalized points on the image.
"""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from widgets.image_preview import ImagePreview  # noqa: E402


def _scan_png(tmp_path, name="negative.png", w=600, h=400, seed=21):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 20000 + 25000 * (xx / w) + 10000 * (yy / h)
    img = np.stack([base * 1.2, base, base * 0.7], axis=-1)
    img += rng.normal(0, 1500, img.shape)
    img = np.clip(img, 1000, 64000).astype(np.uint16)
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


class _StubPanel:
    def set_sliders_enabled(self, *a):
        pass

    def set_current_idx(self, *a):
        pass

    def set_histogram(self, *a):
        pass

    def set_hint(self, *a, **kw):
        pass


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.sliders_panel = _StubPanel()
        self.mid = QWidget(self)


_HOSTS = []


def _converted_preview(tmp_path, crop_rect=(0.25, 0.25, 0.75, 0.75),
                       crop_angle=0.0):
    """Real CCRImage (600x400) converted through the backend, displayed in a
    real ImagePreview via the real update_preview, with the given crop."""
    path = _scan_png(tmp_path)
    img = CCRImage(path)
    img.reference_frame = (20, 20, 580, 380)
    ccr_backend.images = [img]
    ccr_backend.file_paths = [path]
    ccr_backend.convert_negative_by_index(0)
    img.crop_rect = crop_rect
    img.crop_angle = crop_angle

    host = _Host()
    _HOSTS.append(host)  # keep alive
    layout = QVBoxLayout(host)
    layout.addWidget(host.mid)
    mid_layout = QVBoxLayout(host.mid)
    ip = ImagePreview(host.mid)
    mid_layout.addWidget(ip)
    ip.update_preview(0)
    return ip, img


class TestDustModeShowsCrop:
    def test_dust_mode_keeps_cropped_display(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        assert ip.current_pixmap.width() == 300   # crop displayed before entry
        assert ip.enter_dust_mode() is True
        # Entering dust mode must KEEP the cropped framing, not swap to the
        # full frame (the old behavior).
        assert ip.dust_mode is True
        assert (ip.current_pixmap.width(), ip.current_pixmap.height()) == (300, 200)
        assert ip._crop_display_transform is not None

    def test_no_crop_shows_full_frame(self, tmp_path):
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        assert ip.enter_dust_mode() is True
        assert (ip.current_pixmap.width(), ip.current_pixmap.height()) == (600, 400)
        assert ip._crop_display_transform is None


class TestHealSourceOverlay:
    def test_overlay_draws_from_plan_and_clears(self, tmp_path):
        # 'Show heal sources': stroke outline + per-segment sample markers,
        # derived from the cached preview plan, drawn through the same
        # crop-display mapping as the brush; toggling off removes them.
        ip, img = _converted_preview(tmp_path)
        ip.enter_dust_mode()
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.02}]
        img.update_thumbnail_and_preview()   # preview heal caches the plan
        cached = getattr(img, "_dust_plan_cache", None)
        assert cached is not None and cached[0] == repr(img.dust_spots)
        ip.set_dust_source_overlay(True)
        # At least the stroke outline plus one sample arrow / fallback ring.
        assert len(ip._dust_debug_items) >= 2
        ip.set_dust_source_overlay(False)
        assert ip._dust_debug_items == []
        # Exit tears the overlay down with the other dust items.
        ip.set_dust_source_overlay(True)
        assert ip._dust_debug_items
        ip.exit_dust_mode()
        assert ip._dust_debug_items == []


class TestRightButtonStrokeEditing:
    def test_right_drag_moves_stroke_and_repicks_touching(self, tmp_path):
        # THE SAMPLING RULE retroacts on automatic pins: a moved stroke's
        # AUTOMATIC source used to travel absolutely (offset compensated),
        # which leaves it far from the stroke's new position — a non-touching
        # automatic sample, which the rule forbids. After a move the segment
        # re-picks a TOUCHING sample at its new location; only a USER re-pick
        # (manual=True, see the repick test below) travels absolutely.
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        ip.enter_dust_mode()
        ip._maybe_request_hires = lambda: None   # keep the test in-thread
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}]
        img.update_thumbnail_and_preview()
        (cy, cx, off, *_), = img._dust_plan_cache[1]
        assert off is not None
        ip.set_dust_source_overlay(True)
        # Right-drag the stroke 60 px right (no crop: scene == full coords).
        ip.dust_right_press(QPointF(300, 200))
        assert ip._dust_drag is not None
        ip.dust_right_move(QPointF(360, 200))
        ip.dust_right_release()
        assert img.dust_spots[0]["pts"][0] == pytest.approx([0.6, 0.5])
        # The moved stroke's automatic sample TOUCHES it at the new place.
        (cy2, cx2, off2, *_), = img._dust_plan_cache[1]
        assert cx2 == pytest.approx(0.6, abs=0.01)
        assert off2 is not None
        h, w = img.resized_raw.shape[:2]
        r_px = 0.03 * w
        assert float(np.hypot(off2[0] * h, off2[1] * w)) <= 2 * r_px + 8
        # The drag is one undo step back to the pre-drag stroke.
        assert img.pop_undo_state()
        assert img.dust_spots[0]["pts"][0] == pytest.approx([0.5, 0.5])

    def test_double_right_click_deletes_stroke(self, tmp_path):
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        ip.enter_dust_mode()
        ip._maybe_request_hires = lambda: None
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}]
        img.update_thumbnail_and_preview()
        ip.set_dust_source_overlay(True)
        ip.dust_right_double(QPointF(100, 50))   # miss: nothing happens
        assert len(img.dust_spots) == 1
        ip.dust_right_double(QPointF(300, 200))  # on the stroke: deleted
        assert img.dust_spots == []
        assert img.pop_undo_state()
        assert len(img.dust_spots) == 1

    def test_right_drag_on_source_marker_repicks_the_sample(self, tmp_path):
        # Dragging the SOURCE ring changes where that segment samples from:
        # the plan record's offset follows the drop point, the re-heal pins
        # it verbatim, and the user's pick becomes the sticky reference.
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        ip.enter_dust_mode()
        ip._maybe_request_hires = lambda: None
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}]
        img.update_thumbnail_and_preview()
        (cy, cx, off, *_), = img._dust_plan_cache[1]
        assert off is not None
        ip.set_dust_source_overlay(True)
        # Grab the marker at the source position (no crop: scene == full px).
        sx, sy = (cx + off[1]) * 600, (cy + off[0]) * 400
        ip.dust_right_press(QPointF(sx, sy))
        assert ip._dust_src_drag is not None and ip._dust_drag is None
        # Drop it 80 px below (clean area) and release.
        ip.dust_right_move(QPointF(sx, sy + 80))
        ip.dust_right_release()
        (cy2, cx2, off2, *rest), = img._dust_plan_cache[1]
        assert (cy2, cx2) == pytest.approx((cy, cx), abs=1e-6)
        assert cx2 + off2[1] == pytest.approx((sx) / 600.0, abs=0.01)
        assert cy2 + off2[0] == pytest.approx((sy + 80) / 400.0, abs=0.01)
        # The pick is marked manual (verbatim clone) and survives the
        # re-heal's plan collection.
        assert rest[2] is True

    def test_adj_sig_tracks_source_repick(self, tmp_path):
        # Dragging a source ring rewrites only the PLAN (spots untouched);
        # the hi-res display signature must change with it, or the zoomed
        # display keeps showing the old fill after a re-pick.
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        ip.enter_dust_mode()
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}]
        img.update_thumbnail_and_preview()
        sig1 = ip._current_adj_sig()
        key, plan = img._dust_plan_cache
        (cy, cx, off, *rest), = plan
        assert off is not None
        img._dust_plan_cache = (key, [(cy, cx, (off[0] + 0.05, off[1]),
                                       *rest)])
        assert ip._current_adj_sig() != sig1

    def test_right_press_needs_overlay_shown(self, tmp_path):
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        ip.enter_dust_mode()
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}]
        ip.dust_right_press(QPointF(300, 200))   # overlay off: no drag
        assert ip._dust_drag is None


class TestSceneToNormThroughCrop:
    def test_center_of_crop_maps_to_crop_center(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        ip.enter_dust_mode()
        # Displayed pixmap is the (150,100)-(450,300) region of the 600x400
        # frame; its center must normalize to the crop's full-frame center.
        n = ip._dust_scene_to_norm(QPointF(150, 100))
        assert n == pytest.approx([0.5, 0.5], abs=1e-6)

    def test_display_origin_maps_to_crop_origin(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        ip.enter_dust_mode()
        n = ip._dust_scene_to_norm(QPointF(0, 0))
        assert n == pytest.approx([0.25, 0.25], abs=1e-6)

    def test_rotated_crop_center_maps_to_crop_center(self, tmp_path):
        ip, img = _converted_preview(tmp_path, crop_angle=10.0)
        ip.enter_dust_mode()
        w = ip.current_pixmap.width()
        h = ip.current_pixmap.height()
        # A rotated crop rotates about its center, so the display center maps
        # to the crop center regardless of the angle.
        n = ip._dust_scene_to_norm(QPointF(w / 2.0, h / 2.0))
        assert n == pytest.approx([0.5, 0.5], abs=1e-3)

    def test_no_crop_is_identity(self, tmp_path):
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        ip.enter_dust_mode()
        n = ip._dust_scene_to_norm(QPointF(150, 100))
        assert n == pytest.approx([0.25, 0.25], abs=1e-6)


class TestOverlayMapsBack:
    def test_stroke_overlay_lands_on_display_position(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        ip.enter_dust_mode()
        # Full-frame stroke from the crop center to 0.05 to its right; on the
        # displayed (cropped) pixmap that is (150,100) -> (180,100). (Two
        # distinct points — Qt drops a lineTo onto the current position.)
        ip._dust_pts = [[0.5, 0.5], [0.55, 0.5]]
        ip._draw_dust_stroke()
        assert ip._dust_stroke_item is not None
        r = ip._dust_stroke_item.path().boundingRect()
        assert (r.left(), r.top()) == pytest.approx((150.0, 100.0), abs=1e-6)
        assert (r.right(), r.bottom()) == pytest.approx((180.0, 100.0), abs=1e-6)

    def test_stroke_pen_width_uses_full_frame_width(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        ip.enter_dust_mode()
        ip._dust_pts = [[0.5, 0.5]]
        ip._draw_dust_stroke()
        # 600 = full-frame width; the cropped pixmap is only 300 wide.
        assert ip._dust_stroke_item.pen().widthF() == pytest.approx(
            2.0 * ip._dust_brush_r * 600)

    def test_cursor_ring_uses_full_frame_width(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        ip.enter_dust_mode()
        ip._update_dust_cursor(QPointF(50, 50))
        assert ip._dust_cursor_item is not None
        assert ip._dust_cursor_item.rect().width() == pytest.approx(
            2.0 * ip._dust_brush_r * 600)


class TestCommittedSpotIsFullFrame:
    def test_press_release_stores_full_frame_norm(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        ip.enter_dust_mode()
        ip._maybe_request_hires = lambda: None   # keep the test threadless
        ip.dust_press(QPointF(150, 100))         # display center of the crop
        ip.dust_release()
        assert len(img.dust_spots) == 1
        spot = img.dust_spots[0]
        assert spot["kind"] == "brush"
        assert spot["pts"][0] == pytest.approx([0.5, 0.5], abs=1e-6)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestSourceHandleAfterPainting:
    """spec/dust-spot-editing.md, the Brush part (2026-11-08): the stroke just
    painted shows its source ring with 'Show heal sources' OFF, and a LEFT
    drag on the ring re-picks it, one undo step."""

    def _painted(self, tmp_path):
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        ip.enter_dust_mode()
        ip._maybe_request_hires = lambda: None
        img.dust_spots = [{"kind": "brush", "pts": [[0.2, 0.2]], "r": 0.02}]
        img.update_thumbnail_and_preview()
        ip.dust_press(QPointF(300, 200))
        ip.dust_release()
        assert len(img.dust_spots) == 2
        return ip, img

    def _ring(self, img, spot_x):
        for cy, cx, off, *rest in img._dust_plan_cache[1]:
            if off is not None and abs(cx - spot_x) < 0.02:
                return cy, cx, off
        raise AssertionError("no sampled record")

    def test_the_new_strokes_ring_shows_and_left_drags(self, tmp_path):
        ip, img = self._painted(tmp_path)
        assert not ip._dust_debug and ip._dust_debug_items      # drawn anyway
        # only the new stroke's sources are grabbable
        cy0, cx0, off0 = self._ring(img, 0.2)
        assert ip._dust_source_index_at(
            [cx0 + off0[1], cy0 + off0[0]]) is None
        cy, cx, off = self._ring(img, 0.5)
        sx, sy = (cx + off[1]) * 600, (cy + off[0]) * 400
        ip.dust_press(QPointF(sx, sy))
        assert ip._dust_src_drag is not None and not ip._dust_painting
        ip.dust_right_move(QPointF(sx, sy + 60))
        ip.dust_right_release()
        assert len(img.dust_spots) == 2                          # nothing painted
        cy2, cx2, off2 = self._ring(img, 0.5)
        assert cy2 + off2[0] == pytest.approx((sy + 60) / 400.0, abs=0.01)
        # one Ctrl+Z puts the source back, the stroke stays
        assert img.pop_undo_state()
        assert len(img.dust_spots) == 2
        img.update_thumbnail_and_preview()
        cy3, cx3, off3 = self._ring(img, 0.5)
        assert off3 == pytest.approx(off, abs=1e-6)

    def test_a_click_off_the_ring_paints_and_undo_hides_the_ring(self, tmp_path):
        ip, img = self._painted(tmp_path)
        ip.dust_press(QPointF(100, 300))
        assert ip._dust_painting and ip._dust_src_drag is None
        ip.dust_release()
        assert len(img.dust_spots) == 3
        img.pop_undo_state()
        assert ip._dust_selected_index(img) is None
        ip._draw_dust_debug()
        assert ip._dust_debug_items == []


class TestBwPointUncrop:
    """spec/bw-point-uncrop.md: a black / white point pick shows the frame
    uncropped, and the crop comes back when the pick ends."""

    def test_arming_uncrops_and_esc_recrops(self, tmp_path):
        ip, img = _converted_preview(tmp_path)
        assert ip.current_pixmap.width() == 300
        ip.set_bwpoint_mode("black")
        assert (ip.current_pixmap.width(), ip.current_pixmap.height()) == (600, 400)
        assert ip._crop_display_transform is None
        assert img.crop_rect == (0.25, 0.25, 0.75, 0.75)          # untouched
        ip._on_escape_key()
        assert ip.view.bwpoint_mode is None
        assert ip.current_pixmap.width() == 300

    def test_a_drawn_pick_samples_the_full_frame_then_recrops(self, tmp_path, monkeypatch):
        from PySide6.QtCore import QEvent, QPoint, Qt
        from PySide6.QtGui import QMouseEvent
        ip, img = _converted_preview(tmp_path)
        got = []
        monkeypatch.setattr(ccr_backend, "set_black_point",
                            lambda bgr, exposure=None: got.append(bgr))
        ip.set_bwpoint_mode("black")
        # a box in the top-left corner: outside the crop, in the film area
        ip.view._bw_drag_start = ip.view.mapToScene(ip.view.mapFromScene(QPointF(10, 10)))
        end = ip.view.mapFromScene(QPointF(60, 50))
        ip.view.mouseReleaseEvent(QMouseEvent(
            QEvent.MouseButtonRelease, QPointF(end), QPointF(ip.view.mapToGlobal(end)),
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        assert len(got) == 1
        raw = img.read_image(img.file_path, max_long_side=1080)
        want = raw[10:50, 10:60].reshape(-1, 3).mean(axis=0)
        assert np.allclose(got[0], want, rtol=0.03)
        assert ip.view.bwpoint_mode is None
        assert ip.current_pixmap.width() == 300                   # cropped again

    def test_no_crop_no_rerender(self, tmp_path, monkeypatch):
        ip, img = _converted_preview(tmp_path, crop_rect=None)
        calls = []
        monkeypatch.setattr(ip, "update_preview", lambda i: calls.append(i))
        ip.set_bwpoint_mode("white")
        ip.set_bwpoint_mode(None)
        assert calls == []
