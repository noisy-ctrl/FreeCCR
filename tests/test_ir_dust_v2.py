#!/usr/bin/env python3
"""IR dust revision 2 (spec/ir-dust-v2.md): local crosstalk, judged by the
frame's own shots too, fibres whole as strokes, a model that learns from the
user's corrections."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core import ir_dust, ir_dust_learn  # noqa: E402


def _scene(h=400, w=600, seed=3, kfun=lambda xx: 0.2, specks=(), loop=None,
           vis_dust=0.55, ir_dust_att=0.81, ir_noise=0.04):
    """Block maps (visible, IR) of a frame with sharp-edged bands. Dust is
    in both shots as measured on the user's frames: the IR's log dip about a
    third of the visible one (ln 0.81 / ln 0.55 = 0.35)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    pic = 0.3 + 0.5 * xx / w + 0.1 * np.sin(yy / 37.0)
    bands = np.zeros((h, w), np.float32)
    for i, x in enumerate(range(40, w - 40, 70)):
        bands[60:340, x:x + 30] = 0.6 if i % 2 else -0.5
    pic = (pic * np.exp(cv2.GaussianBlur(bands, (0, 0), 0.8))).astype(np.float32)
    dv = np.ones((h, w), np.float32)
    di = np.ones((h, w), np.float32)
    for x, y, r in specks:
        cv2.circle(dv, (x, y), r, vis_dust, -1)
        cv2.circle(di, (x, y), r, ir_dust_att, -1)
    if loop is not None:
        cx, cy, R = loop
        cv2.circle(dv, (cx, cy), R, vis_dust, 2)
        cv2.circle(di, (cx, cy), R, ir_dust_att, 2)
    dv = cv2.GaussianBlur(dv, (0, 0), 0.6)
    di = cv2.GaussianBlur(di, (0, 0), 0.9)
    vis = 9000 * pic * dv * (1 + rng.normal(0, 0.01, (h, w)))
    hot = 1.0 + 0.8 * np.exp(-((xx - w / 2) ** 2 + (yy - h / 2) ** 2) / (2 * (w / 4) ** 2))
    ir = 600 * hot * np.exp(kfun(xx) * np.log(pic)) * di * (1 + rng.normal(0, ir_noise, (h, w)))
    return vis.astype(np.float32), ir.astype(np.float32)


def _cand_near(dm, x, y, tol=4):
    for i, c in enumerate(dm.cands):
        cx, cy = c.centre()
        if abs(cx - x) < tol and abs(cy - y) < tol:
            return i, c
    return None, None


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECCR_IR_DUST_LEARNED", str(tmp_path / "learned.json"))
    ir_dust_learn.reset_cache()
    yield tmp_path / "learned.json"
    ir_dust_learn.reset_cache()


# --------------------------------------------------------------------------- #
# the finder
# --------------------------------------------------------------------------- #
def test_local_crosstalk_takes_out_a_crosstalk_that_changes_across_the_frame():
    vis, ir = _scene(kfun=lambda xx: 0.1 + 0.2 * xx / 600)
    L, V = ir_dust._log(ir), ir_dust._log(vis)
    k = ir_dust.fit_crosstalk(L, [V])
    one_k = L - k[0] * V
    local = ir_dust.local_crosstalk(L, [V], k)
    edge = cv2.dilate((np.abs(cv2.Sobel(cv2.GaussianBlur(V, (0, 0), 1), cv2.CV_32F, 1, 0)) > 0.3)
                      .astype(np.uint8), np.ones((5, 5))) > 0
    sm = lambda a: cv2.GaussianBlur(a - cv2.GaussianBlur(a, (0, 0), 8), (0, 0), 2)  # noqa: E731
    away = float(np.abs(sm(local))[~edge].mean())
    excess_one = float(np.abs(sm(one_k))[edge].mean()) - away
    excess_local = float(np.abs(sm(local))[edge].mean()) - away
    assert excess_one > 0 and excess_local < 0.5 * excess_one     # measured 0.0020 -> 0.0007


