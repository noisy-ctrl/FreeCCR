#!/usr/bin/env python3
"""Marks the IR can't see, found in the frame's own shots
(spec/colour-shot-marks.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core import film_marks, ir_dust, ir_dust_learn  # noqa: E402

H, W = 300, 400


def _negative(seed=4, marks=(), dust=(), blur_marks=0.0, edge=False, halo=None):
    """A smooth negative (blocks) with grain: `marks` lighter (prints dark),
    `dust` darker (prints white); `edge` adds a soft-free picture step."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    base = 3000 * (1 + 0.3 * np.sin(xx / 90.0) + 0.2 * np.cos(yy / 70.0))
    # the picture is soft (the taking lens): its detail is blurred
    pic = cv2.GaussianBlur(rng.normal(0, 0.05, (H, W)).astype(np.float32), (0, 0), 6)
    f = np.exp(pic)
    if edge:
        step = np.where(xx > 300, 0.55, 1.0).astype(np.float32)
        f = f * step
    m = np.ones((H, W), np.float32)
    for x, y, r in marks:
        cv2.circle(m, (x, y), r, 1.8, -1)
    if blur_marks:
        m = cv2.GaussianBlur(m, (0, 0), blur_marks)
    d = np.ones((H, W), np.float32)
    for x, y, r in dust:
        cv2.circle(d, (x, y), r, 0.35, -1)
        if halo:
            cv2.circle(d, (x, y), r + 2, halo, 2)
    v = base * f * m * d * (1 + rng.normal(0, 0.01, (H, W)))
    return v.astype(np.float32)


def _ir_z(spots=(), depth=-4.0):
    z = np.zeros((H, W), np.float32)
    for x, y, r in spots:
        cv2.circle(z, (x, y), r + 1, depth, -1)
    return z


def _at(mm, x, y, kind, tol=4):
    for i, mk in enumerate(mm.marks):
        cx, cy = mk.centre()
        if mk.kind == kind and abs(cx - x) < tol and abs(cy - y) < tol:
            return i, mk
    return None, None


def test_a_sharp_light_mark_is_found_and_a_soft_one_or_an_edge_isnt():
    v = _negative(marks=[(100, 150, 3)])
    mm = film_marks.find_marks([v])
    i, mk = _at(mm, 100, 150, "light")
    assert mk is not None and i in film_marks.flagged(mm)
    soft = film_marks.find_marks([_negative(marks=[(100, 150, 3)], blur_marks=6.0)])
    i, mk = _at(soft, 100, 150, "light")
    assert mk is None or i not in film_marks.flagged(soft)
    edge = film_marks.find_marks([_negative(edge=True)])
    for i in film_marks.flagged(edge, 100):
        cx, _cy = edge.marks[i].centre()
        assert abs(cx - 300) > 10                        # nothing along the picture's edge


def test_a_dust_specks_halo_is_not_a_mark():
    v = _negative(dust=[(200, 120, 3)], halo=1.5)
    mm = film_marks.find_marks([v])
    assert _at(mm, 200, 120, "dark")[1] is not None   # the speck prints white
    for mk in mm.marks:
        if mk.kind == "light":
            cx, cy = mk.centre()
            assert np.hypot(cx - 200, cy - 120) > 10    # its bright ring isn't looked at


def test_a_white_printing_mark_needs_the_irs_support_when_there_is_an_ir_shot():
    v = _negative(dust=[(120, 100, 2), (280, 200, 2)])
    mm = film_marks.find_marks([v], ir_z=_ir_z([(120, 100, 2)], depth=-2.0))
    a, b = _at(mm, 120, 100, "dark")[0], _at(mm, 280, 200, "dark")[0]
    fl = film_marks.flagged(mm)
    assert a in fl and b not in fl                     # the IR darkens one a little, not the other
    no_ir = film_marks.find_marks([v])
    fl = film_marks.flagged(no_ir)
    assert _at(no_ir, 120, 100, "dark")[0] in fl and _at(no_ir, 280, 200, "dark")[0] in fl


def test_sensitivity_nests():
    v = _negative(marks=[(100, 150, 3), (300, 80, 1), (60, 60, 2)], dust=[(220, 220, 2)])
    mm = film_marks.find_marks([v])
    a, b, c = (set(film_marks.flagged(mm, s)) for s in (0, 50, 100))
    assert a <= b <= c
    assert film_marks.thresholds(0)[0] > film_marks.thresholds(50)[0] > film_marks.thresholds(100)[0]


def test_marks_become_spots_with_their_features():
    v = _negative(marks=[(100, 150, 3)])
    mm = film_marks.find_marks([v])
    spots = film_marks.spots_from(mm)
    assert spots and all(s["kind"] == "mark" and len(s["mk_f"]) == len(film_marks.FEATURES)
                         and s["mk_k"] in film_marks.KINDS for s in spots)
    x, y = spots[0]["pts"][0]
    assert abs(x * W - 100) < 3 and abs(y * H - 150) < 3


