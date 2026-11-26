"""Organics v2: Lab A or Lab B (spec/organics-v2-labs.md). Each lab's tables
held to the finishing tools' quality rules; the choice stored, copied,
pasted, synced and undone like the tools' options."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from core import organics_v2 as v2                       # noqa: E402
from core import organics_v2_data as data                # noqa: E402
from test_organics_v2 import lab_patch, lch, mean_turn  # noqa: E402
from test_x_tones import _Img, _wire                     # noqa: E402

LABS = ("a", "b")


# --- the choice -------------------------------------------------------------------

@pytest.mark.parametrize("value,want", [("b", "b"), ("B", "b"), (" b ", "b"),
                                        ("a", None), (None, None), ("x", None), (3, None)])
def test_normalize_lab(value, want):
    assert v2.normalize_lab(value) == want
    assert v2.lab_of(value) == (want or "a")


def test_both_labs_are_on_the_same_knots():
    assert set(data.LABS) == {"a", "b"}
    for t in data.LABS.values():
        assert np.shape(t["HUE_MOVE"]) == (len(data.HUE_KNOTS), len(data.L_KNOTS))
        assert np.shape(t["CHROMA_LOG"]) == (len(data.HUE_KNOTS), len(data.L_KNOTS))
        assert np.max(t["CHROMA_LOG"]) <= 0.0
    assert data.HUE_MOVE is data.LABS["a"]["HUE_MOVE"]


# --- each lab keeps the quality rules ------------------------------------------------

@pytest.mark.parametrize("lab", LABS)
def test_greys_exact_and_lightness_kept(lab):
    ramp = np.repeat(np.repeat(np.linspace(0, 1, 256, dtype=np.float32)[None, :, None], 16, 0), 3, 2)
    assert np.array_equal(v2.apply_organics_v2(ramp, 100, 100, lab), ramp)
    img = lab_patch(0.7, 0.08, 60)
    L0, _, _ = lch(img)
    L1, _, _ = lch(v2.apply_organics_v2(img, 100, 100, lab))
    assert np.abs(L1 - L0).max() < 2e-3


@pytest.mark.parametrize("lab", LABS)
def test_chroma_never_grows(lab):
    rng = np.random.default_rng(7)
    for _ in range(12):
        L, C, h = rng.uniform(0.15, 0.9), rng.uniform(0.02, 0.12), rng.uniform(0, 360)
        img = lab_patch(L, C, h, (16, 16))
        _, c0, _ = lch(img)
        _, c1, _ = lch(v2.apply_organics_v2(img, 0, 100, lab))
        assert c1.max() <= c0.max() + 1e-4


@pytest.mark.parametrize("lab", LABS)
def test_the_hue_map_never_folds_at_full(lab):
    assert v2.fold_limit(lab) >= v2.AMOUNT_MAX
    h = np.arange(0, 360, 0.25)
    for L in (0.15, 0.3, 0.5, 0.7, 0.9):
        move, _ = v2.table_fields(np.full_like(h, L), h, lab)
        assert (1 + 2.0 * np.gradient(move, 0.25)).min() >= v2.MIN_SLOPE


@pytest.mark.parametrize("lab", LABS)
def test_a_fine_hue_ramp_stays_in_order(lab):
    n = 4096
    h = np.linspace(0, 359.9, n)
    ok = np.stack([np.full(n, 0.6), 0.07 * np.cos(np.radians(h)), 0.07 * np.sin(np.radians(h))], 1)
    from core.oklab import from_oklab
    img = np.repeat(from_oklab(ok.astype(np.float32)).reshape(1, n, 3), 8, 0)
    _, _, h1 = lch(v2.apply_organics_v2(img, 100, 0, lab)[4:5])
    assert (np.diff(np.unwrap(np.radians(h1))) > -1e-6).all()


@pytest.mark.parametrize("lab", LABS)
def test_in_range_and_grain_does_not_grow(lab):
    rng = np.random.default_rng(3)
    img = rng.uniform(0, 1, (64, 64, 3)).astype(np.float32)
    out = v2.apply_organics_v2(img, 100, 100, lab)
    assert out.min() >= 0.0 and out.max() <= 1.0
    base = lab_patch(0.6, 0.07, 50, (96, 96))
    noisy = np.clip(base + rng.normal(0, 0.02, base.shape).astype(np.float32), 0, 1)
    from core.oklab import to_oklab
    o0 = to_oklab(noisy.reshape(-1, 3))
    o1 = to_oklab(v2.apply_organics_v2(noisy, 100, 100, lab).reshape(-1, 3))
    assert o1[:, 1:].std(0).max() <= o0[:, 1:].std(0).max() * 1.02


# --- the labs differ where they were measured to ---------------------------------------

def test_lab_a_turns_light_skin_and_reds_yellower_than_lab_b():
    for h in (25, 55):                        # OKLab hue: red, skin/orange
        a = mean_turn(img := lab_patch(0.8, 0.06, h), v2.apply_organics_v2(img, 50, 0, "a"))
        b = mean_turn(img, v2.apply_organics_v2(img, 50, 0, "b"))
        assert a > b + 5.0


def test_lab_b_keeps_more_colour_in_light_skin():
    img = lab_patch(0.8, 0.06, 55)
    _, c0, _ = lch(img)
    _, ca, _ = lch(v2.apply_organics_v2(img, 0, 50, "a"))
    _, cb, _ = lch(v2.apply_organics_v2(img, 0, 50, "b"))
    assert np.median(cb) > np.median(ca) + 0.003
    assert np.median(cb) <= np.median(c0) + 1e-4


def test_adjust_image_takes_the_lab_last():
    from core import ccr_processor as cp
    import inspect
    for fn in (cp.adjust_image, cp.adjust_image_opencl):
        assert list(inspect.signature(fn).parameters)[-1] == "organics_v2_lab"
    rng = np.random.default_rng(4)
    img16 = (rng.uniform(0.2, 0.8, (32, 48, 3)) * 65535).astype(np.uint16)
    base = cp.adjust_image(img16)
    assert np.array_equal(cp.adjust_image(img16, organics_v2_lab="b"), base)
    a = cp.adjust_image(img16, organics_v2=60.0, organic_sat=60.0)
    assert np.array_equal(cp.adjust_image(img16, organics_v2=60.0, organic_sat=60.0,
                                          organics_v2_lab="a"), a)
    b = cp.adjust_image(img16, organics_v2=60.0, organic_sat=60.0, organics_v2_lab="b")
    assert not np.array_equal(a, b)


def test_apply_adjustments_passes_the_lab(monkeypatch):
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    from test_x_tones import _Stub, _img
    seen = {}

    def cap(image, *a, **kw):
        seen.update(kw)
        return image

    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    CCRImage.apply_adjustments(_Stub({"organics_v2": 50, "organics_v2_lab": "b"}), _img())
    assert seen["organics_v2_lab"] == "b"
    seen.clear()
    CCRImage._adjust_for_area(_Stub({}), _img(), {"organics_v2": 30, "organics_v2_lab": "b"})
    assert seen["organics_v2_lab"] == "b"


# --- the panel ----------------------------------------------------------------------

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def _choose(panel, lab):
    menu = panel.organics_v2_menu()
    act = next(a for a in menu.actions() if a.data() == lab)
    act.trigger()
    return menu


def test_both_labels_offer_the_menu_and_say_the_lab(panel, monkeypatch):
    from PySide6.QtCore import Qt
    assert [lbl.text() for lbl in panel._v2_labels] == ["Organics v2", "Organic Saturation"]
    for lbl in panel._v2_labels:
        assert lbl.contextMenuPolicy() == Qt.CustomContextMenu
        tip = lbl.toolTip()
        assert "Right-click: Lab A or Lab B" in tip and "Lab A:" in tip
        assert "Noritsu" not in tip
        assert not lbl.font().underline()
    img = _Img({"organics_v2": 50})
    _wire(panel, monkeypatch, img)
    menu = panel.organics_v2_menu()
    acts = menu.actions()
    assert [a.data() for a in acts] == ["a", "b"]
    assert acts[0].isChecked() and not acts[1].isChecked()
    assert acts[0].text().startswith("Lab A") and acts[1].text().startswith("Lab B")


def test_choosing_b_stores_it_once_and_a_removes_it(panel, monkeypatch):
    img = _Img({"organics_v2": 50})
    _wire(panel, monkeypatch, img)
    _choose(panel, "b")
    assert img.adjustment_settings["organics_v2_lab"] == "b"
    assert img.undo == 1                                  # one undo step
    assert all(lbl.font().underline() for lbl in panel._v2_labels)
    assert "Lab B:" in panel._v2_labels[0].toolTip()
    assert panel.organics_v2_menu().actions()[1].isChecked()
    _choose(panel, "b")                                   # no change: no undo step
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("contrast")].setValue(12)
    assert img.adjustment_settings["organics_v2_lab"] == "b"   # survives a slider move
    _choose(panel, "a")
    assert "organics_v2_lab" not in img.adjustment_settings
    assert not panel._v2_labels[0].font().underline()


def test_loading_and_reset(panel, monkeypatch):
    img = _Img({"organics_v2": 40, "organics_v2_lab": "b"})
    _wire(panel, monkeypatch, img)
    panel._load_active_layer(0)
    assert panel._v2_lab == "b"
    img2 = _Img({"organics_v2": 40})
    _wire(panel, monkeypatch, img2)
    panel._load_active_layer(0)
    assert panel._v2_lab is None
    _wire(panel, monkeypatch, img)
    panel._load_active_layer(0)
    monkeypatch.setattr(panel, "_show_rendered", lambda *a, **k: None)
    panel.on_reset_clicked()                      # Reset goes back to Lab A
    assert panel._v2_lab is None
    assert "organics_v2_lab" not in img.adjustment_settings


def _clip(**kw):
    base = dict(adjustments={"organics_v2": 50, "organic_sat": 40}, curves=None,
                cineon_log=False, look_lut=None, flags={}, profile="color",
                crop=(None, 0.0), rotation=0, flip_h=False, flip_v=False, fine_rotation=0)
    base.update(kw)
    return base


def test_paste_rows_say_the_lab():
    from widgets.sliders_panel import paste_options
    rows = {r[1]: r[2] for r in paste_options(_clip(organics_v2_lab="b"), lambda k: 0)}
    assert rows["adj:organics_v2"].endswith(", lab B)")
    assert rows["adj:organic_sat"].endswith(", lab B)")
    rows = {r[1]: r[2] for r in paste_options(_clip(), lambda k: 0)}
    assert "lab" not in rows["adj:organics_v2"]


@pytest.mark.parametrize("chosen,clip_lab,target_lab,want", [
    ({"adj:organics_v2"}, "b", None, "b"),
    ({"adj:organic_sat"}, "b", None, "b"),
    ({"adj:organics_v2"}, None, "b", None),       # the clipboard's Lab A replaces B
    ({"adj:contrast"}, "b", None, None),          # neither row: the target's kept
    ({"adj:contrast"}, None, "b", "b"),
])
def test_paste_brings_the_lab_with_its_rows(panel, chosen, clip_lab, target_lab, want):
    target = {"contrast": 0}
    if target_lab:
        target["organics_v2_lab"] = target_lab
    clip = _clip(organics_v2_lab=clip_lab)
    clip["adjustments"]["contrast"] = 7
    merged = panel.paste_into(target, clip, chosen)
    assert merged.get("organics_v2_lab") == want


def test_copy_snapshot_carries_the_lab(panel, monkeypatch):
    src = _Img({"organics_v2": 50, "organics_v2_lab": "b"})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5                         # copy from a frame not on screen
    panel.copy_settings_from_index(0)
    assert panel.clipboard["organics_v2_lab"] == "b"


def _sync(panel, monkeypatch, src, dst, sat):
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
    panel._sync_group_selection = {gid: (gid == "sat" and sat) or gid == "tone"
                                   for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()


def test_sync_with_saturation_carries_the_lab(panel, monkeypatch):
    src = _Img({"organics_v2": 50, "organics_v2_lab": "b"})
    dst = _Img({"organics_v2": 50})
    _sync(panel, monkeypatch, src, dst, sat=True)
    assert dst.adjustment_settings["organics_v2_lab"] == "b"
    back = _Img({"organics_v2": 50})
    _sync(panel, monkeypatch, back, dst, sat=True)
    assert "organics_v2_lab" not in dst.adjustment_settings


def test_sync_without_saturation_keeps_the_target_lab(panel, monkeypatch):
    src = _Img({"contrast": 20})
    dst = _Img({"contrast": 0, "organics_v2_lab": "b"})
    _sync(panel, monkeypatch, src, dst, sat=False)
    assert dst.adjustment_settings["contrast"] == 20
    assert dst.adjustment_settings["organics_v2_lab"] == "b"