def test_dust_in_both_shots_is_flagged_and_an_ir_only_dip_isnt(monkeypatch):
    # the model's judgement at this scene's own noise (cleaner than the 8 s
    # shots it was fitted on; the reference is tested on its own below)
    monkeypatch.setattr(ir_dust, "NOISE_REF", 0.0)
    both = [(150, 200, 2), (330, 120, 2)]
    vis, ir = _scene(specks=both)
    _, ir_only = _scene(specks=[(300, 260, 2), (520, 300, 2)], ir_dust_att=0.9)
    _, plain = _scene()
    ir = ir * (ir_only / plain)                  # IR-only dips, nothing in the visible
    dm = ir_dust.dust_map(ir, [vis])
    p = ir_dust.probabilities(dm)
    for x, y, _r in both:
        i, c = _cand_near(dm, x, y)
        assert c is not None and p[i] >= 0.5, (x, y)
    for x, y in ((300, 260), (520, 300)):
        i, c = _cand_near(dm, x, y)
        assert c is not None and 4.0 <= c.feats[0] <= 7.5     # as deep as real dust ...
        assert p[i] < 0.5                                      # ... but not in the picture
    flagged = set(ir_dust.flagged(dm, ir_dust.SENS_DEFAULT))
    assert _cand_near(dm, 300, 260)[0] not in flagged


def test_sensitivity_flags_nest():
    vis, ir = _scene(specks=[(150, 200, 2), (330, 120, 2), (480, 300, 1)])
    dm = ir_dust.dust_map(ir, [vis])
    a, b, c = (set(ir_dust.flagged(dm, s)) for s in (0, 50, 100))
    assert a <= b <= c and len(c) >= len(a)


def test_a_curled_fibre_is_healed_as_strokes_along_it():
    vis, ir = _scene(loop=(300, 200, 22), ir_dust_att=0.75)
    dm = ir_dust.dust_map(ir, [vis])
    spots = ir_dust.spots_from(dm, ir_dust.SENS_DEFAULT)
    h, w = ir.shape
    ring = np.zeros((h, w), np.uint8)
    cv2.circle(ring, (300, 200), 22, 1, 2)
    inner = np.zeros((h, w), np.uint8)
    cv2.circle(inner, (300, 200), 12, 1, -1)
    cov = np.zeros((h, w), np.uint8)
    for s in spots:
        pts = np.array([[x * w, y * h] for x, y in s["pts"]], np.int32)
        r = max(1, int(round(s["r"] * w)))
        if len(pts) == 1:
            cv2.circle(cov, tuple(int(v) for v in pts[0]), r, 1, -1)
        else:
            cv2.polylines(cov, [pts.reshape(-1, 1, 2)], False, 1, 2 * r)
    assert cov[ring > 0].mean() >= 0.95                # the whole loop
    assert cov[inner > 0].mean() <= 0.05               # not the inside of the curl
    assert any(len(s["pts"]) >= 10 for s in spots)     # followed, as a stroke
    assert all("ir_f" in s and len(s["ir_f"]) == len(ir_dust.FEATURES) for s in spots)


def test_thinning_and_paths():
    t = np.zeros((40, 40), bool)
    t[5:8, 5:35] = True                                # a T: three arms from one junction
    t[5:35, 19:22] = True
    paths = ir_dust.skeleton_paths(ir_dust.thin(t))
    assert len([p for p in paths if len(p) >= 5]) == 3
    ring = np.zeros((60, 60), np.uint8)
    cv2.circle(ring, (30, 30), 20, 1, 3)
    paths = ir_dust.skeleton_paths(ir_dust.thin(ring > 0))
    longest = max(paths, key=len)
    assert len(longest) > 100 and longest[0] == longest[-1]   # walked round, closed


