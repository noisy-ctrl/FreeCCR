#!/usr/bin/env python3
"""The Skin tool (spec/skin-picker.md): show a face's hue against the range
your labs print skin at, or pull it into that range."""
import math
import os
import sys

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import skin_balance as sb  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402

_KEEP = []
BASE = np.array([0.30, 0.50, 0.75])            # the film base, BGR
# pinkish "skin" patches on the negative: 112 and 103 degrees once converted
FACS = [(0.9, 0.35, 0.08), (0.85, 0.35, 0.10), (0.9, 0.5, 0.12)]


class _Store(dict):
    def value(self, k, d=None):
        return self.get(k, d)

    def setValue(self, k, v):
        self[k] = v


def _converted_frame(tmp_path, monkeypatch):
    from core.ccr_image import CCRImage
    h, w = 120, 260
    neg = np.zeros((h, w, 3), np.float32)
    neg[...] = BASE
    for i, f in enumerate(FACS):
        neg[10:110, 10 + i * 34: 38 + i * 34] = BASE * np.array(f)
    neg[0:8, :] = BASE * 0.03                   # something bright in the picture
    path = str(tmp_path / "neg.tif")
    cv2.imwrite(path, (neg * 65535).astype(np.uint16))
    img = CCRImage(path)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "file_paths", [path])
    monkeypatch.setattr(ccr_backend, "black_point_bgr", tuple(BASE * 65535))
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None)
    if hasattr(ccr_backend, "auto_black_point"):
        monkeypatch.setattr(ccr_backend, "auto_black_point", False)
    ccr_backend.apply_bwpoint_to_all_images()
    assert img.converted and img.conversion_inputs["mode"] == "bw"
    H, W = img.resized_raw.shape[:2]
    sx = W / w
    patches = [img.resized_raw[int(30 * sx):int(90 * sx),
                               int((14 + i * 34) * sx):int((34 + i * 34) * sx)]
               for i in range(len(FACS))]
    return img, patches


# --- the maths ----------------------------------------------------------------

def _ycc_at(angle, rho=30.0, y=150.0):
    r = math.radians(angle)
    return np.array([y, rho * math.cos(r), rho * math.sin(r)])


def test_the_ranges():
    assert sb.video_line_deg() == pytest.approx(132.5, abs=0.1)
    assert sb.skin_range("lab") == sb.LAB_RANGE == (128.9, 146.4)
    lo, hi = sb.skin_range("video")
    assert (lo, hi) == pytest.approx((121.0, 138.5), abs=0.1)


@pytest.mark.parametrize("angle,edge", [(112.0, 128.9), (160.0, 146.4), (135.0, None)])
def test_a_pull_goes_to_the_nearest_edge_keeping_brightness_and_saturation(angle, edge):
    y = _ycc_at(angle)
    t = sb.range_target(y, sb.LAB_RANGE, 1.0)
    if edge is None:
        assert t is None
        return
    assert sb.angle_of(t) == pytest.approx(edge, abs=1e-6)
    assert t[0] == y[0] and sb.chroma_of(t) == pytest.approx(sb.chroma_of(y))
    half = sb.range_target(y, sb.LAB_RANGE, 0.5)
    assert sb.angle_of(half) == pytest.approx((angle + edge) / 2, abs=1e-6)


def test_the_short_way_round():
    assert sb.outside_by(350.0, (10.0, 20.0)) == pytest.approx(-20.0)
    t = sb.range_target(_ycc_at(350.0), (10.0, 20.0), 1.0)
    assert sb.angle_of(t) == pytest.approx(10.0, abs=1e-6)


def test_refusals_and_the_show_text():
    assert "no colour" in sb.refusal_range(_ycc_at(112, rho=2.0), sb.LAB_RANGE)
    assert "doesn't look like skin" in sb.refusal_range(_ycc_at(300), sb.LAB_RANGE)
    assert sb.refusal_range(_ycc_at(112), sb.LAB_RANGE) is None
    assert sb.describe(112.3, sb.LAB_RANGE) == \
        "Skin at 112°: 17° outside your labs' range, towards magenta."
    assert "towards green" in sb.describe(155.0, sb.LAB_RANGE)
    assert sb.describe(135.0, sb.LAB_RANGE) == "Skin at 135°: inside your labs' range."


def test_keep_pixels_drops_eyes_and_shine():
    rgb = np.full((100, 3), 150.0)
    rgb[:5] = 10.0                     # eyes, brows
    rgb[-5:] = 250.0                   # shine
    keep = sb.keep_pixels(rgb)
    assert set(keep) == set(range(5, 95))
    mask = np.zeros(100, bool)
    mask[50:] = True
    assert set(sb.keep_pixels(rgb, mask)) == set(range(50, 95))


