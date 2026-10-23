#!/usr/bin/env python3
"""Highlights x / Shadows x / Punch x: the measured tone tools
(spec/x-tone-tools.md). Also hosts the small panel helpers the popup
tests share (they lived in the removed Highlights/Shadows range tests)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import x_tones as et  # noqa: E402
from core.ccr_processor import adjust_image, adjust_image_opencl, encode_window  # noqa: E402

# the measured exports (level-16 HALD, positive mode), read at five colours:
# three greys and two colours, input (r, g, b) / 255 =
# (51,51,51) (119,119,119) (187,187,187) (40,90,160) (200,150,110).
MEASURED = {
    'base': [(0.2475, 0.301, 0.2844), (0.5944, 0.6314, 0.6199), (0.8136, 0.8286, 0.8237), (0.2952, 0.509, 0.7478), (0.8554, 0.7356, 0.6063)],
    'hx+50': [(0.2703, 0.3351, 0.3152), (0.6791, 0.7154, 0.7038), (0.8609, 0.8713, 0.8678), (0.3693, 0.5875, 0.8224), (0.8977, 0.8025, 0.6906)],
    'hx+100': [(0.2941, 0.3706, 0.3472), (0.7481, 0.7817, 0.7715), (0.8941, 0.9019, 0.8997), (0.4473, 0.6572, 0.8741), (0.9266, 0.8524, 0.7581)],
    'hx-50': [(0.2257, 0.2684, 0.2551), (0.4949, 0.5288, 0.518), (0.7508, 0.7706, 0.7642), (0.2443, 0.4244, 0.6449), (0.7947, 0.6483, 0.5053)],
    'hx-100': [(0.2051, 0.2376, 0.2274), (0.3881, 0.4134, 0.4053), (0.6683, 0.6936, 0.6855), (0.211, 0.3424, 0.528), (0.7076, 0.5393, 0.3913)],
    'sx+50': [(0.3621, 0.4218, 0.4034), (0.6928, 0.7225, 0.713), (0.851, 0.8607, 0.8578), (0.4674, 0.6236, 0.8192), (0.8866, 0.8023, 0.7012)],
    'sx+100': [(0.4889, 0.5477, 0.5294), (0.7695, 0.7927, 0.785), (0.8812, 0.8855, 0.8841), (0.6021, 0.7165, 0.8666), (0.9081, 0.8523, 0.7748)],
    'sx-50': [(0.1553, 0.198, 0.1847), (0.4755, 0.5181, 0.5045), (0.7664, 0.7889, 0.7818), (0.0853, 0.3828, 0.6393), (0.8121, 0.6487, 0.4901)],
    'sx-100': [(0.0866, 0.119, 0.109), (0.35, 0.3915, 0.3784), (0.7072, 0.7403, 0.7296), (0.0, 0.2662, 0.5205), (0.7495, 0.5391, 0.3572)],
    'px+50': [(0.2453, 0.2986, 0.282), (0.6189, 0.6563, 0.6443), (0.8395, 0.8534, 0.8487), (0.3039, 0.525, 0.7694), (0.8791, 0.7639, 0.6376)],
    'px+100': [(0.2427, 0.2963, 0.2798), (0.6425, 0.6805, 0.6686), (0.8654, 0.8775, 0.8734), (0.3125, 0.5409, 0.7908), (0.9026, 0.7919, 0.6679)],
    'px-50': [(0.25, 0.3034, 0.2868), (0.5707, 0.6061, 0.5946), (0.7881, 0.804, 0.7985), (0.2858, 0.4926, 0.7261), (0.8319, 0.7074, 0.5749)],
    'px-100': [(0.2522, 0.3058, 0.2892), (0.5456, 0.581, 0.5698), (0.7615, 0.7787, 0.7736), (0.277, 0.4763, 0.7041), (0.8076, 0.6783, 0.5427)],
}
SLIDER = {"hx": "highlights_x", "sx": "shadows_x", "px": "punch_x"}


def _img(seed=0, shape=(40, 50, 3)):
    return (np.random.default_rng(seed).random(shape) * 65535).astype(np.uint16)


def _xtone_space():
    d = et._load()
    return d["black"].astype(np.float64), d["white"].astype(np.float64)


# --------------------------------------------------------------------------- #
# against the measured render
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("tool", ["hx", "sx", "px"])
@pytest.mark.parametrize("amount", [-100, -50, 50, 100])
def test_matches_the_measured_exports(tool, amount):
    """the measured rendered colours, mapped into FreeCCR's black..white, then the
    tool: the measured own result, in the same units — greys within 0.5/255, the
    two colours within 2/255 (one sits where the measured render clips a channel)."""
    b, w = _xtone_space()
    base = np.array(MEASURED["base"], np.float64)
    want = np.array(MEASURED[f"{tool}{'+' if amount > 0 else '-'}{abs(amount)}"], np.float64)
    u = ((base - b) / (w - b)).astype(np.float32)[None]
    got = et.apply_x_tones(u, **{SLIDER[tool]: amount})[0].astype(np.float64)
    want = np.clip((want - b) / (w - b), 0, 1)
    np.testing.assert_allclose(got[:3], want[:3], atol=0.5 / 255)
    np.testing.assert_allclose(got[3:], want[3:], atol=2 / 255)


def test_zero_is_an_exact_no_op():
    img = _img()
    a = adjust_image(img, highlights=20, contrast=10)
    for kw in ({}, {"xtone_shape": {"hx_keep": True}},
               {"highlights_x": 0, "shadows_x": 0, "punch_x": 0}):
        np.testing.assert_array_equal(a, adjust_image(img, highlights=20, contrast=10, **kw))
        np.testing.assert_array_equal(
            adjust_image_opencl(img, highlights=20, contrast=10),
            adjust_image_opencl(img, highlights=20, contrast=10, **kw))


def test_ends_behave_like_measured render():
    x = np.array([0.0, 1.0], np.float32)
    for v in (100, -100):
        lo, hi = et.grey_curve(x, highlights_x=v)
        assert abs(lo) < 0.003 and abs(hi - 1.0) < 0.015      # black held, white barely moves
        lo, hi = et.grey_curve(x, shadows_x=v)
        assert abs(hi - 1.0) < 0.003                          # white held
    assert et.grey_curve(x, shadows_x=100)[0] == pytest.approx(0.072, abs=0.01)   # the measured fade
    assert et.grey_curve(x, shadows_x=-100)[0] < 0.003                             # and crush


def test_keep_pins_the_ends_exactly():
    x = np.array([0.0, 1.0], np.float32)
    assert et.grey_curve(x, shadows_x=100, shape={"sx_keep": True})[0] == 0.0
    assert et.grey_curve(x, highlights_x=100, shape={"hx_keep": True})[1] == 1.0
    p = et.grey_curve(x, punch_x=100, shape={"px_keep_black": True, "px_keep_white": True})
    assert p[0] == 0.0 and p[1] == 1.0
    # ...and only near the end: the midtones are the measured
    m = np.array([0.5], np.float32)
    assert (et.grey_curve(m, shadows_x=100, shape={"sx_keep": True})[0]
            == pytest.approx(et.grey_curve(m, shadows_x=100)[0], abs=1e-6))


def test_range_moves_where_the_tool_reaches():
    x = np.array([0.2], np.float32)
    lift = {r: et.grey_curve(x, highlights_x=100, shape={"hx_range": r})[0] - 0.2
            for r in (0, 50, 100)}
    assert lift[0] < lift[50] < lift[100]            # wider reaches further down
    y = np.array([0.7], np.float32)
    lift = {r: et.grey_curve(y, shadows_x=100, shape={"sx_range": r})[0] - 0.7
            for r in (0, 50, 100)}
    assert lift[0] < lift[50] < lift[100]            # wider reaches further up
    assert et.normalize_xtone_shape({"hx_range": 50, "sx_keep": 0}) is None


@pytest.mark.parametrize("key,opts", [
    ("highlights_x", ({}, {"hx_range": 0}, {"hx_range": 100}, {"hx_keep": True})),
    ("shadows_x", ({}, {"sx_range": 0}, {"sx_range": 100}, {"sx_keep": True})),
    ("punch_x", ({}, {"px_keep_black": True, "px_keep_white": True})),
])
@pytest.mark.parametrize("v", [100, 50, -50, -100])
def test_grey_axis_never_turns_back(key, opts, v):
    """No tone ever comes out darker than a darker one, beyond a 0.5/255
    ripple of the grid's interpolation where the chain clips at black."""
    x = np.linspace(0, 1, 801, dtype=np.float32)
    for o in opts:
        y = et.grey_curve(x, shape=o, **{key: v})
        back = float((np.maximum.accumulate(y) - y).max())
        assert back <= 0.5 / 255, (o, back)


