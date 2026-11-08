#!/usr/bin/env python3
"""Film Look auto balance per frame (experimental) and colour-only looks
(spec/film-look-auto-balance.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import look_balance as lb  # noqa: E402
from core import look_lut as ll  # noqa: E402
from core.ccr_processor import (CH_SLIDER_DIV, adjust_image,  # noqa: E402
                                adjust_image_opencl, apply_cineon_to_workspace,
                                encode_window)
from test_film_look import _StubImage, _base, _curvy_table, _decode_window, _lut  # noqa: E402

BAL = (np.array([-0.047, -0.026, 0.073]), 0.56)


def _blut():
    t = _curvy_table()
    return ll.LookLUT(t, (-0.25,) * 3, (1.25,) * 3, title="b", balance=BAL)


# --------------------------------------------------------------------------- #
# the cube's balance line
# --------------------------------------------------------------------------- #

def test_balance_line_round_trips(tmp_path):
    p = tmp_path / "b.cube"
    ll.write_cube(str(p), _curvy_table(5), (-0.25,) * 3, (1.25,) * 3, balance=BAL,
                  comments=["FREECCR_BALANCE 9 9 9 9", "a note"])   # a stale line is replaced
    lut = ll.read_cube(str(p))
    np.testing.assert_allclose(lut.balance[0], BAL[0], atol=1e-6)
    assert lut.balance[1] == pytest.approx(0.56)
    assert open(p).read().count(ll.BALANCE_MARK) == 1
    ll.write_cube(str(p), _curvy_table(5), (-0.25,) * 3, (1.25,) * 3)
    assert ll.read_cube(str(p)).balance is None
    with open(p, "a") as fh:                       # a malformed line is ignored
        fh.write("# FREECCR_BALANCE 1 two 3 0.5\n")
    assert ll.read_cube(str(p)).balance is None


def test_alpha_is_kept_in_range():
    assert ll.LookLUT(_curvy_table(3), balance=((0, 0, 0), 5.0)).balance[1] == ll.ALPHA_MAX
    assert ll.LookLUT(_curvy_table(3), balance=((0, 0, 0), -1)).balance[1] == ll.ALPHA_MIN


# --------------------------------------------------------------------------- #
# the model
# --------------------------------------------------------------------------- #

def test_offsets_are_alpha_times_the_cast_and_sum_to_zero():
    mean = np.array([0.40, 0.45, 0.62])
    off = lb.balance_offsets(mean, BAL)
    assert off.sum() == pytest.approx(0.0, abs=1e-12)
    np.testing.assert_allclose(off, 0.56 * (BAL[0] - (mean - mean.mean())), atol=1e-12)
    assert lb.balance_offsets(None, BAL) is None and lb.balance_offsets(mean, None) is None


def test_fit_balance_recovers_the_lab():
    rng = np.random.default_rng(0)
    t, a = np.array([-0.05, -0.02, 0.07]), 0.55
    means = rng.uniform(0.3, 0.7, (9, 3))
    offs = [a * (t - lb.colour(m)) + rng.normal(0, 0.1) for m in means]   # + exposure
    T, alpha = lb.fit_balance(means, offs)
    np.testing.assert_allclose(T, t, atol=1e-9)
    assert alpha == pytest.approx(a, abs=1e-9)


def test_picture_mean_density_uses_the_crop_the_window_and_the_map():
    h, w = 120, 160
    d = np.zeros((h, w, 3), np.float32) + np.float32([0.3, 0.4, 0.5])
    yy, xx = np.mgrid[0:h, 0:w]
    d[..., 0] += 0.2 * xx / w                     # smooth: every 4th pixel is enough
    d[:10] = 3.0                                  # holder past the window: left out
    base = encode_window(d)
    full = _decode_window(base)[10:].reshape(-1, 3).mean(0)
    got = lb.picture_mean_density(base, (0, 0, 1, 1))
    np.testing.assert_allclose(got, full, atol=1e-3)
    left = lb.picture_mean_density(base, (0.0, 0.2, 0.5, 1.0))
    assert left[0] < got[0]                       # the crop is used
    fl = ll.FrameLook(_lut(), "scale", (2.0, 1.0, 0.5))
    np.testing.assert_allclose(lb.picture_mean_density(base, (0, 0, 1, 1), fl),
                               got * (2.0, 1.0, 0.5), atol=2e-3)
    assert lb.picture_mean_density(encode_window(np.full((40, 40, 3), 3.0, np.float32))) is None


def test_picture_rect_prefers_the_crop_then_convert_picture():
    class F:
        crop_rect = None
        conversion_inputs = {}
    f = F()
    assert lb.picture_rect(f) == lb.MIDDLE
    f.conversion_inputs = {"picture": [0.1, 0.2, 0.8, 0.9]}
    assert lb.picture_rect(f) == (0.1, 0.2, 0.8, 0.9)
    f.crop_rect = (0.0, 0.0, 0.5, 0.5)
    assert lb.picture_rect(f) == (0.0, 0.0, 0.5, 0.5)


# --------------------------------------------------------------------------- #
# apply_adjustments: the hidden offsets
# --------------------------------------------------------------------------- #

def _frame(settings, ci=None, preview=None):
    s = _StubImage(settings)
    s.conversion_inputs = ci if ci is not None else {
        "mode": "bw", "bw": ((50000.0,) * 3, None), "density": True}
    s.crop_rect = None
    d = np.zeros((40, 60, 3), np.float32) + np.float32([0.30, 0.42, 0.66])
    s.resized_raw = encode_window(d) if preview is None else preview
    return s


def _capture(monkeypatch, img, lut, on=True, image=None):
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    seen = {}

    def cap(image, *args, **kw):
        seen["brightness"] = args[3]
        seen.update(kw)
        return image
    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    monkeypatch.setattr(ccr_backend, "auto_gain", True, raising=False)
    monkeypatch.setattr(ccr_backend, "look_auto_balance", on, raising=False)
    monkeypatch.setattr(ll, "resolve_look", lambda name: lut)
    CCRImage.apply_adjustments(img, img.resized_raw if image is None else image)
    return seen


def test_auto_balance_adds_hidden_shifts_when_on(monkeypatch):
    lut = _blut()
    img = _frame({"look_lut": "x.cube", "ch_r_shift": 4, "ch_b_shift": -2})
    seen = _capture(monkeypatch, img, lut)
    want = lb.balance_offsets(np.array([0.30, 0.42, 0.66]), BAL) * CH_SLIDER_DIV
    got = np.array([seen["ch_r_shift"] - 4, seen["ch_g_shift"], seen["ch_b_shift"] + 2])
    np.testing.assert_allclose(got, want, atol=0.05)
    assert img.adjustment_settings["ch_r_shift"] == 4          # the sliders keep theirs
    # measured on the PREVIEW base whatever is rendered (a zoom tile)
    tile = encode_window(np.full((8, 8, 3), 0.9, np.float32))
    seen2 = _capture(monkeypatch, img, lut, image=tile)
    assert seen2["ch_b_shift"] == pytest.approx(seen["ch_b_shift"])


@pytest.mark.parametrize("case", ["off", "no data", "reference", "no anchor", "no look"])
def test_auto_balance_does_nothing_otherwise(monkeypatch, case):
    lut = _lut() if case == "no data" else _blut()
    ci = {"reference": {"mode": "ref"},
          "no anchor": {"mode": "bw", "bw": (None, None)}}.get(case)
    settings = {} if case == "no look" else {"look_lut": "x.cube"}
    seen = _capture(monkeypatch, _frame(settings, ci), lut, on=case != "off")
    assert (seen["ch_r_shift"], seen["ch_g_shift"], seen["ch_b_shift"]) == (0, 0, 0)


def test_the_balance_is_colour_only_not_brightness(monkeypatch):
    seen = _capture(monkeypatch, _frame({"look_lut": "x.cube"}), _blut())
    assert seen["ch_r_shift"] + seen["ch_g_shift"] + seen["ch_b_shift"] == \
        pytest.approx(0.0, abs=1e-9)
    assert seen["ch_master_shift"] == 0


# --------------------------------------------------------------------------- #
# colour-only looks
# --------------------------------------------------------------------------- #

def _lin(v):
    return ll._decode(np.asarray(v, np.float32))


def test_names():
    assert ll.split_look_name("P.cube|colour") == ("P.cube", True)
    assert ll.split_look_name("P.cube") == ("P.cube", False)
    assert ll.colour_only_name("P.cube") == "P.cube|colour"
    assert ll.colour_only_name("P.cube|colour") == "P.cube|colour"
    assert ll.is_full_look("P.cube") and not ll.is_full_look("P.cube|colour")
    assert not ll.is_full_look("") and not ll.is_full_look(None)


def test_resolve_look_ignores_the_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "looks_dir", lambda: str(tmp_path))
    ll.write_cube(str(tmp_path / "P.cube"), _curvy_table(3))
    assert ll.resolve_look("P.cube|colour") is ll.resolve_look("P.cube")


def test_keep_luminance_takes_ref_luminance_and_look_chromaticity():
    rng = np.random.default_rng(1)
    look = rng.uniform(0.05, 0.95, (500, 3)).astype(np.float32)
    ref = rng.uniform(0.05, 1.3, (500, 3)).astype(np.float32)
    out = ll.keep_luminance(look, ref)
    lo, lr, lout = _lin(look), _lin(ref), _lin(out)
    Y = np.array([0.2126, 0.7152, 0.0722], np.float32)
    np.testing.assert_allclose(lout @ Y, lr @ Y, rtol=1e-4, atol=1e-6)
    np.testing.assert_allclose(lout / (lout @ Y)[:, None], lo / (lo @ Y)[:, None],
                               rtol=1e-4, atol=1e-6)


def test_keep_luminance_grey_and_margins():
    grey = np.full((4, 3), 0.5, np.float32)
    ref = np.array([[0.2, 0.3, 0.4], [1.2, 1.1, 1.3], [-0.05, -0.02, -0.04],
                    [0.0, 0.0, 0.0]], np.float32)
    out = ll.keep_luminance(grey, ref)
    Y = np.array([0.2126, 0.7152, 0.0722], np.float32)
    yr = _lin(ref) @ Y
    np.testing.assert_allclose(_lin(out), np.repeat(yr[:, None], 3, 1), atol=1e-6)
    assert out[1].min() > 1.0                      # headroom above white kept
    assert out[2].max() < 0.0                      # the shadow margin kept
    black = ll.keep_luminance(np.zeros((1, 3), np.float32), np.full((1, 3), 0.4, np.float32))
    np.testing.assert_allclose(black, 0.4, atol=1e-5)   # no colour to give: ref's grey


def _density(seed=3, shape=(6, 7, 3)):
    return np.random.default_rng(seed).uniform(0.05, 1.05, shape).astype(np.float32)


@pytest.mark.parametrize("cineon", [False, True])
def test_colour_only_in_the_pipeline(cineon):
    lut = _lut()
    fl = ll.for_frame(lut, None, colour_only=True)
    assert isinstance(fl, ll.FrameLook) and fl.colour_only
    base = _base(_density())
    got = adjust_image(base, ws_windowed=True, ch_g_shift=6, cineon_log=cineon,
                       look_lut=fl).astype(np.float64) / 65535
    d = _decode_window(base)
    d[..., 1] += 6 / CH_SLIDER_DIV
    ref = apply_cineon_to_workspace(d) if cineon else d
    want = np.clip(ll.keep_luminance(ll.apply_look(d, lut), ref), 0, 1)
    np.testing.assert_allclose(got, want, atol=2.5 / 65535)


def test_colour_only_undoes_the_white_point_map_for_its_tone():
    lut = _lut()
    ci = {"mode": "bw", "bw": ((52000.0,) * 3, (4000.0, 3000.0, 2500.0)), "density": True}
    fl = ll.for_frame(lut, ci, colour_only=True)
    d = _density(4)
    np.testing.assert_allclose(fl.unmap_input(fl.map_input(d)), d, atol=1e-5)
    lin = ll.FrameLook(lut, "linear", (0.9, 0.8, 0.7))
    np.testing.assert_allclose(lin.unmap_input(lin.map_input(d * 0.9)), d * 0.9, atol=1e-4)


def test_full_look_unchanged_by_the_colour_only_path():
    lut = _lut()
    base = _base(_density(5))
    a = adjust_image(base, ws_windowed=True, look_lut=lut)
    b = adjust_image(base, ws_windowed=True, look_lut=ll.for_frame(lut, None))
    np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("ws", [True, False])
def test_colour_only_opencl_parity(ws):
    fl = ll.for_frame(_lut(), None, colour_only=True)
    d = _density(6)
    img = _base(d) if ws else (np.clip(d, 0, 1) * 65535).astype(np.uint16)
    kw = dict(ws_windowed=ws, ch_g_shift=9, balance_r=20, contrast=15, saturation=10,
              look_lut=fl)
    cpu = adjust_image(img, **kw)
    gpu = adjust_image_opencl(img, **kw)
    np.testing.assert_allclose(cpu.astype(np.int64), gpu.astype(np.int64), atol=3)


def test_colour_only_keeps_auto_gain_and_the_default_brightness(monkeypatch):
    seen = _capture(monkeypatch, _frame({"look_lut": "x.cube|colour", "ch_master_gain": 5}),
                    _lut(), on=False)
    assert seen["look_lut"].colour_only
    assert seen["ch_master_gain"] != pytest.approx(5)       # Auto Gain rides it
    assert seen["brightness"] == pytest.approx(-8)          # the default render
    full = _capture(monkeypatch, _frame({"look_lut": "x.cube", "ch_master_gain": 5}),
                    _lut(), on=False)
    assert full["ch_master_gain"] == pytest.approx(5) and full["brightness"] == 0


def test_auto_balance_applies_to_colour_only_too(monkeypatch):
    seen = _capture(monkeypatch, _frame({"look_lut": "x.cube|colour"}), _blut())
    assert seen["look_lut"].colour_only and abs(seen["ch_b_shift"]) > 0.5


# --------------------------------------------------------------------------- #
# panel and settings
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    lib = tmp_path / "looks"
    lib.mkdir()
    monkeypatch.setattr(ll, "looks_dir", lambda: str(lib))
    ll.write_cube(str(lib / "Noritsu Test.cube"), _curvy_table(5), (-0.25,) * 3, (1.25,) * 3)
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_both_lists_offer_the_colour_only_version(panel):
    for combo in panel._look_combos:
        texts = [combo.itemText(i) for i in range(combo.count())]
        i = texts.index("Noritsu Test")
        assert texts[i + 1] == "Noritsu Test (colour only)"
        assert combo.itemData(i + 1) == "Noritsu Test.cube|colour"


def test_colour_only_leaves_cineon_on(panel, monkeypatch):
    from core.ccr_backend import ccr_backend

    class Img:
        active_area_id = None
        converted = True
        adjustment_settings = {}

        def push_undo_state(self):
            pass

        def update_thumbnail_and_preview(self):
            pass
    img = Img()
    img.adjustment_settings = {}
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: img)
    monkeypatch.setattr(panel, "_update_thumb", lambda: None)
    monkeypatch.setattr(panel, "_show_rendered", lambda: None)
    panel.current_idx = 0
    panel.set_look("Noritsu Test.cube|colour")
    assert img.adjustment_settings["look_lut"] == "Noritsu Test.cube|colour"
    assert panel.cineon_checkbox.isEnabled()
    assert panel.look_combo.currentData() == "Noritsu Test.cube|colour"
    panel.set_look("Noritsu Test.cube")
    assert not panel.cineon_checkbox.isEnabled()


def test_paste_row_names_the_colour_only_look():
    from widgets.sliders_panel import paste_options
    rows = paste_options({"adjustments": {}, "look_lut": "Noritsu Test.cube|colour"},
                         lambda k: 0)
    assert ("Channel Levels", "look_lut", "Film Look: Noritsu Test (colour only)") in rows


def test_settings_has_no_film_look_balance_box(monkeypatch):
    """Moved beside the look lists (revision 2026-11-08)."""
    from PySide6.QtWidgets import QApplication, QWidget
    QApplication.instance() or QApplication([])
    from widgets.settings_dialog import SettingsDialog
    mw = QWidget()
    dlg = SettingsDialog(mw)
    _KEEP.append((mw, dlg))
    assert not hasattr(dlg, "_cb_look_balance")


# --------------------------------------------------------------------------- #
# the per-frame Balance box (revision 2026-11-08)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value, want", [
    ("", ("", False, None)),
    ("P.cube", ("P.cube", False, None)),
    ("P.cube|colour", ("P.cube", True, None)),
    ("P.cube|bal", ("P.cube", False, True)),
    ("P.cube|nobal", ("P.cube", False, False)),
    ("P.cube|colour|bal", ("P.cube", True, True)),
    ("P.cube|colour|nobal", ("P.cube", True, False)),
    ("a|b.cube", ("a|b.cube", False, None)),
])
def test_parse_look(value, want):
    assert ll.parse_look(value) == want
    name, colour, bal = want
    if name:
        assert ll.with_balance(value, bal) == value
        assert ll.split_look_name(value) == (name, colour)
        assert ll.look_choice(value) == (name + "|colour" if colour else name)


def test_balance_mark_doesnt_change_the_look():
    assert ll.with_balance("P.cube|colour", True) == "P.cube|colour|bal"
    assert ll.with_balance("P.cube|colour|bal", False) == "P.cube|colour|nobal"
    assert ll.with_balance("P.cube|bal", None) == "P.cube"
    assert ll.with_balance("", True) == ""
    assert ll.is_full_look("P.cube|bal") and not ll.is_full_look("P.cube|colour|nobal")
    from widgets.sliders_panel import _look_display_name
    assert _look_display_name("P.cube|colour|bal") == "P (colour only)"


@pytest.mark.parametrize("value, setting, applies", [
    ("x.cube|bal", False, True), ("x.cube|nobal", True, False),
    ("x.cube", True, True), ("x.cube", False, False),
    ("x.cube|colour|bal", False, True),
])
def test_the_mark_decides_and_unmarked_follows_the_setting(monkeypatch, value, setting, applies):
    seen = _capture(monkeypatch, _frame({"look_lut": value}), _blut(), on=setting)
    assert (abs(seen["ch_b_shift"]) > 0.5) == applies


def _write_balanced(lib, name="Balanced.cube"):
    ll.write_cube(str(lib / name), _curvy_table(5), (-0.25,) * 3, (1.25,) * 3,
                  balance=BAL)


class _PanelImg:
    converted = True

    def __init__(self):
        self.active_area_id = None
        self.adjustment_settings = {}
        self.undo = 0

    def push_undo_state(self):
        self.undo += 1

    def update_thumbnail_and_preview(self):
        pass


@pytest.fixture
def bal_panel(panel, tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    _write_balanced(tmp_path / "looks")
    panel._populate_look_combo()
    img = _PanelImg()
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: img)
    monkeypatch.setattr(ccr_backend, "look_auto_balance", False, raising=False)
    monkeypatch.setattr(panel, "_update_thumb", lambda: None)
    monkeypatch.setattr(panel, "_show_rendered", lambda: None)
    panel.current_idx = 0
    return panel, img


def _pick(panel, combo, data):
    panel._on_look_activated(combo.findData(data), combo)


def test_box_is_off_with_no_look_and_with_no_balance_data(bal_panel):
    panel, img = bal_panel
    panel._sync_look_balance_boxes(img)
    assert all(not b.isEnabled() and not b.isChecked() for b in panel._look_balance_boxes)
    _pick(panel, panel.look_combo, "Noritsu Test.cube")            # fitted without
    assert img.adjustment_settings["look_lut"] == "Noritsu Test.cube"
    assert all(not b.isEnabled() for b in panel._look_balance_boxes)


def test_picking_a_balanced_look_ticks_the_box_and_toggling_marks_it(bal_panel):
    panel, img = bal_panel
    _pick(panel, panel.look_combo_top, "Balanced.cube|colour")
    assert img.adjustment_settings["look_lut"] == "Balanced.cube|colour|bal"
    assert all(b.isEnabled() and b.isChecked() for b in panel._look_balance_boxes)
    assert panel.look_combo.currentData() == "Balanced.cube|colour"
    n = img.undo
    panel.look_balance_box.setChecked(False)
    assert img.adjustment_settings["look_lut"] == "Balanced.cube|colour|nobal"
    assert img.undo == n + 1
    assert not panel.look_balance_top.isChecked()                  # kept in step
    assert panel.look_combo_top.currentData() == "Balanced.cube|colour"
    _pick(panel, panel.look_combo, "Balanced.cube|colour")         # same look again
    assert img.adjustment_settings["look_lut"] == "Balanced.cube|colour|nobal"


def test_unmarked_frame_shows_the_old_setting_and_an_area_disables(bal_panel, monkeypatch):
    from core.ccr_backend import ccr_backend
    panel, img = bal_panel
    img.adjustment_settings["look_lut"] = "Balanced.cube"
    monkeypatch.setattr(ccr_backend, "look_auto_balance", True, raising=False)
    panel._sync_look_balance_boxes(img)
    assert panel.look_balance_box.isChecked() and panel.look_balance_box.isEnabled()
    img.active_area_id = "a1"
    panel._sync_look_balance_boxes(img)
    assert not panel.look_balance_box.isEnabled()


def test_paste_row_says_whether_balanced():
    from widgets.sliders_panel import paste_options
    rows = paste_options({"adjustments": {}, "look_lut": "N.cube|nobal"}, lambda k: 0)
    assert ("Channel Levels", "look_lut", "Film Look: N, not balanced") in rows


_KEEP = []
