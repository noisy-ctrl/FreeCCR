#!/usr/bin/env python3
"""Film Look: .cube I/O, the trilinear apply, its pipeline slot (Cineon's),
Auto Gain suppression and the panel control. See spec/film-look-lut.md."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import look_lut as ll  # noqa: E402
from core.ccr_processor import (adjust_image, adjust_image_opencl,  # noqa: E402
                                encode_window, WS_B, WS_W, CH_SLIDER_DIV,
                                apply_cineon_to_workspace)


def _curvy_table(n=9, lo=-0.25, hi=1.25):
    """A non-trivial but smooth look: per-channel tone curves plus a mix."""
    t = ll.identity_table(n, (lo,) * 3, (hi,) * 3).astype(np.float64)
    x = (t - lo) / (hi - lo)
    out = np.stack([x[..., 0] ** 0.8, 0.9 * x[..., 1] + 0.1 * x[..., 0],
                    np.sqrt(x[..., 2])], -1)
    return np.clip(out, 0, 1).astype(np.float32)


def _lut(n=9, lo=-0.25, hi=1.25):
    return ll.LookLUT(_curvy_table(n, lo, hi), (lo,) * 3, (hi,) * 3, title="t")


def _base(d):
    return encode_window(np.asarray(d, dtype=np.float32))


def _decode_window(code):
    return (code.astype(np.float32) - WS_B) / (WS_W - WS_B)


# --------------------------------------------------------------------------- #
# .cube I/O and the apply
# --------------------------------------------------------------------------- #

class TestCube:
    def test_round_trip(self, tmp_path):
        tab = _curvy_table(7)
        p = tmp_path / "x.cube"
        ll.write_cube(str(p), tab, (-0.25,) * 3, (1.25,) * 3, title="My Look",
                      comments=["hello"])
        lut = ll.read_cube(str(p))
        assert lut.size == 7 and lut.title == "My Look"
        np.testing.assert_allclose(lut.domain_min, -0.25)
        np.testing.assert_allclose(lut.domain_max, 1.25)
        np.testing.assert_allclose(lut.table, tab, atol=1e-6)

    def test_red_varies_fastest_in_file(self, tmp_path):
        p = tmp_path / "id.cube"
        ll.write_cube(str(p), ll.identity_table(3))
        rows = [l for l in p.read_text().splitlines() if l[:1].isdigit()]
        assert rows[0].split() == ["0.000000", "0.000000", "0.000000"]
        assert rows[1].split() == ["0.500000", "0.000000", "0.000000"]
        assert rows[3].split() == ["0.000000", "0.500000", "0.000000"]

    def test_resolve_input_range_keyword(self, tmp_path):
        p = tmp_path / "r.cube"
        body = "\n".join("{0} {1} {2}".format(*row) for row in
                         ll.identity_table(2, (0, 0, 0), (2, 2, 2)).reshape(-1, 3))
        p.write_text("LUT_3D_SIZE 2\nLUT_3D_INPUT_RANGE 0 2\n" + body + "\n")
        lut = ll.read_cube(str(p))
        np.testing.assert_allclose(lut.domain_max, 2.0)

    @pytest.mark.parametrize("text", [
        "LUT_1D_SIZE 2\n0 0 0\n1 1 1\n",
        "LUT_3D_SIZE 2\n0 0 0\n",
        "nonsense\n",
        "LUT_3D_SIZE 2\n" + "0 0\n" * 8,
    ])
    def test_rejects_bad_files(self, tmp_path, text):
        p = tmp_path / "bad.cube"
        p.write_text(text)
        with pytest.raises(ll.CubeError):
            ll.read_cube(str(p))

    def test_identity_is_identity(self):
        lut = ll.LookLUT(ll.identity_table(5))
        x = np.random.default_rng(0).random((50, 40, 3)).astype(np.float32)
        np.testing.assert_allclose(ll.apply_look(x, lut), x, atol=1e-6)

    def test_exact_at_nodes_and_linear_between(self):
        lut = _lut(9)
        g = np.linspace(-0.25, 1.25, 9)
        nodes = np.array([[g[2], g[5], g[7]], [g[0], g[8], g[3]]], np.float32)
        out = ll.apply_look(nodes, lut)
        np.testing.assert_allclose(out[0], lut.table[7, 5, 2], atol=1e-6)
        np.testing.assert_allclose(out[1], lut.table[3, 8, 0], atol=1e-6)
        mid = np.array([[(g[2] + g[3]) / 2, g[5], g[7]]], np.float32)
        np.testing.assert_allclose(
            ll.apply_look(mid, lut)[0],
            (lut.table[7, 5, 2] + lut.table[7, 5, 3]) / 2, atol=1e-6)

    def test_outside_domain_clamps(self):
        lut = _lut(9)
        out = ll.apply_look(np.array([[-5.0, 9.0, 0.5]], np.float32), lut)
        ref = ll.apply_look(np.array([[-0.25, 1.25, 0.5]], np.float32), lut)
        np.testing.assert_allclose(out, ref, atol=1e-6)

    def test_chunking_matches_single_pass(self, monkeypatch):
        lut = _lut(9)
        x = np.random.default_rng(1).uniform(-0.3, 1.3, (37, 29, 3)).astype(np.float32)
        whole = ll.apply_look(x, lut)
        monkeypatch.setattr(ll, "_CHUNK", 100)
        np.testing.assert_array_equal(ll.apply_look(x, lut), whole)


class TestLibrary:
    def test_import_list_resolve(self, tmp_path, monkeypatch):
        lib = tmp_path / "looks"
        lib.mkdir()
        monkeypatch.setattr(ll, "looks_dir", lambda: str(lib))
        src = tmp_path / "Lab Look.cube"
        ll.write_cube(str(src), _curvy_table(5), (-0.25,) * 3, (1.25,) * 3)
        name = ll.import_look(str(src))
        assert name == "Lab Look.cube"
        assert ll.list_looks() == ["Lab Look.cube"]
        lut = ll.resolve_look(name)
        assert lut is not None and lut.size == 5
        assert ll.resolve_look(name) is lut          # cached
        assert ll.resolve_look("missing.cube") is None
        assert ll.resolve_look("") is None

    def test_import_rejects_unreadable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ll, "looks_dir", lambda: str(tmp_path))
        bad = tmp_path / "bad.cube"
        bad.write_text("LUT_3D_SIZE 2\n")
        with pytest.raises(ll.CubeError):
            ll.import_look(str(bad))


# --------------------------------------------------------------------------- #
# pipeline slot
# --------------------------------------------------------------------------- #

def _random_density(shape=(6, 7, 3), seed=0):
    return np.random.default_rng(seed).uniform(-0.1, 1.05, shape).astype(np.float32)


class TestPipeline:
    def test_look_follows_channel_levels_on_windowed_base(self):
        lut = _lut()
        d = _random_density()
        base = _base(d)
        out = adjust_image(base, ws_windowed=True, ch_r_shift=15, ch_master_shift=-6,
                           look_lut=lut)
        x = _decode_window(base)
        x[..., 0] += 15 / CH_SLIDER_DIV
        x -= 6 / CH_SLIDER_DIV
        ref = np.clip(ll.apply_look(x, lut), 0, 1)
        np.testing.assert_allclose(out.astype(np.float64) / 65535, ref, atol=2.5 / 65535)

    def test_look_replaces_cineon(self):
        lut = _lut()
        base = _base(_random_density(seed=2))
        with_both = adjust_image(base, ws_windowed=True, cineon_log=True, look_lut=lut)
        look_only = adjust_image(base, ws_windowed=True, look_lut=lut)
        np.testing.assert_array_equal(with_both, look_only)
        cineon = adjust_image(base, ws_windowed=True, cineon_log=True)
        assert not np.array_equal(look_only, cineon)

    def test_no_look_is_byte_identical(self):
        base = _base(_random_density(seed=3))
        a = adjust_image(base, ws_windowed=True, contrast=10, cineon_log=True)
        b = adjust_image(base, ws_windowed=True, contrast=10, cineon_log=True,
                         look_lut=None)
        np.testing.assert_array_equal(a, b)

    def test_non_windowed_path_applies_look(self):
        lut = _lut()
        img = (np.random.default_rng(4).random((5, 6, 3)) * 65535).astype(np.uint16)
        out = adjust_image(img, look_lut=lut)
        ref = np.clip(ll.apply_look(img.astype(np.float32) / 65535, lut), 0, 1)
        np.testing.assert_allclose(out.astype(np.float64) / 65535, ref, atol=2.5 / 65535)

    def test_headroom_keeps_whites_apart_for_master_gain(self):
        """2026-11-01: with Master Gain down, a look that held white past
        density 1.0 rendered the petals as one flat grey; with headroom their
        texture comes back (spec/film-look-lut.md revision 4)."""
        from core import look_shoulder as ls
        lo, hi, n = -0.25, 1.25, 17
        x = np.linspace(lo, hi, n)
        y = np.clip(1.0 / (1.0 + np.exp(-(x - 0.35) * 5.0)) / 0.93, 0, 1)
        t = ll.identity_table(n, (lo,) * 3, (hi,) * 3).astype(np.float64)
        idx = np.round((t - lo) / (hi - lo) * (n - 1)).astype(int)
        t = y[idx]                                   # each channel its own curve
        old = ll.LookLUT(t, (lo,) * 3, (hi,) * 3)
        new_t, info = ls.headroom(t, lo, hi)
        new = ll.LookLUT(new_t, (lo,) * 3, (hi,) * 3, headroom=True)
        rng = np.random.default_rng(7)
        d = 1.12 + rng.normal(0, 0.02, (8, 32, 1)).astype(np.float32)    # petals
        d = np.repeat(d, 3, axis=2)
        base = _base(d)
        kw = dict(ws_windowed=True, ch_master_gain=-60)       # Master Gain down (x0.71)
        flat = adjust_image(base, look_lut=old, **kw).astype(np.float64)
        kept = adjust_image(base, look_lut=new, **kw).astype(np.float64)
        assert flat.std() < 1.0                               # one grey: clipped in the look
        assert kept.std() > 200 and kept.max() < 65535        # 16-bit codes: texture back
        # as shown with no gain change: identical where the old look was white
        a = adjust_image(base, ws_windowed=True, look_lut=old)
        b = adjust_image(base, ws_windowed=True, look_lut=new)
        np.testing.assert_array_equal(a, b)

    def test_headroom_marker_round_trips_and_extrapolates(self, tmp_path):
        tab = _curvy_table(7)
        p = tmp_path / "h.cube"
        ll.write_cube(str(p), tab, (-0.25,) * 3, (1.25,) * 3, headroom=True)
        lut = ll.read_cube(str(p))
        assert lut.headroom and np.all(lut.top_slope >= 0)
        past = np.array([[1.45, 1.25, 1.65]], np.float32)
        edge = np.array([[1.25, 1.25, 1.25]], np.float32)
        got = ll.apply_look(past, lut) - ll.apply_look(edge, lut)
        np.testing.assert_allclose(got[0], np.array([0.2, 0.0, 0.4]) * lut.top_slope,
                                   rtol=1e-5, atol=1e-6)
        plain = ll.LookLUT(tab, (-0.25,) * 3, (1.25,) * 3)          # no marker: clamped
        np.testing.assert_array_equal(ll.apply_look(past, plain), ll.apply_look(edge, plain))

    @pytest.mark.parametrize("ws", [True, False])
    def test_opencl_parity(self, ws):
        lut = _lut()
        d = _random_density(seed=5)
        img = _base(d) if ws else (np.clip(d, 0, 1) * 65535).astype(np.uint16)
        kw = dict(ws_windowed=ws, ch_g_shift=9, ch_b_gain=7, balance_r=20,
                  contrast=15, saturation=10, look_lut=lut)
        cpu = adjust_image(img, **kw)
        gpu = adjust_image_opencl(img, **kw)
        np.testing.assert_allclose(cpu.astype(np.int64), gpu.astype(np.int64), atol=3)


# --------------------------------------------------------------------------- #
# the look's input whatever the conversion (revision 6, 2026-11-02): with the
# auto white point on, Convert normalised each channel by log10(base/white),
# so the look got a per-channel scaled density and Portra came out yellow-green
# with the sliders at 0
# --------------------------------------------------------------------------- #

BASE = np.array([52000.0, 36400.0, 58100.0])         # film base (scan units)
WHITE = np.array([4140.0, 2720.0, 2690.0])           # a dense point


def _scan(seed=0, shape=(6, 7, 3), top=1.4):
    """A linear scan of a negative: base down to past the dense point."""
    dens = np.random.default_rng(seed).uniform(-0.05, top, shape)
    return (BASE * 10 ** (-dens)).astype(np.float32)


def _converted(x, white, density=True, slopes=None):
    from core.ccr_processor import _default_slope_invert, _twopoint_invert
    if white is None:
        return _default_slope_invert(x, BASE, slopes)
    return _twopoint_invert(x, BASE, white, density)


def _ci(white, density=True, slopes=None):
    return {"mode": "bw", "bw": (tuple(BASE), None if white is None else tuple(WHITE)),
            "density": density, "slopes": slopes}


class TestLookInput:
    def test_map_kinds(self):
        assert ll.look_input_map(_ci(None)) is None                  # what it was fitted on
        assert ll.look_input_map({"mode": "ref"}) is None
        assert ll.look_input_map({"mode": "bw", "bw": (None, None)}) is None   # no anchor
        assert ll.look_input_map(None) is None
        kind, k = ll.look_input_map(_ci(WHITE))
        assert kind == "scale"
        np.testing.assert_allclose(k, 0.8 * np.log10(BASE / WHITE), rtol=1e-6)
        kind, k = ll.look_input_map(_ci(None, slopes=(0.9, 0.8, 0.7)))
        np.testing.assert_allclose(k, (0.8 / 0.9, 1.0, 0.8 / 0.7), rtol=1e-6)
        kind, r = ll.look_input_map(_ci(WHITE, density=False))
        assert kind == "linear"
        np.testing.assert_allclose(r, 1 - WHITE / BASE, rtol=1e-6)

    def test_plain_look_passes_through_for_frame(self):
        lut = _lut()
        assert ll.for_frame(lut, _ci(None)) is lut
        assert ll.for_frame(None, _ci(WHITE)) is None
        fl = ll.for_frame(lut, _ci(WHITE))
        assert fl.size == lut.size and fl.headroom == lut.headroom
        import copy
        copy.deepcopy(fl)                                             # no recursion

    @pytest.mark.parametrize("density", [True, False])
    @pytest.mark.parametrize("ws", [True, False])
    def test_white_point_no_longer_changes_the_look(self, density, ws, monkeypatch):
        monkeypatch.setenv("FREECCR_WORKING_SPACE", "1" if ws else "0")
        lut = _lut()
        # the legacy linear base keeps 1024 codes per unit of TRANSMITTANCE,
        # so its dense end is coarse: compare it up to density 1.0
        x = _scan(top=1.4 if density else 1.0)
        kw = dict(ch_r_shift=12, ch_g_gain=-6, ch_master_shift=-4, ws_windowed=ws)
        want = adjust_image(_converted(x, None), look_lut=lut, **kw).astype(np.int64)
        got = adjust_image(_converted(x, WHITE, density), look_lut=ll.for_frame(
            lut, _ci(WHITE, density)), **kw).astype(np.int64)
        if not ws:                     # clipped bases: compare where neither clipped
            ok = ((_converted(x, None) < 65535).all(-1)
                  & (_converted(x, WHITE, density) < 65535).all(-1))
            want, got = want[ok], got[ok]
        # a windowed base holds 1024 codes per display unit, so the two bases
        # differ by up to a code of input before the look's slope amplifies it
        np.testing.assert_allclose(got, want, atol=(90 if density else 200) if ws else 4)
        # ...and before the fix it did change (the cast this fixes)
        old = adjust_image(_converted(x, WHITE, density), look_lut=lut, **kw)
        assert np.abs(old.astype(np.int64) - adjust_image(
            _converted(x, None), look_lut=lut, **kw).astype(np.int64)).max() > 500

    @pytest.mark.parametrize("density", [True, False])
    def test_map_recovers_the_black_point_density(self, density):
        x = _scan(3, top=1.4 if density else 1.0)
        bp = _decode_window(_converted(x, None))
        fl = ll.for_frame(_lut(), _ci(WHITE, density))
        got = fl.map_input(_decode_window(_converted(x, WHITE, density)))
        np.testing.assert_allclose(got, bp, atol=(1.5 if density else 3.0) / 1024)

    def test_film_stock_slopes_map_back_to_the_default(self):
        lut = _lut()
        x = _scan(1)
        slopes = (0.9, 0.75, 0.7)
        want = adjust_image(_converted(x, None), look_lut=lut, ws_windowed=True)
        got = adjust_image(_converted(x, None, slopes=slopes), ws_windowed=True,
                           look_lut=ll.for_frame(lut, _ci(None, slopes=slopes)))
        np.testing.assert_allclose(got.astype(np.int64), want.astype(np.int64), atol=90)

    @pytest.mark.parametrize("ws", [True, False])
    def test_opencl_parity_with_the_map(self, ws, monkeypatch):
        monkeypatch.setenv("FREECCR_WORKING_SPACE", "1" if ws else "0")
        look = ll.for_frame(_lut(), _ci(WHITE))
        img = _converted(_scan(2), WHITE)
        kw = dict(ws_windowed=ws, ch_g_shift=9, ch_b_gain=7, balance_r=20,
                  contrast=15, saturation=10, look_lut=look)
        cpu = adjust_image(img, **kw)
        gpu = adjust_image_opencl(img, **kw)
        np.testing.assert_allclose(cpu.astype(np.int64), gpu.astype(np.int64), atol=3)


def test_apply_adjustments_hands_the_look_its_frame_map(monkeypatch):
    lut = _lut()
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    seen = {}
    monkeypatch.setattr(mod, "adjust_image_opencl",
                        lambda image, *a, **kw: seen.update(kw) or image)
    monkeypatch.setattr(ccr_backend, "auto_gain", True, raising=False)
    monkeypatch.setattr(ll, "resolve_look", lambda name: lut)
    stub = _StubImage({"look_lut": "x.cube"})
    stub.conversion_inputs = _ci(WHITE)
    CCRImage.apply_adjustments(stub, _base(np.full((8, 8, 3), 0.35, np.float32)))
    assert isinstance(seen["look_lut"], ll.FrameLook) and seen["look_lut"].lut is lut
    stub.conversion_inputs = _ci(None)
    CCRImage.apply_adjustments(stub, _base(np.full((8, 8, 3), 0.35, np.float32)))
    assert seen["look_lut"] is lut


# --------------------------------------------------------------------------- #
# apply_adjustments: name resolution and Auto Gain suppression
# --------------------------------------------------------------------------- #

class _StubImage:
    def __init__(self, settings):
        self._ws_windowed = True
        self.converted = True
        self.adjustment_settings = settings
        self.contrast_base = 0
        self.temperature_base = 0
        self.brightness_base = -8
        self.exposure_base = 7.0
        self.color_profile = "color"
        self.area_layers = []
        self.tint_balance_factor = 1.0

    def _apply_dust_removal(self, image, ws_windowed=False):
        return image

    def _to_grayscale(self, image):
        return image


def _capture(monkeypatch, settings, auto_gain=True, resolve=None):
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    seen = {}

    def cap(image, *args, **kw):
        seen["exposure"] = args[2]
        seen["brightness"] = args[3]          # temperature, tint, exposure, brightness
        seen.update(kw)
        return image

    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    monkeypatch.setattr(ccr_backend, "auto_gain", auto_gain, raising=False)
    if resolve is not None:
        monkeypatch.setattr(ll, "resolve_look", resolve)
    base = _base(np.full((16, 16, 3), 0.35, np.float32))
    CCRImage.apply_adjustments(_StubImage(settings), base)
    return seen


def test_look_resolved_and_auto_gain_suppressed(monkeypatch):
    lut = _lut()
    seen = _capture(monkeypatch, {"look_lut": "x.cube", "ch_master_gain": 5},
                    resolve=lambda name: lut if name == "x.cube" else None)
    assert seen["look_lut"] is lut
    assert seen["ch_master_gain"] == pytest.approx(5)      # user's own gain kept
    assert seen["exposure"] == pytest.approx(0.0)          # baked eb suppressed too
    assert seen["brightness"] == pytest.approx(0.0)        # -8 baseline suppressed


def test_without_look_auto_gain_still_rides_master_gain(monkeypatch):
    seen = _capture(monkeypatch, {"ch_master_gain": 5})
    assert seen["look_lut"] is None
    assert seen["ch_master_gain"] != pytest.approx(5)
    assert seen["brightness"] == pytest.approx(-8)          # default render intact


def test_missing_look_renders_as_none(monkeypatch):
    seen = _capture(monkeypatch, {"look_lut": "gone.cube"}, resolve=lambda n: None)
    assert seen["look_lut"] is None


# --------------------------------------------------------------------------- #
# panel
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


def test_combo_lists_library(panel):
    texts = [panel.look_combo.itemText(i) for i in range(panel.look_combo.count())]
    assert texts[0] == "LUT (none loaded)"
    assert "Noritsu Test" in texts
    assert texts[-1].startswith("Import")


def test_the_look_has_its_own_fold_above_the_histogram(panel, monkeypatch):
    """2026-11-01: a second Film Look list, alone in a fold above the
    histogram, in step with Channel Levels' list."""
    lay = panel.layout()
    items = [lay.itemAt(i) for i in range(lay.count())]
    order = []
    for it in items:
        sub = it.layout()
        w = sub.itemAt(0).widget() if sub is not None and sub.count() else it.widget()
        order.append(w)
    assert order.index(panel.look_top_section) < order.index(panel.histogram)
    top = panel.look_combo_top
    assert [top.itemText(i) for i in range(top.count())] == \
        [panel.look_combo.itemText(i) for i in range(panel.look_combo.count())]
    # the row holds the list only: no fold to open, no label, no slider
    from PySide6.QtWidgets import QSlider, QComboBox, QPushButton, QLabel
    assert not panel.look_top_section.findChildren(QSlider)
    assert not panel.look_top_section.findChildren(QPushButton)
    assert not panel.look_top_section.findChildren(QLabel)
    assert panel.look_top_section.findChildren(QComboBox) == [top]
    assert top.itemText(0) == "LUT (none loaded)"
    # picking in either list sets the look and both show it
    picked = []
    monkeypatch.setattr(panel, "set_look", lambda name: (picked.append(name),
                                                        panel._select_look_in_combo(name)))
    i = top.findData("Noritsu Test.cube")
    top.activated.emit(i)
    assert picked == ["Noritsu Test.cube"]
    assert panel.look_combo.currentData() == "Noritsu Test.cube"
    panel.look_combo.activated.emit(panel.look_combo.findData(""))
    assert picked[-1] == "" and top.currentData() == ""
    assert "look_top" in {sid for sid, _l, _a in panel.HIDEABLE_SECTIONS}