def test_tools_add_up_in_order():
    img = (np.random.default_rng(4).random((20, 30, 3))).astype(np.float32)
    both = et.apply_x_tones(img, highlights_x=40, shadows_x=-30)
    lone = et.apply_x_tones(img, highlights_x=40)
    assert not np.allclose(both, lone)
    assert np.all((both >= 0) & (both <= 1))


def test_highlights_and_shadows_ignore_an_old_shape():
    img = _img(3)
    a = adjust_image(img, highlights=50, shadows=-30)
    b = adjust_image(img, highlights=50, shadows=-30,
                     tone_shape={"hl_keep": True, "hl_range": 20, "sh_keep": True})
    np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("ws", [False, True])
@pytest.mark.parametrize("kw", [
    dict(highlights_x=60, shadows_x=-40),
    dict(highlights_x=-80, punch_x=50, highlights=30, exposure=30, brightness=-16,
         ch_r_shift=12, ch_master_gain=10),
    dict(shadows_x=40, contrast=20, saturation=15, blackpoint=10, ch_g_gain=8,
         xtone_shape={"sx_keep": True, "sx_range": 70}),
    dict(punch_x=70, balance_r=20, cineon_log=True, shadows=20),
])
def test_gpu_matches_cpu(ws, kw):
    rng = np.random.default_rng(1)
    img = (encode_window(rng.uniform(-0.1, 1.1, (30, 40, 3)).astype(np.float32))
           if ws else _img(2, (30, 40, 3)))
    a = adjust_image(img, ws_windowed=ws, **kw)
    b = adjust_image_opencl(img, ws_windowed=ws, **kw)
    np.testing.assert_allclose(a.astype(np.int64), b.astype(np.int64), atol=2)