def _hand_map():
    """A map of four hand-made candidates and a model on the first feature
    only: A likely, B a weak line-like piece touching A, C the same far
    away, D touching A but too unlikely."""
    def cand(x0, y0, f0, line_like=True, shape=(3, 12)):
        feats = np.zeros(len(ir_dust.FEATURES), np.float32)
        feats[0] = f0
        return ir_dust.Candidate(x0=x0, y0=y0, mask=np.ones(shape, bool), feats=feats,
                                 seed=(x0 + 1, y0 + 1), line_like=line_like)
    cs = [cand(10, 10, 10.0), cand(24, 10, 4.0), cand(80, 80, 4.0), cand(10, 15, 2.0)]
    dm = ir_dust.DustMap(z=np.zeros((100, 100), np.float16), size=(100, 100), cands=cs,
                         kind="merged")
    model = (np.zeros(8), np.ones(8), np.r_[1.0, np.zeros(7)], -5.0)   # p = sigmoid(f0 - 5)
    return dm, model


def test_a_fibres_weaker_pieces_join_it():
    dm, model = _hand_map()
    assert ir_dust.flagged(dm, 50, model=model) == [0, 1]      # B joins, C and D don't
    keep = np.zeros((100, 100), bool)
    keep[50:, 50:] = True
    assert ir_dust.flagged(dm, 50, keep, model=model) == []    # A's seed is outside the crop


def test_the_dark_ir_hint():
    assert ir_dust.dark_ir_text(None) == "" and ir_dust.dark_ir_text(0.35) == ""
    t = ir_dust.dark_ir_text(0.08)
    assert "8 %" in t and "30 s" in t


# --------------------------------------------------------------------------- #
# learning from the user's corrections
# --------------------------------------------------------------------------- #
NOISE = [4.6, 4.5, 0.1, 0.05, 0.0, 0.9, 3.3, 2.5]     # IR-deep, nothing in the picture


class _Img:
    merge_sources = ["r", "g", "b"]
    file_path = "f"

    def __init__(self, n=12):
        self.dust_spots = [{"kind": "brush", "pts": [[0.05, 0.05]], "r": 0.01}]
        self.ir_dust_pending = [
            {"kind": "ir", "pts": [[0.1 + 0.06 * i, 0.5]], "r": 0.005,
             "ir_f": [v + 0.01 * i for v in NOISE], "ir_c": i} for i in range(n)]
        # a fibre's two strokes share one candidate
        self.ir_dust_pending.append({"kind": "ir", "pts": [[0.9, 0.9]], "r": 0.005,
                                     "ir_f": [9, 9, 8, 0.8, 0.5, 0.2, 4, 20], "ir_c": 99})
        self.ir_dust_pending.append({"kind": "ir", "pts": [[0.92, 0.9]], "r": 0.005,
                                     "ir_f": [9, 9, 8, 0.8, 0.5, 0.2, 4, 20], "ir_c": 99})


def test_heal_teaches_lightly_once_per_candidate(store):
    img = _Img(n=3)
    assert ir_dust.heal(img) == 5
    ex = ir_dust_learn.examples("merged")
    assert len(ex) == 4                                  # 3 + the fibre once
    assert all(e[8] == 1 and abs(e[9] - ir_dust_learn.HEAL_WEIGHT) < 1e-9 for e in ex)
    assert ir_dust_learn.explicit_count("merged") == 0
    assert ir_dust_learn.examples("single") == []