def test_solve_converges_rounds_and_never_does_worse():
    A = np.array([[1.0, 0.4, -0.2], [-0.3, 0.9, 0.1], [0.2, -0.1, 1.1]])

    def smooth(v):
        v = np.asarray(v, float)
        return A @ v + 0.004 * v ** 2

    target = smooth([12.5, -7.0, 30.0])
    vals, err, n = sb.solve(smooth, [0, 0, 0], target, 4.0, -100, 100, 0.5)
    assert err < 0.5 and n < 40
    assert all(abs(v * 2 - round(v * 2)) < 1e-9 for v in vals)
    flat = lambda v: np.zeros(3)                             # noqa: E731
    vals, err, _n = sb.solve(flat, [3, 1, 2], [5, 5, 5], 4.0, -100, 100, 0.5)
    assert list(vals) == [3, 1, 2]                            # nothing better found


def test_the_knobs_follow_the_base():
    class Img:
        conversion_inputs = {"mode": "bw"}
    assert sb.knobs_for(Img()) == ("ch_r_shift", "ch_g_shift", "ch_b_shift")
    assert sb.knobs_for(Img(), positive=True) == ("temperature", "tint")
    Img.conversion_inputs = {"mode": "ref"}
    assert sb.knobs_for(Img()) == ("temperature", "tint")


# --- the closed loop on the real render ---------------------------------------

def test_a_pull_lands_on_the_edge_on_a_converted_negative(tmp_path, monkeypatch):
    img, patches = _converted_frame(tmp_path, monkeypatch)
    keys = sb.knobs_for(img)
    measure, _base = img._neutral_measure_fn(patches[0], None, None)
    y0 = sb.ycc(measure({}) / 257.0)
    mask = np.ones(patches[0].shape[:2], bool)
    vals, info = img.solve_skin(patches[0], mask, keys, sb.LAB_RANGE, 1.0)
    assert set(vals) == set(keys)
    assert info["before"] == pytest.approx(sb.angle_of(y0), abs=0.5)
    y1 = sb.ycc(measure(vals) / 257.0)
    assert sb.angle_of(y1) == pytest.approx(sb.LAB_RANGE[0], abs=1.0)
    assert y1[0] == pytest.approx(y0[0], abs=1.0)
    assert sb.chroma_of(y1) == pytest.approx(sb.chroma_of(y0), rel=0.03)
    again, _ = img.solve_skin(patches[0], mask, keys, sb.LAB_RANGE, 1.0)
    assert again == vals                                       # the same pull, the same answer
    half, info = img.solve_skin(patches[0], mask, keys, sb.LAB_RANGE, 0.5)
    want = (info["before"] + sb.LAB_RANGE[0]) / 2
    assert sb.angle_of(sb.ycc(measure(half) / 257.0)) == pytest.approx(want, abs=1.5)
    inside, info = img.solve_skin(patches[0], mask, keys, (100.0, 150.0), 1.0)
    assert inside is None and info["inside"]


def test_a_pull_on_a_positive_moves_temperature_and_tint(tmp_path, monkeypatch):
    from core.ccr_image import CCRImage
    a = np.zeros((200, 300, 3), np.uint8)
    a[:] = (170, 150, 120)
    a[50:150, 100:200] = (150, 140, 205)       # a pinkish face (BGR)
    p = str(tmp_path / "a.png")
    cv2.imwrite(p, a)
    img = CCRImage(p)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "positive_mode", True)
    keys = sb.knobs_for(img, True)
    patch = img.resized_raw[60:140, 110:190]
    vals, info = img.solve_skin(patch, np.ones(patch.shape[:2], bool), keys, sb.LAB_RANGE, 1.0)
    assert set(vals) == {"temperature", "tint"}
    assert info["after"] == pytest.approx(sb.LAB_RANGE[0], abs=1.5)


# --- the tool on the canvas ---------------------------------------------------

@pytest.fixture
def win(tmp_path, monkeypatch):
    from ui.main_window import MainWindow
    img, _patches = _converted_frame(tmp_path, monkeypatch)
    w = MainWindow()
    _KEEP.append(w)
    w.resize(1400, 900)
    w.show()
    ip = w.image_preview
    ip._skin_settings = _Store()
    ip.update_preview(0)
    w.sliders_panel.set_current_idx(0)
    ip.scopes_panel._toggle_btn.setChecked(True)
    ip.scopes_panel._on_toggle(True)
    _app.processEvents()
    hints = []
    monkeypatch.setattr(w.sliders_panel, "set_temporary_hint",
                        lambda t, duration=0: hints.append(t))
    w._hints = hints
    yield w
    ip.set_skin_mode(False)