def test_a_large_image_goes_through_in_chunks(monkeypatch):
    monkeypatch.setattr(et, "CHUNK", 1000)
    img = np.random.default_rng(5).random((60, 70, 3)).astype(np.float32)
    a = et.apply_x_tones(img, highlights_x=30, punch_x=-20)
    monkeypatch.setattr(et, "CHUNK", 1 << 20)
    np.testing.assert_array_equal(a, et.apply_x_tones(img, highlights_x=30, punch_x=-20))


# --------------------------------------------------------------------------- #
# the image passes its settings
# --------------------------------------------------------------------------- #

class _Stub:
    def __init__(self, settings, areas=()):
        self._ws_windowed = False
        self.converted = False
        self.adjustment_settings = settings
        self.contrast_base = 0
        self.temperature_base = 0
        self.brightness_base = 0
        self.exposure_base = 0.0
        self.color_profile = "color"
        self.area_layers = list(areas)
        self.tint_balance_factor = 1.0

    def _apply_dust_removal(self, image, ws_windowed=False):
        return image

    def _to_grayscale(self, image):
        return image


SHAPE = {"hx_range": 30, "sx_keep": True}


def _capture(monkeypatch):
    import core.ccr_image as mod
    seen = {}

    def cap(image, *a, **kw):
        seen.update(kw)
        return image
    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    return seen