def test_erasing_teaches_and_moves_the_model_after_enough(store):
    feat = np.array([NOISE], np.float64)
    before = ir_dust.probabilities(
        ir_dust.DustMap(z=np.zeros((2, 2), np.float16), kind="merged",
                        cands=[ir_dust.Candidate(0, 0, np.ones((1, 1), bool),
                                                 feat[0].astype(np.float32), (0, 0), False)]))[0]
    img = _Img(n=12)
    trial = _Img(n=12)
    ir_dust.erase(trial, [[0.1, 0.5]], 0.01, 1.0, learn=False)   # the canvas's trial run
    assert ir_dust_learn.examples("merged") == []
    # nine erasures: not enough to move it
    for i in range(9):
        ir_dust.erase(img, [[0.1 + 0.06 * i, 0.5]], 0.004, 1.0)
    assert ir_dust_learn.explicit_count("merged") == 9
    m = ir_dust_learn.model_for("merged")
    assert np.allclose(m[2], ir_dust_learn.default_model()[2])
    for i in range(9, 12):
        ir_dust.erase(img, [[0.1 + 0.06 * i, 0.5]], 0.004, 1.0)
    assert ir_dust_learn.explicit_count("merged") == 12
    dm = ir_dust.DustMap(z=np.zeros((2, 2), np.float16), kind="merged",
                         cands=[ir_dust.Candidate(0, 0, np.ones((1, 1), bool),
                                                  feat[0].astype(np.float32), (0, 0), False)])
    after = ir_dust.probabilities(dm)[0]
    assert after < before and after < 0.5
    # what it learned is kept in the store, and Forget takes it away
    assert store.exists()
    ir_dust_learn.reset_cache()
    assert ir_dust_learn.explicit_count("merged") == 12
    ir_dust_learn.forget("merged")
    assert ir_dust_learn.examples("merged") == []
    assert np.allclose(ir_dust_learn.model_for("merged")[2], ir_dust_learn.default_model()[2])


def test_the_store_keeps_the_newest(store, monkeypatch):
    monkeypatch.setattr(ir_dust_learn, "MAX_EXAMPLES", 20)
    for i in range(30):
        ir_dust_learn.add("single", [([float(i)] * 8, 0, 1.0)])
    ex = ir_dust_learn.examples("single")
    assert len(ex) == 20 and ex[0][0] == 10.0 and ex[-1][0] == 29.0
    assert ir_dust_learn.add("ir-only", [([0.0] * 8, 0, 1.0)]) == 0


def test_a_hand_stroke_over_a_missed_candidate_teaches(store):
    dm, _model = _hand_map()
    img = _Img(n=0)
    img.ir_dust_pending = [dict(img.ir_dust_pending[0], ir_c=0)]   # A is flagged
    img._ir_dust_map = dm
    # over B (missed) and A (already flagged): only B teaches
    n = ir_dust.learn_from_stroke(img, [[0.29, 0.11], [0.15, 0.11]], 0.01, 1.0)
    assert n == 1
    ex = ir_dust_learn.examples("merged")
    assert len(ex) == 1 and ex[0][8] == 1 and ex[0][9] == 1.0 and ex[0][0] == 4.0
    img._ir_dust_map = None
    assert ir_dust.learn_from_stroke(img, [[0.29, 0.11]], 0.01, 1.0) == 0


def test_fit_map_moves_with_the_evidence():
    base = ir_dust_learn.default_model()
    X = np.array([NOISE] * 40, np.float64)
    m_few = ir_dust_learn.fit_map(X[:5], np.zeros(5), np.ones(5), base)
    m_many = ir_dust_learn.fit_map(X, np.zeros(40), np.ones(40), base)
    p = lambda m: 1 / (1 + np.exp(-(m[3] + ((X[0] - m[0]) / m[1]) @ m[2])))  # noqa: E731
    assert p(base) > p(m_few) > p(m_many)