def test_look_is_a_channel_levels_flag():
    from widgets.sliders_panel import SYNC_GROUPS, GLOBAL_FLAG_KEYS, SlidersPanel
    groups = {gid: keys for gid, _l, keys in SYNC_GROUPS}
    assert "look_lut" in groups["channels"]
    assert "look_lut" in GLOBAL_FLAG_KEYS
    assert "look_lut" not in SlidersPanel.ADJUSTMENT_KEYS


def test_paste_row_names_the_look():
    from widgets.sliders_panel import paste_options
    rows = paste_options({"adjustments": {}, "look_lut": "Noritsu Test.cube"},
                         lambda k: 0)
    assert ("Channel Levels", "look_lut", "Film Look: Noritsu Test") in rows


def test_set_look_writes_key_and_blocks_cineon(panel, monkeypatch):
    from core.ccr_backend import ccr_backend

    class Img:
        active_area_id = None
        converted = True

        def __init__(self):
            self.adjustment_settings = {}
            self.undo = 0

        def push_undo_state(self):
            self.undo += 1

        def update_thumbnail_and_preview(self):
            pass

    img = Img()
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: img)
    monkeypatch.setattr(panel, "parent", lambda: type("P", (), {"parent": lambda s: type(
        "MW", (), {"image_preview": type("IP", (), {"update_preview": lambda s, i: None})()})()})())
    monkeypatch.setattr(panel, "_update_thumb", lambda: None)
    panel.current_idx = 0
    panel.set_look("Noritsu Test.cube")
    assert img.adjustment_settings["look_lut"] == "Noritsu Test.cube"
    assert img.undo == 1
    assert not panel.cineon_checkbox.isEnabled()
    assert panel.look_combo.currentData() == "Noritsu Test.cube"
    # a slider rebuild keeps the VALUE, not True
    adj = panel._attach_cineon({})
    assert adj["look_lut"] == "Noritsu Test.cube"
    panel.set_look("")
    assert "look_lut" not in img.adjustment_settings
    assert panel.cineon_checkbox.isEnabled()
