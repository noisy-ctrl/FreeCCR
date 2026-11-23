#!/usr/bin/env python3
"""IR dust: found in an infrared shot, flagged, healed on request
(spec/ir-dust-and-flat-sets.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core import ir_dust  # noqa: E402

SPECKS = [(80, 60, 2.5), (300, 120, 3.0), (480, 330, 2.0), (150, 300, 3.5), (520, 70, 2.2)]
HAIR = ((220, 250), (300, 285), 1.2)
BARS = [((x, 180), (x + 4, 230)) for x in range(360, 470, 14)]   # fine dense detail


def _scene(h=400, w=600, seed=3, k=0.2, shift=(0.0, 0.0), warp=None):
    """Block maps (visible, IR) of a frame with specks, a hair and dense bars.
    Visible = picture x dust; IR = picture^k x dust x hotspot, both noisy."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    pic = 0.25 + 0.55 * xx / w + 0.1 * np.sin(yy / 37.0)
    bars = np.zeros((h, w), np.uint8)
    for a, b in BARS:
        cv2.line(bars, a, b, 1, 3)
    pic = np.where(bars > 0, pic * 0.15, pic).astype(np.float32)
    pic = cv2.GaussianBlur(pic, (0, 0), 0.7)
    dust = np.ones((h, w), np.float32)
    for x, y, r in SPECKS:
        cv2.circle(dust, (x, y), int(round(r)), 0.12, -1)
    cv2.line(dust, HAIR[0], HAIR[1], 0.2, 2)
    dust = cv2.GaussianBlur(dust, (0, 0), 0.8)
    vis = 9000 * pic * dust * (1 + rng.normal(0, 0.01, (h, w)))
    hot = 1.0 + 0.8 * np.exp(-((xx - w / 2) ** 2 + (yy - h / 2) ** 2) / (2 * (w / 4) ** 2))
    dust_ir = 1 - 0.85 * (1 - dust)
    ir = 600 * hot * pic ** k * dust_ir
    if shift != (0.0, 0.0):
        m = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
        ir = cv2.warpAffine(ir, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
    if warp is not None:
        ir = cv2.warpAffine(ir, np.float32(warp), (w, h), borderMode=cv2.BORDER_REPLICATE)
    ir = ir * (1 + rng.normal(0, 0.04, (h, w)))
    return vis.astype(np.float32), ir.astype(np.float32)


def _near(spots, x, y, w, h, tol=3.0):
    for s in spots:
        for px, py in s["pts"]:
            if abs(px * w - x) <= tol + s["r"] * w and abs(py * h - y) <= tol + s["r"] * w:
                return s
    return None


def _on_bars(spots, w, h):
    out = []
    for s in spots:
        for px, py in s["pts"]:
            X, Y = px * w, py * h
            if 355 <= X <= 480 and 175 <= Y <= 235:
                out.append(s)
                break
    return out


def test_the_picture_divides_out_and_the_dust_stays():
    vis, ir = _scene()
    h, w = ir.shape
    dm = ir_dust.dust_map(ir, [vis])
    assert dm.k and abs(dm.k[0] - 0.2) < 0.06            # the crosstalk recovered
    spots = ir_dust.spots_from(dm)
    for x, y, _r in SPECKS:
        assert _near(spots, x, y, w, h), (x, y)
    hair = _near(spots, 260, 267, w, h, tol=6)
    assert hair is not None and len(hair["pts"]) >= 2     # a hair is a polyline
    assert not _on_bars(spots, w, h)                      # the dense bars are picture
    assert all(s["kind"] == "ir" for s in spots)
    assert len(spots) <= len(SPECKS) + 2
    # IR alone takes the bars for dust: that is what the white-light shot is for
    alone = ir_dust.spots_from(ir_dust.dust_map(ir, None))
    assert _on_bars(alone, w, h)


def test_sensitivity_finds_more_and_the_spots_cover_their_specks():
    vis, ir = _scene()
    h, w = ir.shape
    dm = ir_dust.dust_map(ir, [vis])
    counts = [len(ir_dust.spots_from(dm, s)) for s in (0, 50, 100)]
    assert counts[0] <= counts[1] <= counts[2]
    for x, y, r in SPECKS:
        s = _near(ir_dust.spots_from(dm), x, y, w, h)
        assert s["r"] * w >= r                           # the hole covers the speck
    assert ir_dust.sigma_for(0) > ir_dust.sigma_for(50) > ir_dust.sigma_for(100)
    assert ir_dust.p_needed(0) > ir_dust.p_needed(50) > ir_dust.p_needed(100)


def test_a_knock_between_the_shots_is_registered():
    vis, ir = _scene(shift=(1.6, -1.2))
    h, w = ir.shape
    dm = ir_dust.dust_map(ir, [vis])
    assert abs(dm.shift[0] + 1.6) < 0.5 and abs(dm.shift[1] - 1.2) < 0.5
    spots = ir_dust.spots_from(dm)
    for x, y, _r in SPECKS:
        assert _near(spots, x, y, w, h, tol=2.0)


def test_the_crop_keeps_out_the_rebate():
    vis, ir = _scene()
    h, w = ir.shape
    dm = ir_dust.dust_map(ir, [vis])
    keep = np.zeros((h, w), bool)
    keep[:, 200:] = True
    spots = ir_dust.spots_from(dm, keep=keep)
    assert not _near(spots, 80, 60, w, h) and _near(spots, 300, 120, w, h)


def test_blocks_from_a_mosaic():
    raw = np.array([[110, 220], [330, 440]], np.uint16)
    raw = np.tile(raw, (2, 3))
    colors = np.tile(np.array([[0, 1], [3, 2]]), (2, 3))
    b = ir_dust.blocks_from_mosaic(raw, [10, 20, 40, 30], colors)
    assert b.shape == (2, 3)
    assert np.allclose(b, (100 + 200 + 300 + 400) / 4)


class _Img:
    def __init__(self):
        self.dust_spots = [{"kind": "brush", "pts": [[0.1, 0.1]], "r": 0.01},
                           {"kind": "ir", "pts": [[0.5, 0.5]], "r": 0.01}]
        self.ir_dust_pending = [{"kind": "ir", "pts": [[0.3, 0.3]], "r": 0.005},
                                {"kind": "ir", "pts": [[0.7, 0.2]], "r": 0.005}]


def test_heal_replaces_earlier_ir_spots_and_keeps_hand_ones():
    img = _Img()
    assert ir_dust.heal(img) == 2
    kinds = [s["kind"] for s in img.dust_spots]
    assert kinds.count("brush") == 1 and kinds.count("ir") == 2
    assert [0.5, 0.5] not in [s["pts"][0] for s in img.dust_spots]
    assert img.ir_dust_pending is None


def test_the_eraser_removes_what_it_touches():
    img = _Img()
    n_h, n_f = ir_dust.erase(img, [[0.3, 0.3]], 0.01, 1.0)
    assert (n_h, n_f) == (0, 1) and len(img.ir_dust_pending) == 1
    n_h, n_f = ir_dust.erase(img, [[0.1, 0.1], [0.5, 0.505]], 0.01, 1.0)
    assert (n_h, n_f) == (2, 0) and img.dust_spots == []
    n_h, n_f = ir_dust.erase(img, [[0.9, 0.9]], 0.01, 1.0)
    assert (n_h, n_f) == (0, 0)


def test_catalogue_and_undo_keep_the_flagged_dust(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv[:1])
    from core.ccr_image import CCRImage
    from core import catalog
    p = str(tmp_path / "f.png")
    cv2.imwrite(p, np.full((40, 60, 3), 120, np.uint8))
    img = CCRImage(p)
    img.ir_source = "/x/f_4IR.raf"
    img.ir_dust_pending = [{"kind": "ir", "pts": [[0.3, 0.3]], "r": 0.01}]
    img.ir_dust_sens = 60
    state = catalog.serialize_image(img)
    assert not catalog._is_pristine(state)
    back = catalog._restore_image(p, state)
    assert back.ir_source == "/x/f_4IR.raf" and back.ir_dust_sens == 60
    assert back.ir_dust_pending == img.ir_dust_pending
    img.push_undo_state()
    ir_dust.heal(img)
    assert img.ir_dust_pending is None
    assert img.pop_undo_state()
    assert img.ir_dust_pending and not any(s["kind"] == "ir" for s in img.dust_spots)


def test_can_find_says_why_not(tmp_path):
    class I:
        ir_source = None
        source_ops = None
    assert "No IR shot" in ir_dust.can_find(I())
    I.ir_source = str(tmp_path / "gone.raf")
    assert "isn't there" in ir_dust.can_find(I())
    (tmp_path / "gone.raf").write_bytes(b"x")
    assert ir_dust.can_find(I()) is None
    I.source_ops = [("slice",)]
    assert "slices" in ir_dust.can_find(I())


# --------------------------------------------------------------------------- #
# the user's own files (2026-11-19): skipped where they aren't
# --------------------------------------------------------------------------- #
VIS = "/mnt/user-data/uploads/R0011_645_XP2_001_BW.raf"
IR = "/mnt/user-data/uploads/R0011_645_XP2_002_BW.raf"


@pytest.mark.skipif(not (os.path.exists(VIS) and os.path.exists(IR)),
                    reason="the user's IR test files aren't here")
def test_the_users_pair_finds_dust_that_is_dark_in_white_light():
    vis = ir_dust.read_blocks(VIS)
    ir = ir_dust.read_blocks(IR)
    dm = ir_dust.dust_map(ir, [vis])
    assert 0.08 < dm.k[0] < 0.35
    h, w = ir.shape
    keep = np.zeros((h, w), bool)
    keep[120:2840, 220:3920] = True
    spots = ir_dust.spots_from(dm, ir_dust.SENS_DEFAULT, keep)
    assert len(spots) >= 10
    # revision 2 (spec/ir-dust-v2.md) flags fainter dust than revision 1's
    # 7 sigma, so the test is "dark in white light" at 4x that residual's
    # noise (MAD 0.025 log): measured 93 % of the flagged candidates
    lv = cv2.GaussianBlur(np.log(np.maximum(vis, 0.5)), (0, 0), 1.0)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    bg = np.maximum(cv2.GaussianBlur(cv2.morphologyEx(lv, cv2.MORPH_CLOSE, el), (0, 0), 2.5), lv)
    d = lv - bg
    flagged = ir_dust.flagged(dm, ir_dust.SENS_DEFAULT, keep)
    dark = sum(1 for i in flagged
               if d[dm.cands[i].y0:dm.cands[i].y0 + dm.cands[i].mask.shape[0],
                    dm.cands[i].x0:dm.cands[i].x0 + dm.cands[i].mask.shape[1]][dm.cands[i].mask].min()
               < -0.1)
    assert dark >= 0.85 * len(flagged)


# --------------------------------------------------------------------------- #
# the Dust panel and the canvas
# --------------------------------------------------------------------------- #
def test_flag_badge_heal_before_and_erase_in_the_window(tmp_path, monkeypatch):
    import time
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from ui import theme
    theme.apply_theme(app)
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from ui.main_window import MainWindow
    vis, ir = _scene()
    h, w = ir.shape
    p = str(tmp_path / "R0001_645_XP2_001_BW.png")
    cv2.imwrite(p, (np.clip(vis / 9000.0, 0, 1) * 255).astype(np.uint8))
    ir_path = tmp_path / "R0001_645_XP2_001_4IR.raf"
    ir_path.write_bytes(b"raw")
    monkeypatch.setattr(ir_dust, "read_blocks",
                        lambda path: vis if os.path.normpath(path) == os.path.normpath(p) else ir)
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
    assert prev.dust_mode
    assert "No IR shot" in panel.ir_status.text()
    assert not panel.ir_heal_btn.isEnabled()
    monkeypatch.setattr(panel, "_ask_ir_file", lambda img=None: str(ir_path))
    panel._on_ir_attach()
    end = time.time() + 20
    while time.time() < end and (panel._ir_thread is not None or img.ir_dust_pending is None):
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert img.ir_source == os.path.normpath(str(ir_path))
    flagged = len(img.ir_dust_pending or [])
    assert flagged >= len(SPECKS)
    assert not img.dust_spots                          # flagged, not healed
    assert prev._dust_badge is True                    # the red dot
    assert prev._ir_pending_items                      # outlined in amber
    assert panel.ir_heal_btn.isEnabled() and "Found" in panel.ir_status.text()
    assert not panel.ir_before_btn.isEnabled()
    # Heal
    panel.ir_heal_btn.click()
    app.processEvents()
    assert img.ir_dust_pending is None
    assert sum(s["kind"] == "ir" for s in img.dust_spots) == flagged
    assert prev._dust_badge is False and not prev._ir_pending_items
    assert panel.ir_before_btn.isEnabled() and "painted over" in panel.ir_status.text()
    # Before shows the picture as it was, After the healed one
    healed_key = prev.pixmap_item.pixmap().cacheKey()
    # (the preview kept at Heal, or the detail render kept with it when one
    # had landed by then: which depends on how fast the find was)
    kept = {pm.cacheKey() for pm in prev._dust_before[1:] if pm is not None}
    panel.ir_before_btn.setChecked(True)
    app.processEvents()
    assert prev.pixmap_item.pixmap().cacheKey() in kept
    panel.ir_before_btn.setChecked(False)
    app.processEvents()
    assert prev.pixmap_item.pixmap().cacheKey() not in kept
    # Erase one healed spot; undo brings it back
    panel.erase_btn.setChecked(True)
    assert prev._dust_tool == "erase"
    target = next(s for s in img.dust_spots if s["kind"] == "ir")
    prev._dust_erase(img, [list(target["pts"][0])])
    assert target not in img.dust_spots
    assert img.pop_undo_state()
    assert target in img.dust_spots
    panel.erase_btn.setChecked(False)
    assert prev._dust_tool == "brush"
    # Undo the Heal: the dust is flagged again and the dot comes back
    assert img.pop_undo_state()
    prev.update_preview(prev.current_idx)
    app.processEvents()
    assert len(img.ir_dust_pending) == flagged and prev._dust_badge is True
    del healed_key


def _similarity(w, h, scale, turn_deg, tx, ty):
    """IR moved about the centre: the camera refocused (scale), turned, shifted."""
    import math
    c, sn = scale * math.cos(math.radians(turn_deg)), scale * math.sin(math.radians(turn_deg))
    cx, cy = w / 2, h / 2
    return np.array([[c, -sn, cx - c * cx + sn * cy + tx],
                     [sn, c, cy - sn * cx - c * cy + ty]], np.float64)


def _film(h, w, warp, seed=5):
    """A frame with picture detail everywhere (as film has), specks spread
    over it, and its IR shot moved by `warp`."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    tex = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 3.0)
    tex = (tex - tex.mean()) / tex.std()
    pic = np.clip(0.45 + 0.25 * xx / w + 0.08 * tex, 0.05, 1).astype(np.float32)
    specks = [(int(x), int(y), 3) for x, y in rng.uniform([60, 60], [w - 60, h - 60], (14, 2))]
    dust = np.ones((h, w), np.float32)
    for x, y, r in specks:
        cv2.circle(dust, (x, y), r, 0.12, -1)
    dust = cv2.GaussianBlur(dust, (0, 0), 0.8)
    vis = 9000 * pic * dust * (1 + rng.normal(0, 0.01, (h, w)))
    ir = 600 * pic ** 0.2 * (1 - 0.85 * (1 - dust))
    ir = cv2.warpAffine(ir, np.float32(warp), (w, h), borderMode=cv2.BORDER_REPLICATE)
    ir = ir * (1 + rng.normal(0, 0.04, (h, w)))
    return vis.astype(np.float32), ir.astype(np.float32), specks


@pytest.mark.parametrize("move", [(1.012, 0.4, 9.0, -6.0), (0.99, -0.3, -14.0, 4.0)])
def test_an_ir_shot_refocused_or_moved_is_lined_up(move):
    """spec/ir-dust-and-flat-sets.md §6: scale (focus or rail), turn, shift."""
    h, w = 800, 1200
    M = _similarity(w, h, *move)
    vis, ir, specks = _film(h, w, M)
    dm = ir_dust.dust_map(ir, [vis])
    al = dm.align
    assert al.how == "similarity" and al.tiles >= 6
    inv = np.linalg.inv(np.vstack([M, [0, 0, 1]]))[:2]
    got = np.array(al.matrix)
    err = max(np.hypot(*((got - inv) @ np.array([x, y, 1.0])))
              for x in (0, w / 2, w - 1) for y in (0, h / 2, h - 1))
    assert err < 0.5, err                                 # within half a block anywhere
    spots = ir_dust.spots_from(dm)
    for x, y, _r in specks:
        assert _near(spots, x, y, w, h, tol=2.0), (x, y)
    text = ir_dust.align_text(al)
    assert text.startswith("The IR shot was lined up with the frame (it was ")
    assert ("larger" if move[0] > 1 else "smaller") in text and " px off" in text


def test_an_ir_shot_of_another_frame_says_so():
    h, w = 400, 600
    vis, _ir = _scene()
    rng = np.random.default_rng(7)
    other = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 3) * 500 + 100
    dm = ir_dust.dust_map(other, [vis])
    assert dm.align.how == "none" and dm.align.matrix == ir_dust.IDENTITY
    assert "couldn't be lined up" in ir_dust.align_text(dm.align)
    still = ir_dust.dust_map(_scene()[1], [vis])           # in place: nothing to say
    assert still.align.how != "none" and ir_dust.align_text(still.align) == ""


@pytest.mark.skipif(not os.path.exists("/mnt/user-data/uploads/R0011_645_XP2_002_BW.raf"),
                    reason="the user's pair isn't here")
def test_the_users_pair_sits_together():
    U = "/mnt/user-data/uploads/"
    vis = ir_dust._log(ir_dust.read_blocks(U + "R0011_645_XP2_001_BW.raf"))
    ir = ir_dust._log(ir_dust.read_blocks(U + "R0011_645_XP2_002_BW.raf"))
    al = ir_dust.align(ir, vis)
    assert al.how == "similarity" and al.tiles == 9
    assert abs(al.scale - 1) < 2e-4 and abs(al.turn) < 0.02 and np.hypot(*al.shift) < 0.4


def test_the_ir_flat_follows_the_ir_shots_own_opening():
    """The flat corrects the IR shot before it is lined up, so it is moved
    to that shot's carrier opening, not the frame's."""
    import types
    from core import flat_field as ff
    sys.path.insert(0, os.path.dirname(__file__))
    from test_flat_sets import _field, _shot, OPEN
    ff.set_library([ff.build_tagged_profile(_shot()[0], "ir", light="ir", fmt="645")])
    try:
        move = dict(s=1.04, dx=0.03, dy=-0.01)
        ir_blocks = _shot(**move)[0][..., 1].astype(np.float32)
        img = types.SimpleNamespace(film_format="645", flat_opening=dict(OPEN),
                                    merge_sources=None)
        h, w = ir_blocks.shape
        f, inside = _field(h=h, w=w, **move)
        inner = inside & (cv2.erode(inside.astype(np.uint8), np.ones((25, 25))) > 0)
        own = ir_dust.ir_flat_for(img, (h, w), ir_blocks=ir_blocks) / f
        frames = ir_dust.ir_flat_for(img, (h, w)) / f
        spread = lambda r: float(np.std(r[inner]) / np.mean(r[inner]))  # noqa: E731
        assert spread(own) < 0.02 and spread(own) < 0.5 * spread(frames)
    finally:
        ff.set_library([])
