"""Colour Contrast (spec/colour-contrast.md): areas pushed apart in colour,
lightness, greys, gradations, grain and skin left alone."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import colour_contrast as cc                  # noqa: E402
from core.oklab import from_oklab, to_oklab             # noqa: E402


def patch(h, w, L, a, b):
    lab = np.dstack([np.full((h, w), L), np.full((h, w), a), np.full((h, w), b)])
    return from_oklab(lab.reshape(-1, 3).astype(np.float32)).reshape(h, w, 3)


def two_areas(W=1200, noise=0.0, seed=0):
    H = W * 2 // 3
    img = patch(H, W, 0.65, -0.05, 0.04)                       # a green ...
    img[:, W // 2:] = patch(H, W - W // 2, 0.65, -0.06, 0.015)  # ... beside a cooler green
    if noise:
        img = np.clip(img + np.random.default_rng(seed).normal(0, noise, img.shape).astype(np.float32), 0, 1)
    return img


def lab(img):
    return to_oklab(img.reshape(-1, 3)).reshape(img.shape)


def test_zero_is_the_input_itself():
    img = two_areas(300)
    assert cc.apply_colour_contrast(img, 0) is img


def test_lightness_kept_and_in_range():
    img = two_areas(600, noise=0.01)
    out = cc.apply_colour_contrast(img, 100)
    assert out.dtype == np.float32 and out.min() >= 0 and out.max() <= 1
    assert np.abs(lab(out)[..., 0] - lab(img)[..., 0]).max() < 1e-4


def test_neighbouring_areas_move_apart():
    img = two_areas(1200)
    W = 1200
    def gap(im):
        o = lab(im)
        return np.linalg.norm(o[50:100, W // 2 - 50:W // 2 - 10, 1:].reshape(-1, 2).mean(0)
                              - o[50:100, W // 2 + 10:W // 2 + 50, 1:].reshape(-1, 2).mean(0))
    g0, g50, g100 = gap(img), gap(cc.apply_colour_contrast(img, 50)), gap(cc.apply_colour_contrast(img, 100))
    assert g0 < g50 < g100 and g100 > 1.3 * g0


def test_greys_stay_grey():
    img = patch(400, 600, 0.6, 0.06, 0.03)
    img[150:250, 250:350] = patch(100, 100, 0.6, 0.0, 0.0)
    out = lab(cc.apply_colour_contrast(img, 100))
    assert np.abs(out[170:230, 270:330, 1:]).max() < 2e-4


def test_a_linear_ramp_is_not_moved_inside_the_frame():
    x = np.linspace(-0.08, 0.08, 600, dtype=np.float32)
    ramp = from_oklab(np.dstack([np.full((60, 600), 0.6), np.tile(x, (60, 1)),
                                 np.full((60, 600), 0.04)]).reshape(-1, 3).astype(np.float32)).reshape(60, 600, 3)
    out = cc.apply_colour_contrast(ramp, 100)
    assert np.abs(out - ramp)[:, 60:540].max() * 255 < 0.5


def test_grain_does_not_grow():
    for sd in (0.01, 0.02):
        img = two_areas(1200, noise=sd)
        out = cc.apply_colour_contrast(img, 100)
        g = []
        for im in (img, out):
            o = lab(im)[..., 1:].astype(np.float32)
            g.append((o - cv2.GaussianBlur(o, (0, 0), 3))[20:780, 20:580].std())
        assert g[1] <= g[0] * 1.005


def test_skin_is_protected():
    # a skin-coloured area on green: protected, it barely moves; unprotected it is pushed
    img = patch(600, 900, 0.65, -0.06, 0.05)
    img[200:400, 350:550] = patch(200, 200, 0.70, 0.035, 0.065)        # OKLab hue ~62 deg
    def face(o):
        return o[260:340, 410:490, 1:].reshape(-1, 2).mean(0)
    f0 = face(lab(img))
    on = face(lab(cc.apply_colour_contrast(img, 100)))
    off = face(lab(cc.apply_colour_contrast(img, 100, protect_skin=False)))
    assert np.linalg.norm(off - f0) > 0.003
    assert np.linalg.norm(on - f0) < 0.25 * np.linalg.norm(off - f0)


def test_resolution_independent():
    big = two_areas(1200)
    small = cv2.resize(big, (600, 400), interpolation=cv2.INTER_AREA)
    a = cv2.resize(cc.apply_colour_contrast(big, 100), (600, 400), interpolation=cv2.INTER_AREA)
    b = cc.apply_colour_contrast(small, 100)
    assert np.abs(a - b)[20:380, 20:580].max() * 255 < 2.0


def test_unmoved_pixels_come_back_exactly():
    img = patch(200, 300, 0.6, 0.05, 0.02)                              # one area: nothing to push
    out = cc.apply_colour_contrast(img, 100)
    assert np.array_equal(out, img)