class _Img:
    merge_sources = None
    file_path = "f.raf"

    def __init__(self):
        self.dust_spots = [{"kind": "brush", "pts": [[0.1, 0.1]], "r": 0.01},
                           {"kind": "mark", "pts": [[0.5, 0.5]], "r": 0.01},
                           {"kind": "ir", "pts": [[0.6, 0.6]], "r": 0.01}]
        self.ir_dust_pending = [
            {"kind": "ir", "pts": [[0.3, 0.3]], "r": 0.005, "ir_f": [5] * 8, "ir_c": 1},
            {"kind": "mark", "pts": [[0.7, 0.2]], "r": 0.005, "mk_f": [9, 8, 0.5, 3, 1, 0, 0],
             "mk_c": 4, "mk_k": "light"}]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECCR_IR_DUST_LEARNED", str(tmp_path / "learned.json"))
    ir_dust_learn.reset_cache()
    yield
    ir_dust_learn.reset_cache()


def test_heal_and_erase_take_marks_too(store):
    img = _Img()
    assert ir_dust.erase(img, [[0.7, 0.2]], 0.01, 1.0) == (0, 1)
    assert [s["kind"] for s in img.ir_dust_pending] == ["ir"]
    assert len(ir_dust_learn.examples("marks-light")) == 1          # kept for later
    img = _Img()
    assert ir_dust.heal(img) == 2
    kinds = sorted(s["kind"] for s in img.dust_spots)
    assert kinds == ["brush", "ir", "mark"]                      # earlier IR and mark spots replaced
    assert [0.5, 0.5] not in [s["pts"][0] for s in img.dust_spots]


def test_rethreshold_keeps_the_other_finders_spots():
    img = _Img()
    img._mark_map = film_marks.find_marks([_negative(marks=[(100, 150, 3)])])
    film_marks.rethreshold(img, 50)
    kinds = [s["kind"] for s in img.ir_dust_pending]
    assert "ir" in kinds and kinds.count("mark") >= 1 and img.mark_sens == 50
    img._ir_dust_map = None
    ir_dust.rethreshold(img, 30)                                   # no IR map: the marks stay
    assert any(s["kind"] == "mark" for s in img.ir_dust_pending)


def test_find_all_looks_in_the_shots_without_an_ir_shot(tmp_path, monkeypatch):
    raf = tmp_path / "R0001_645_001_BW.raf"
    raf.write_bytes(b"raw")
    v = _negative(marks=[(100, 150, 3)])
    monkeypatch.setattr(ir_dust, "read_blocks", lambda p: v)

    class I:
        merge_sources = None
        file_path = str(raf)
        ir_source = None
        crop_rect = None
        dust_spots = []
        ir_dust_pending = None
    img = I()
    assert ir_dust.can_find(img) and film_marks.can_find(img) is None
    spots = ir_dust.find_all(img, None, 50)
    assert spots and all(s["kind"] == "mark" for s in spots)
    img.file_path = str(tmp_path / "f.tif")
    assert "RAW" in film_marks.can_find(img)
    with pytest.raises(ValueError):
        ir_dust.find_all(img)


def test_the_panel_finds_marks_and_outlines_them_blue(tmp_path, monkeypatch):
    import time
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from ui import theme
    theme.apply_theme(app)
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from ui.main_window import MainWindow
    v = _negative(marks=[(100, 150, 3), (250, 90, 2)])
    p = str(tmp_path / "frame.png")
    cv2.imwrite(p, (np.clip(1 - v / v.max(), 0, 1) * 255).astype(np.uint8))
    monkeypatch.setattr(ir_dust, "read_blocks", lambda path: v)
    monkeypatch.setattr(film_marks, "can_find", lambda img: None)
    img = CCRImage(p)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "save_catalog", lambda *a, **k: None)
    win = MainWindow()
    win.resize(1600, 1000)
    win.thumbnail_list.load_thumbnails()
    app.processEvents()
    win.toggle_dust_removal(True)
    app.processEvents()
    prev, panel = win.image_preview, win.dust_panel
    assert panel.ir_find_btn.isEnabled() and panel.mark_sens_slider.isEnabled()
    panel.ir_find_btn.click()
    end = time.time() + 20
    while time.time() < end and (panel._ir_thread is not None or img.ir_dust_pending is None):
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert img.ir_dust_pending and all(s["kind"] == "mark" for s in img.ir_dust_pending)
    assert "(blue)" in panel.ir_status.text() and "Found" in panel.ir_status.text()
    assert panel.ir_heal_btn.text().startswith("Paint Over ")     # spec/dust-brush-eraser.md
    assert [it.data(0) for it in prev._ir_pending_items] == ["mark"]
    assert prev._dust_badge is True
    n = len(img.ir_dust_pending)
    panel.mark_sens_slider.setValue(0)
    panel._apply_mark_sensitivity()
    assert len(img.ir_dust_pending or []) <= n and img.mark_sens == 0
    panel.mark_sens_slider.setValue(50)
    panel._apply_mark_sensitivity()
    panel.ir_heal_btn.click()
    app.processEvents()
    assert img.ir_dust_pending is None and any(s["kind"] == "mark" for s in img.dust_spots)
    win.hide()