# --------------------------------------------------------------------------- #
# the Dust panel
# --------------------------------------------------------------------------- #
def test_the_panel_says_what_it_learned_and_forgets_on_request(store, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv[:1])
    from widgets.dust_panel import DustRemovalPanel
    assert DustRemovalPanel._ir_learned_text("merged") == ""
    ir_dust_learn.add("merged", [(NOISE, 0, 1.0)] * 11)
    assert DustRemovalPanel._ir_learned_text("merged") == (
        "Learned from 11 of your corrections on this kind of scan.")

    class P:                    # the panel's own methods on a stand-in
        _ir_learned_text = DustRemovalPanel._ir_learned_text
        ir_heading_actions = DustRemovalPanel.ir_heading_actions
        _forget_ir_learning = DustRemovalPanel._forget_ir_learning
        image_preview = None
        refreshed = 0

        def _current_image(self):
            return _Img(n=0)

        def _confirm_forget_ir(self, kind):
            return self.answer

        def refresh_ir_section(self):
            self.refreshed += 1

    p = P()
    (text, cb, enabled), = p.ir_heading_actions()
    assert text == "Forget What It Learned…" and enabled
    p.answer = False
    cb()
    assert ir_dust_learn.explicit_count("merged") == 11
    p.answer = True
    cb()
    assert ir_dust_learn.examples("merged") == [] and p.refreshed == 1
    (_t, _cb, enabled), = p.ir_heading_actions()
    assert not enabled


# --------------------------------------------------------------------------- #
# bridges: a fibre's pieces joined where the IR barely shows it (§3.6)
# --------------------------------------------------------------------------- #
def test_a_fibre_the_ir_loses_in_the_middle_is_healed_end_to_end():
    """The user's wavy line on 002: the IR shows its top and bottom, the
    colour shots all of it. The pieces are flagged and the middle bridged."""
    h, w = 400, 600
    vis, ir = _scene()
    line = np.zeros((h, w), np.uint8)
    cv2.line(line, (300, 120), (306, 280), 1, 2)
    ends = line.copy()
    ends[192:208] = 0                                   # the IR loses the middle
    lv = cv2.GaussianBlur(np.where(line > 0, 0.55, 1.0).astype(np.float32), (0, 0), 0.6)
    li = cv2.GaussianBlur(np.where(ends > 0, 0.75, 1.0).astype(np.float32), (0, 0), 0.9)
    dm = ir_dust.dust_map(ir * li, [vis * lv])
    spots = ir_dust.spots_from(dm, ir_dust.SENS_DEFAULT)
    assert any("ir_bridge" in s for s in spots)
    assert all("ir_f" not in s for s in spots if "ir_bridge" in s)   # nothing to learn from
    cov = np.zeros((h, w), np.uint8)
    for s in spots:
        pts = np.array([[x * w, y * h] for x, y in s["pts"]], np.int32)
        r = max(1, int(round(s["r"] * w)))
        if len(pts) == 1:
            cv2.circle(cov, tuple(int(v) for v in pts[0]), r, 1, -1)
        else:
            cv2.polylines(cov, [pts.reshape(-1, 1, 2)], False, 1, 2 * r)
    assert cov[line > 0].mean() >= 0.95
    gap = line.copy()
    gap[:192] = 0
    gap[208:] = 0
    assert cov[gap > 0].mean() >= 0.95                 # the middle too
    no_bridge = np.zeros((h, w), np.uint8)
    for s in spots:
        if "ir_bridge" not in s:
            pts = np.array([[x * w, y * h] for x, y in s["pts"]], np.int32)
            r = max(1, int(round(s["r"] * w)))
            if len(pts) == 1:
                cv2.circle(no_bridge, tuple(int(v) for v in pts[0]), r, 1, -1)
            else:
                cv2.polylines(no_bridge, [pts.reshape(-1, 1, 2)], False, 1, 2 * r)
    assert no_bridge[gap > 0].mean() < 0.8             # which the pieces alone don't
    # without the colour shots' line in the gap there is no bridge
    lv2 = cv2.GaussianBlur(np.where(ends > 0, 0.55, 1.0).astype(np.float32), (0, 0), 0.6)
    dm2 = ir_dust.dust_map(ir * li, [vis * lv2])
    assert not any("ir_bridge" in s for s in ir_dust.spots_from(dm2, ir_dust.SENS_DEFAULT))