def test_apply_adjustments_passes_the_tools(monkeypatch):
    from core.ccr_image import CCRImage
    seen = _capture(monkeypatch)
    CCRImage.apply_adjustments(_Stub({"highlights_x": 20, "punch_x": -5,
                                      "xtone_shape": SHAPE}), _img())
    assert (seen["highlights_x"], seen["shadows_x"], seen["punch_x"]) == (20, 0, -5)
    assert seen["xtone_shape"] == SHAPE


def test_area_layer_passes_its_own(monkeypatch):
    from core.ccr_image import CCRImage
    seen = _capture(monkeypatch)
    CCRImage._adjust_for_area(_Stub({}), _img(), {"shadows_x": 30, "xtone_shape": SHAPE})
    assert seen["shadows_x"] == 30 and seen["xtone_shape"] == SHAPE


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def _wire(panel, monkeypatch, img):
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: img)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "set_active_settings_by_index",
                        lambda i, s, reprocess=True: setattr(img, "adjustment_settings", s))
    monkeypatch.setattr(ccr_backend, "get_active_settings_by_index",
                        lambda i: img.adjustment_settings)
    mw = type("MW", (), {"image_preview": type("IP", (), {
        "update_preview": lambda s, i: None})()})()
    monkeypatch.setattr(panel, "parent", lambda: type("P", (), {"parent": lambda s: mw})())
    panel.current_idx = 0


class _Img:
    active_area_id = None
    converted = True
    crop_rect = None

    def __init__(self, settings=None):
        self.adjustment_settings = settings or {}
        self.undo = 0
        self.area_layers = []

    def push_undo_state(self):
        self.undo += 1

    def get_area(self, _id):
        return None

    def update_thumbnail_and_preview(self):
        pass


def _row_label(panel, key):
    i = panel.adjustment_keys.index(key)
    return panel._xtone_labels[{"highlights_x": "hx", "shadows_x": "sx",
                                "punch_x": "px"}[key]], panel.sliders[i]


def test_three_sliders_after_black_point(panel):
    from PySide6.QtCore import Qt
    keys = panel.adjustment_keys
    i = keys.index("black_point")
    assert keys[i + 1:i + 4] == ["highlights_x", "shadows_x", "punch_x"]
    assert keys[i + 4] == "contrast"
    for key, text in (("highlights_x", "Highlights L"), ("shadows_x", "Shadows ⅃"),
                      ("punch_x", "Punch ×")):
        lbl, slider = _row_label(panel, key)
        assert lbl.text() == text
        assert slider.value_range() == (-100, 100)
        assert lbl.contextMenuPolicy() == Qt.CustomContextMenu
        assert "Right-click" in lbl.toolTip() and "the measured render" in lbl.toolTip()


def test_highlights_and_shadows_have_no_popup_any_more(panel):
    from PySide6.QtCore import Qt
    for key in ("highlights", "shadows"):
        lay = getattr(panel, f"{key}_slider_layout")
        assert lay.itemAt(0).widget().contextMenuPolicy() != Qt.CustomContextMenu


