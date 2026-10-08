#!/usr/bin/env python3
"""Look fitter (spec/film-look-lut.md): the model recovers a known look and
per-frame corrections, the Channel Levels mapping is exact, the rebate black
point is found, and registration undoes a mirror + warp."""

import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import look_fit as lf  # noqa: E402


def _true_prior():
    p = lf.Prior.init()
    M = np.array([[0.95, 0.04, 0.06], [0.12, 0.85, 0.05], [0.10, 0.08, 0.90]])
    p[:9] = M.ravel()
    # steeper mid-section, a bit different per channel
    c = p[9:].reshape(3, -1)
    c[:, 0] = [-0.05, -0.08, -0.1]
    c[:, 1:] += np.linspace(-0.6, 0.4, c.shape[1] - 1)[None, :]
    c[1, 1:] += 0.1
    return p


def _synthetic_frames(K=4, n=1200, seed=0):
    rng = np.random.default_rng(seed)
    p = _true_prior()
    q = rng.normal(0, [0.05, 0.05, 0.05, 0.02, 0.02, 0.02], (K, 6))
    q -= q.mean(0)
    frames = []
    for k in range(K):
        d = rng.uniform(0.05, 0.9, (n, 3))
        d = 0.6 * d + 0.4 * d.mean(1, keepdims=True)        # film-like: mostly low chroma
        t = np.clip(lf.frame_apply(lambda x: lf.Prior.eval(p, x), d, q[k]), 0, 1)
        frames.append((d, t))
    return frames, q


def test_recovers_a_known_look_and_frame_corrections():
    frames, q_true = _synthetic_frames()
    look, q = lf.fit_look(frames, grid_n=9, sub=600, rounds=1)
    for k, (d, t) in enumerate(frames):
        e = lf.frame_error(look, d, t, q[k])
        assert np.median(e) < 0.6, np.median(e)
    np.testing.assert_allclose(q, q_true, atol=0.03)


def test_held_out_frame_needs_only_six_numbers():
    frames, _ = _synthetic_frames(K=4, seed=1)
    look, _ = lf.fit_look(frames[:3], grid_n=9, sub=600, rounds=1)
    d, t = frames[3]
    qj = lf.fit_frame(look, d, t)
    assert np.median(lf.frame_error(look, d, t, qj)) < 1.0


def test_cube_table_shape_and_range():
    frames, _ = _synthetic_frames(K=3, n=400, seed=2)
    look, _ = lf.fit_look(frames, grid_n=9, sub=300, rounds=0)
    tab = look.cube_table(9)
    assert tab.shape == (9, 9, 9, 3)
    assert tab.min() >= 0 and tab.max() <= 1


def test_channel_levels_mapping_is_exact():
    from core.ccr_processor import _apply_channel_levels
    q = np.array([0.07, -0.02, 0.11, 0.03, -0.015, 0.05])
    sl = lf.channel_levels_for(q)
    d = np.random.default_rng(3).uniform(-0.1, 1.0, (8, 8, 3)).astype(np.float32)
    got = d.copy()
    _apply_channel_levels(got, 0.0, sl["ch_master_shift"], 0.0,
                          sl["ch_r_shift"], sl["ch_r_gain"], 0.0,
                          sl["ch_g_shift"], sl["ch_g_gain"], 0.0,
                          sl["ch_b_shift"], sl["ch_b_gain"], 0.0,
                          clamp=False, include_master_gain=False)
    want = d * (1 + q[3:]) + q[:3]
    np.testing.assert_allclose(got, want, atol=1e-5)


def test_rebate_black_point_picks_the_clear_band():
    h, w = 900, 1200
    v = np.zeros((h, w, 3), np.float32)               # holder: black
    v[60:840, 80:1120] = (0.40, 0.33, 0.36)           # clear rebate
    v[110:790, 130:1070] = (0.15, 0.12, 0.10)         # image (denser)
    bp, side, bands = lf.rebate_black_point(v)
    np.testing.assert_allclose(bp, (0.40, 0.33, 0.36), atol=1e-6)
    m = lf.image_area_mask(v, bands, margin=10, erode=5)
    assert m[450, 600] and not m[70, 600] and not m[5, 5]


def _texture(h=600, w=800, seed=0):
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 3)
    for _ in range(40):
        x, y = rng.integers(0, w), rng.integers(0, h)
        cv2.circle(img, (int(x), int(y)), int(rng.integers(8, 40)),
                   float(rng.random()), -1)
    return cv2.normalize(img, None, 0, 1, cv2.NORM_MINMAX)


def test_registration_undoes_mirror_and_warp():
    g = _texture()
    d = np.stack([g * 0.8, g * 0.7, g * 0.6], -1).astype(np.float32)
    mask = np.ones(g.shape, bool)
    # the "lab scan": mirrored, slightly rotated + scaled, tone-mapped
    lab = d[:, ::-1] ** 0.6
    M = cv2.getRotationMatrix2D((400, 300), 2.0, 0.9)
    lab = cv2.warpAffine(np.ascontiguousarray(lab), M, (800, 600))
    other = np.stack([_texture(seed=9)] * 3, -1)
    labs = [lf.prepare_lab(other, 800), lf.prepare_lab(lab, 800)]
    reg = lf.register(d, mask, labs)
    assert reg is not None and reg["index"] == 1
    assert reg["orient"] in ("hflip", "vflip")           # equal up to a 180° turn
    assert reg["reproj_px"] < 1.0
    warped, mw = lf.warp_to_lab(reg, labs[1][0])
    core = mw & (cv2.erode(mw.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0)
    resid = np.abs(warped[..., 0] ** 0.6 / 0.8 ** 0.6 - labs[1][0][..., 0] / 0.8 ** 0.6)
    assert np.median(resid[core]) < 0.03


def test_de2000_reference_pair():
    # Sharma et al. (2005) test data, pair 1
    a = np.array([[50.0, 2.6772, -79.7751]])
    b = np.array([[50.0, 0.0, -82.7485]])
    assert lf.de2000(a, b)[0] == pytest.approx(2.0425, abs=1e-3)