def test_a_bridge_follows_a_thin_dark_line_not_an_edge():
    vz = np.zeros((60, 60), np.float32)
    vz[10:50, 29:31] = -6.0                               # a thin dark line
    path = ir_dust.dark_path(vz, (30, 12), (30, 47))
    assert path and all(29 <= x <= 30 for x, _y in path[2:-2])
    assert ir_dust._ridge_share(vz, path, 3.0) >= 0.9
    edge = np.zeros((60, 60), np.float32)
    edge[:, :30] = -6.0                                   # dark on one side only
    path = [(28, y) for y in range(10, 50)]
    assert ir_dust._ridge_share(edge, path, 3.0) < 0.2



# --- revision 2026-11-22: a cleaner IR shot is read at the model's noise -----
def test_a_cleaner_ir_shot_doesnt_flood_the_finder():
    specks = [(150, 200, 2), (330, 120, 2), (450, 300, 3)]
    vis_n, ir_n = _scene(specks=specks, ir_noise=0.06)      # as noisy as an 8 s shot
    vis_c, ir_c = _scene(specks=specks, ir_noise=0.03)      # twice as clean (a 30 s shot)
    noisy, clean = ir_dust.dust_map(ir_n, [vis_n]), ir_dust.dust_map(ir_c, [vis_c])
    assert noisy.noise >= ir_dust.NOISE_REF > clean.noise
    assert len(clean.cands) <= len(noisy.cands)              # 27 without the reference
    fl_n, fl_c = ir_dust.flagged(noisy, 50), ir_dust.flagged(clean, 50)
    assert len(fl_c) <= len(fl_n) + 1
    for x, y, _r in specks:                                 # the dust the noisy shot finds, it finds
        i, c = _cand_near(noisy, x, y)
        if c is None or i not in fl_n:
            continue
        j, c = _cand_near(clean, x, y)
        assert c is not None and j in fl_c, (x, y)


def test_a_shot_at_the_reference_noise_is_read_as_before(monkeypatch):
    vis, ir = _scene(specks=[(150, 200, 2), (450, 300, 3)], ir_noise=0.06)
    with_ref = ir_dust.dust_map(ir, [vis])
    monkeypatch.setattr(ir_dust, "NOISE_REF", 0.0)
    without = ir_dust.dust_map(ir, [vis])
    assert np.array_equal(with_ref.z, without.z)


# --- revision 2026-11-22 b: a found speck covers what shows in the colour shots
def test_a_speck_the_ir_sees_only_part_of_is_healed_whole():
    """The user's hooked speck: the IR saw its top, the colour shots the hook."""
    h, w = 400, 600
    vis, ir = _scene(h=h, w=w)
    dv = np.ones((h, w), np.float32)
    cv2.circle(dv, (300, 200), 3, 0.5, -1)                    # the part the IR sees too
    cv2.line(dv, (300, 200), (306, 212), 0.5, 3)              # the hook it doesn't
    dv = cv2.GaussianBlur(dv, (0, 0), 0.6)
    di = np.ones((h, w), np.float32)
    cv2.circle(di, (300, 200), 3, 0.78, -1)
    di = cv2.GaussianBlur(di, (0, 0), 0.9)
    dm = ir_dust.dust_map((ir * di).astype(np.float32), [(vis * dv).astype(np.float32)])
    spots = ir_dust.spots_from(dm, 50)
    from core.ccr_processor import rasterize_dust_mask
    cov = rasterize_dust_mask(spots, h, w) > 0
    hook = dv < 0.8
    assert cov[hook].mean() >= 0.9                             # the hook too
    assert cov.sum() < 6 * hook.sum()                          # not a big disc round it


def test_with_visible_leaves_a_find_alone_when_nothing_more_shows():
    vis, ir = _scene(specks=[(300, 200, 3)])
    dm = ir_dust.dust_map(ir, [vis])
    i, c = _cand_near(dm, 300, 200)
    assert c is not None
    assert ir_dust.with_visible(c, None) is c
    grown = ir_dust.with_visible(c, dm.vz)
    assert grown.mask.sum() <= ir_dust.VIS_REACH_MAX ** 2 * max(c.mask.sum(), 4)