def test_popup_edit_writes_options_and_survives_a_slider_move(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_xtone_popup("sx", panel.mapToGlobal(panel.rect().center()))
    pop = panel._xtone_popup
    pop.checks["sx_keep"].setChecked(True)
    pop.range_slider.setValue(30)
    assert img.adjustment_settings["xtone_shape"] == dict(
        et.XTONE_SHAPE_DEFAULT, sx_keep=True, sx_range=30)
    assert panel._xtone_labels["sx"].font().underline()
    assert not panel._xtone_labels["hx"].font().underline()
    pop.close()
    assert img.undo == 1                                    # one undo step
    panel.sliders[panel.adjustment_keys.index("contrast")].setValue(12)
    assert img.adjustment_settings["xtone_shape"]["sx_keep"] is True
    panel._open_xtone_popup("sx", panel.mapToGlobal(panel.rect().center()))
    panel._xtone_popup.reset()
    assert "xtone_shape" not in img.adjustment_settings
    panel._xtone_popup.close()


def test_punch_popup_has_both_keeps(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_xtone_popup("px", panel.mapToGlobal(panel.rect().center()))
    pop = panel._xtone_popup
    assert set(pop.checks) == {"px_keep_black", "px_keep_white"} and pop.range_slider is None
    pop.checks["px_keep_white"].setChecked(True)
    assert img.adjustment_settings["xtone_shape"]["px_keep_white"] is True
    pop.close()


def test_paste_row_and_apply(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "xtone_shape": {"hx_keep": True, "hx_range": 30}}
    rows = paste_options(clip, lambda k: 0)
    row = [r for r in rows if r[1] == "xtone_shape"]
    assert row and "keep white" in row[0][2] and "range 30" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1                                   # onto a non-current image
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"xtone_shape"})
    assert img.adjustment_settings["xtone_shape"]["hx_keep"] is True
    assert img.adjustment_settings["contrast"] == 5


def test_copy_carries_the_options(panel, monkeypatch):
    img = _Img({"shadows_x": 10, "xtone_shape": {"sx_keep": True}})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 5                                   # copy from a non-current image
    panel.copy_settings_from_index(0)
    assert panel.clipboard["xtone_shape"]["sx_keep"] is True
    assert panel.clipboard["adjustments"]["shadows_x"] == 10


def _sync(panel, monkeypatch, src, dst, tone):
    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SYNC_GROUPS
    for im in (src, dst):
        im.crop_rect = None
        im.crop_angle = 0.0
        im.color_profile = "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: gid == "sat" for gid, _l, _k in SYNC_GROUPS}
    panel._sync_group_selection["tone"] = tone
    panel._perform_sync_to_all()


def test_sync_copies_tools_and_options_with_tone_group(panel, monkeypatch):
    src = _Img({"punch_x": 30, "xtone_shape": {"px_keep_black": True}})
    dst = _Img({"punch_x": 0})
    _sync(panel, monkeypatch, src, dst, tone=True)
    assert dst.adjustment_settings["punch_x"] == 30
    assert dst.adjustment_settings["xtone_shape"]["px_keep_black"] is True


def test_sync_without_tone_group_keeps_target_options(panel, monkeypatch):
    src = _Img({"saturation": 40})
    dst = _Img({"saturation": 0, "xtone_shape": {"sx_keep": True}})
    _sync(panel, monkeypatch, src, dst, tone=False)
    assert dst.adjustment_settings["saturation"] == 40
    assert dst.adjustment_settings["xtone_shape"]["sx_keep"] is True


def test_l_sliders_sit_under_the_controls_they_measure():
    """Highlights L directly under Highlights, Shadows ⅃ directly under
    Shadows (shown order; the creation order the keys zip with is unchanged)."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    p = SlidersPanel()
    p.resize(380, 2400)
    p.show()
    QApplication.processEvents()
    rows = sorted(((lay.itemAt(0).widget().mapTo(p, lay.itemAt(0).widget().rect().topLeft()).y(),
                    lay.itemAt(0).widget().text())
                   for lay in (p.highlights_slider_layout, p.highlights_x_slider_layout,
                               p.white_point_slider_layout, p.shadows_slider_layout,
                               p.shadows_x_slider_layout, p.black_point_slider_layout,
                               p.punch_x_slider_layout)))
    assert [t for _y, t in rows] == ["Highlights", "Highlights L", "White Point", "Shadows",
                                     "Shadows ⅃", "Black Point", "Punch ×"]
    keys = SlidersPanel.ADJUSTMENT_KEYS
    assert keys.index("highlights_x") > keys.index("black_point")      # zip order kept