def _face(ip, k=0):
    """Scene centre and radius of patch k."""
    br = ip._picture_scene_rect()
    return (QPointF(br.left() + br.width() * ((24 + 34 * k) / 260), br.top() + br.height() / 2),
            br.width() * (10 / 260))


def _draw(ip, k=0, r_scale=1.0):
    c, r = _face(ip, k)
    end = QPointF(c.x() + r * r_scale, c.y())
    ip.skin_press(c)
    ip.skin_move(end)
    ip.skin_release(end)


def _click(ip, k=0):
    """A click on circle k's button."""
    c, _r = _face(ip, k)
    ip.skin_press(c)
    ip.skin_release(c)


def test_the_face_sits_beside_the_sample_circle(win):
    ip = win.image_preview
    acts = ip.toolbar.actions()
    assert acts[acts.index(ip.skin_action) - 1] is ip.sample_action
    assert ip.skin_action.toolTip().startswith("Skin:")
    assert not ip.skin_action.icon().isNull()


def test_show_reads_the_face_and_moves_nothing(win):
    ip, img = win.image_preview, ccr_backend.images[0]
    before = dict(img.adjustment_settings)
    ip.skin_action.trigger()
    assert ip.skin_mode and "Drag a circle on a face" in win._hints[-1]
    assert "skin (drag a circle on a face)" in ip.scopes_panel._toggle_btn.text()
    _draw(ip)
    assert len(ip.skin_circles) == 1
    assert ip.scopes_panel._toggle_btn.text().endswith("Scopes  ·  skin")
    assert "Click the circle to read" in win._hints[-1]
    assert [lab.text for lab in ip._skin_labels if lab.isVisible()] == ["Read"]
    _click(ip)
    assert win._hints[-1].startswith("Skin at 112°") and "towards magenta" in win._hints[-1]
    assert ip._skin_result_item.isVisible()
    assert ip._skin_result_item.text == "Skin 112° · 17° towards magenta"
    assert dict(img.adjustment_settings) == before


def test_every_drag_adds_a_circle_a_right_click_removes_one(win):
    ip = win.image_preview
    ip.set_skin_mode(True)
    _draw(ip, 0)
    _draw(ip, 1)
    assert len(ip.skin_circles) == 2
    _draw(ip, 1, r_scale=0.01)                 # too small to be a circle: a click
    assert len(ip.skin_circles) == 2
    c1, _r = _face(ip, 1)
    ip.skin_hover(c1)
    assert ip._skin_hot == 1 and ip._skin_labels[1].hot
    assert ip.skin_remove_at(c1)
    assert len(ip.skin_circles) == 1
    assert not ip.skin_remove_at(c1)           # nothing there now
    c2, _r = _face(ip, 2)
    ip.skin_press(c2)
    ip.skin_release(c2)                         # a click off the circles: nothing
    assert len(ip.skin_circles) == 1
    ip._on_escape_key()
    assert not ip.skin_mode and ip.skin_circles == []
    assert not ip.skin_action.isChecked()


def test_a_click_reads_every_circle_together(win):
    ip = win.image_preview
    ip.set_skin_mode(True)
    _draw(ip, 0)
    _click(ip, 0)
    one = ip._skin_result_item.text
    _draw(ip, 1)
    assert not ip._skin_result_item.isVisible()            # changed: no old answer
    _click(ip, 1)
    two = ip._skin_result_item.text
    assert one != two and two.startswith("Skin 10")         # 112 and 103 together


def test_the_skin_tool_and_the_sample_circle_take_turns(win):
    ip = win.image_preview
    ip.set_sample_mode(True)
    ip.set_skin_mode(True)
    assert ip.skin_mode and not ip.sample_mode
    ip.set_sample_mode(True)
    assert ip.sample_mode and not ip.skin_mode
    ip.set_sample_mode(False)


def test_pull_moves_the_printer_lights_once_and_repeats_the_same(win):
    ip, img = win.image_preview, ccr_backend.images[0]
    ip._skin_settings.setValue("skin/mode", "pull")
    ip.set_skin_mode(True)
    _draw(ip)
    assert [lab.text for lab in ip._skin_labels if lab.isVisible()] == ["Pull"]
    assert not any(img.adjustment_settings.get(k) for k in ("ch_r_shift", "ch_g_shift"))
    _click(ip)
    def now():
        st = img.adjustment_settings
        return {k: st.get(k) for k in ("ch_r_shift", "ch_g_shift", "ch_b_shift")}
    moved = now()
    assert any(v for v in moved.values())
    assert win._hints[-1].startswith("Skin pulled from 112° to 12")
    assert "Printer Lights" in win._hints[-1]
    assert ip._skin_result_item.text.startswith("Pulled 112° → 12")
    ip.update_preview(0)
    _app.processEvents()
    assert sb.angle_of(ip.skin_reading()) == pytest.approx(sb.LAB_RANGE[0], abs=1.5)
    # a second pull, and the same pull at another amount, start from the same values
    ip.set_skin_option("amount", 50)
    half = now()
    assert half != moved
    ip.set_skin_option("amount", 100)
    assert now() == moved
    # the video range: pulled only to its own edge
    ip.set_skin_option("range", "video")
    assert ip.scopes_panel.vectorscope.skin_line == "video"
    assert "to 121°" in win._hints[-1] or "to 120°" in win._hints[-1]


def test_pull_refuses_with_an_area_layer_and_show_refuses_black_and_white(win, monkeypatch):
    ip, img = win.image_preview, ccr_backend.images[0]
    ip._skin_settings.setValue("skin/mode", "pull")
    ip.set_skin_mode(True)
    monkeypatch.setattr(img, "active_area_id", "a1", raising=False)
    before = dict(img.adjustment_settings)
    _draw(ip)
    _click(ip)
    assert "Whole Image layer" in win._hints[-1]
    assert dict(img.adjustment_settings) == before
    ip.set_skin_mode(False)
    monkeypatch.setattr(type(img), "renders_monochrome", lambda self: True)
    ip.skin_action.trigger()
    assert not ip.skin_mode and not ip.skin_action.isChecked()
    assert "black and white" in win._hints[-1]


def test_the_right_click_menu(win):
    ip = win.image_preview
    menu = ip.skin_menu()
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts[:3] == ["When you draw a circle", "Show it on the vectorscope",
                         "Pull it into the skin range"]
    assert "Your labs' (129–146°)" in texts
    assert "Around the video skin line (121–138°)" in texts
    amounts = [a for a in menu.actions() if a.text().endswith(" %")]
    assert [a.text() for a in amounts] == ["100 %", "75 %", "50 %", "25 %"]
    assert not any(a.isEnabled() for a in amounts)              # greyed with Show
    pull = [a for a in menu.actions() if a.text() == "Pull it into the skin range"][0]
    pull.trigger()
    assert ip._skin_settings["skin/mode"] == "pull"
    menu = ip.skin_menu()
    amounts = [a for a in menu.actions() if a.text().endswith(" %")]
    assert all(a.isEnabled() for a in amounts)
    amounts[2].trigger()
    assert ip.skin_options() == ("pull", "lab", 0.5)


def test_the_vectorscope_draws_the_range(win):
    from widgets.scopes_panel import VectorscopeWidget
    vs = VectorscopeWidget()
    vs.resize(200, 200)
    vs.set_data(np.zeros((128, 128), np.float32))

    def at(deg, img):
        sq = vs._square()
        r = sq.width() / 2 * 0.6
        x = sq.center().x() + r * math.cos(math.radians(deg))
        y = sq.center().y() - r * math.sin(math.radians(deg))
        c = img.pixelColor(int(x), int(y))
        return c.red() + c.green() + c.blue()

    vs.skin_line = "lab"
    lab = vs.grab().toImage()
    assert at(137.0, lab) > at(160.0, lab) + 10                  # inside the wedge is lit
    vs.skin_line = "video"
    video = vs.grab().toImage()
    assert at(125.0, video) > at(125.0, lab) + 10


def test_a_pull_reads_the_right_place_on_a_mirrored_or_sharp_view(win):
    """The first build mapped a circle twice (the item's own transform, then
    the display transform): on a mirrored frame it read the other side, on a
    sharp (Retina) view twice as far out, and a pull did nothing."""
    ip, img = win.image_preview, ccr_backend.images[0]
    ip._skin_settings.setValue("skin/mode", "pull")
    ip.mirror_horizontal()
    _app.processEvents()
    assert ip.current_horizontal_flip
    ip.set_skin_mode(True)
    br = ip._picture_scene_rect()
    # patch 0 is now on the RIGHT of the picture
    c = QPointF(br.right() - br.width() * (24 / 260), br.top() + br.height() / 2)
    r = br.width() * (10 / 260)
    ip.skin_press(c)
    ip.skin_move(QPointF(c.x() + r, c.y()))
    ip.skin_release(QPointF(c.x() + r, c.y()))
    patch, mask = ip._skin_patch(img)
    measure, _ = img._neutral_measure_fn(patch, None, None)
    assert sb.angle_of(sb.ycc(measure({}) / 257.0)) == pytest.approx(112.3, abs=2.0)
    ip.skin_press(c)
    ip.skin_release(c)
    assert ip._skin_result_item.text.startswith("Pulled 112°")
    # a hi-res pixmap is prescaled: the mapping must not see it
    ip._item_prescale = 0.5
    p2, m2 = ip._skin_patch(img)
    assert p2.shape == patch.shape and np.array_equal(m2, mask)
