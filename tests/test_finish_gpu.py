#!/usr/bin/env python3
"""The finishing tools on the GPU (spec/gpu-finishing-tools.md): the same
picture as the numpy reference, step by step and end to end, with the
quality rules holding on the GPU path too. Runs on whatever OpenCL device the
tests have (pocl on the CPU in CI), with FREECCR_GPU_FINISH forced on."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import cv2  # noqa: E402

from core import ccr_processor as cp  # noqa: E402
from core import finish_gpu as fg  # noqa: E402
from core.golden import apply_golden  # noqa: E402
from core.shadow_neutral import apply_shadow_neutralise  # noqa: E402
from core.glow import apply_glow  # noqa: E402
from core import oklab as ok  # noqa: E402


def _gpu():
    if not cp.OPENCL_AVAILABLE or not cp._initialize_opencl():
        pytest.skip("no OpenCL device")
    return cp._opencl_cache['ctx'], cp._opencl_cache['queue']


@pytest.fixture
def gpu(monkeypatch):
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    ctx, queue = _gpu()
    return ctx, queue, fg.for_queue(ctx, queue)


def _scene(H=540, W=810, seed=0):
    """A picture with something for every tool: a warm sunlit wall, a lamp,
    teal-cast shadows with grain, a sky gradient and strong colours."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    img = np.empty((H, W, 3), np.float32)
    img[...] = np.stack([0.55 + 0.3 * yy / H, 0.62 + 0.25 * yy / H, 0.80 + 0.1 * yy / H], -1)
    img[H // 3:, : W // 2] = (0.90, 0.84, 0.70)                     # sunlit cream wall
    img[2 * H // 3:, W // 2:] = (0.07, 0.10, 0.11)                  # teal shadow
    img[H // 3: H // 2, W // 2: 3 * W // 4] = (0.75, 0.15, 0.10)    # a red object
    d = np.hypot(yy - H * 0.25, xx - W * 0.8)
    img[d < 14] = (1.0, 0.95, 0.85)                                  # a lamp
    img += rng.normal(0, 0.008, img.shape).astype(np.float32)
    img = np.clip(cv2.GaussianBlur(img, (0, 0), 0.7), 0, 1)
    return np.round(img * 65535).astype(np.float32) / np.float32(65535)


def _codes(a, b):
    return np.abs(a.astype(np.float64) - b.astype(np.float64)) * 65535


def _close(cpu, gpu_out, max_codes=3.0, frac_1=0.999):
    d = _codes(cpu, gpu_out)
    assert d.max() <= max_codes, d.max()
    assert np.mean(d <= 1.0) >= frac_1, np.mean(d <= 1.0)


# --------------------------------------------------------------------------- #
# building blocks against their references
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("r", [1, 3, 17, 40, 150])
def test_box_filter_matches_cv2(gpu, r):
    ctx, q, g = gpu
    rng = np.random.default_rng(r)
    x = rng.random((97, 131)).astype(np.float32)
    H, W = x.shape
    a, b, t = g.upload(x.ravel()), g.empty(H * W), g.empty(H * W)
    g.box(a, b, t, W, H, r)
    ref = ok._box(x, r)
    assert np.abs(b.get().reshape(H, W) - ref).max() < 2e-6


@pytest.mark.parametrize("r,eps", [(2, ok.DENOISE_EPS[0]), (6, ok.GUIDE_EPS), (22, 0.01)])
def test_guided_filters_match(gpu, r, eps):
    ctx, q, g = gpu
    rng = np.random.default_rng(1)
    I = cv2.GaussianBlur(rng.random((120, 160)).astype(np.float32), (0, 0), 3)
    I[:, 80:] += 0.4
    p = (I + rng.normal(0, 0.02, I.shape)).astype(np.float32)
    H, W = I.shape
    gI, gp = g.upload(I.ravel()), g.upload(p.ravel())
    s = g.guided_smooth(gp, W, H, r, eps).get().reshape(H, W)
    assert np.abs(s - ok.guided_smooth(p, r, eps)).max() < 2e-5
    f = g.guided_filter(gp, gI, W, H, r, eps).get().reshape(H, W)
    assert np.abs(f - ok.guided_filter(p, I, r, eps)).max() < 2e-5


def _apply_area(x, xt, yt):
    xoff, xsrc, xw = xt
    yoff, ysrc, yw = yt
    h = np.stack([(x[:, xsrc[xoff[i]:xoff[i + 1]]] * xw[xoff[i]:xoff[i + 1]]).sum(1)
                  for i in range(len(xoff) - 1)], 1)
    return np.stack([(h[ysrc[yoff[j]:yoff[j + 1]]] * yw[yoff[j]:yoff[j + 1], None]).sum(0)
                     for j in range(len(yoff) - 1)], 0)


@pytest.mark.parametrize("src,dst", [((90, 120), (45, 60)), ((97, 131), (24, 33)),
                                     ((100, 103), (9, 10))])
def test_area_tables_are_opencvs(src, dst):
    rng = np.random.default_rng(2)
    x = rng.random(src).astype(np.float32)
    ref = cv2.resize(x, (dst[1], dst[0]), interpolation=cv2.INTER_AREA)
    got = _apply_area(x.astype(np.float64), fg.area_table(src[1], dst[1]),
                      fg.area_table(src[0], dst[0]))
    assert np.abs(got - ref).max() < 2e-6


@pytest.mark.parametrize("src,dst", [((45, 60), (90, 120)), ((24, 33), (97, 131)),
                                     ((9, 10), (100, 103))])
def test_linear_tables_are_opencvs(src, dst):
    rng = np.random.default_rng(3)
    x = rng.random(src).astype(np.float32)
    ref = cv2.resize(x, (dst[1], dst[0]), interpolation=cv2.INTER_LINEAR)
    xs0, xs1, xa = fg.linear_table_x(src[1], dst[1])
    ys0, ys1, yb = fg.linear_table_y(src[0], dst[0])
    r0 = x[ys0][:, xs0] * (1 - xa) + x[ys0][:, xs1] * xa
    r1 = x[ys1][:, xs0] * (1 - xa) + x[ys1][:, xs1] * xa
    got = r0 * (1 - yb[:, None]) + r1 * yb[:, None]
    assert np.abs(got - ref).max() < 2e-6


@pytest.mark.parametrize("shape,sigma", [((60, 80), 4.0), ((40, 300), 13.5), ((12, 50), 6.0)])
def test_gaussian_matches_cv2(gpu, shape, sigma):
    """Including a strip so thin the kernel reflects more than once."""
    ctx, q, g = gpu
    rng = np.random.default_rng(4)
    x = rng.random(shape + (3,)).astype(np.float32)
    H, W = shape
    out = g.gauss3(g.upload(x.ravel()), W, H, sigma).get().reshape(H, W, 3)
    ref = cv2.GaussianBlur(x, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
    assert np.abs(out - ref).max() < 2e-6


# --------------------------------------------------------------------------- #
# each tool, and the chain, against numpy
# --------------------------------------------------------------------------- #

GOLDEN_CASES = [None, {"hue": 60, "start": 40, "clean": 0, "neutral": 60, "smooth": 90},
                {"smooth": 0}]
SHADOW_CASES = [None, {"range": 45, "reach": 100, "depth": 70, "smooth": 0},
                {"depth": 100, "smooth": 100}]
GLOW_CASES = [None, {"reach": 20, "above": 85}, {"reach": 100, "above": 99}, {"reach": 30, "above": 88}]


@pytest.mark.parametrize("shape", GOLDEN_CASES)
@pytest.mark.parametrize("size", [(540, 810), (1080, 1620)])
def test_golden_matches_numpy(gpu, shape, size):
    ctx, q, g = gpu
    x = _scene(*size)
    _close(apply_golden(x, 80, shape), fg.finish_numpy_image(ctx, q, x, golden=80,
                                                             golden_shape=shape))


@pytest.mark.parametrize("shape", SHADOW_CASES)
@pytest.mark.parametrize("size", [(540, 810), (1080, 1620)])
def test_shadow_matches_numpy(gpu, shape, size):
    ctx, q, g = gpu
    x = _scene(*size)
    _close(apply_shadow_neutralise(x, 90, shape),
           fg.finish_numpy_image(ctx, q, x, shadow=90, shadow_shape=shape))


@pytest.mark.parametrize("shape", GLOW_CASES)
@pytest.mark.parametrize("size", [(540, 810), (1080, 1620), (60, 900)])
def test_glow_matches_numpy(gpu, shape, size):
    """(60 x 900: a thin strip, where the blur reflects more than once.)"""
    ctx, q, g = gpu
    x = _scene(*size)
    _close(apply_glow(x, 100, shape), fg.finish_numpy_image(ctx, q, x, glow=100,
                                                            glow_shape=shape))


def test_the_chain_matches_numpy(gpu):
    ctx, q, g = gpu
    x = _scene()
    cpu = apply_glow(apply_shadow_neutralise(apply_golden(x, 70), 60), 50)
    _close(cpu, fg.finish_numpy_image(ctx, q, x, golden=70, shadow=60, glow=50))


# --------------------------------------------------------------------------- #
# quality on the GPU path
# --------------------------------------------------------------------------- #

def test_zero_and_untouched_pixels_are_exact(gpu):
    import pyopencl.array as cla
    ctx, q, g = gpu
    x = _scene()
    H, W = x.shape[:2]
    src = (x * np.float32(65535.0)).reshape(-1, 3)
    buf = cla.to_device(q, src)
    g.finish(buf, H, W)                                  # nothing on
    np.testing.assert_array_equal(buf.get(), src)
    # a grey between the shadows and the highlights (OKLab L ~0.46): none of
    # the three acts
    grey = np.full((200, 300, 3), 0.35, np.float32) * np.float32(65535.0)
    buf = cla.to_device(q, grey.reshape(-1, 3))
    g.finish(buf, 200, 300, golden=100, shadow=100, glow=100)
    np.testing.assert_array_equal(buf.get(), grey.reshape(-1, 3))


def test_gpu_results_stay_in_range_and_glow_never_darkens(gpu):
    ctx, q, g = gpu
    x = _scene()
    out = fg.finish_numpy_image(ctx, q, x, glow=100, glow_shape={"reach": 100, "above": 85})
    assert out.min() >= 0 and out.max() <= 1.0 + 1e-7
    assert np.all(out >= x - 1e-6)


def test_gpu_shadow_never_grows_grain(gpu):
    ctx, q, g = gpu
    rng = np.random.default_rng(9)
    clean = np.full((400, 600, 3), (0.06, 0.09, 0.10), np.float32)
    noise = rng.normal(0, 0.006, clean.shape).astype(np.float32)
    noisy = np.clip(clean + noise, 0, 1)
    a = fg.finish_numpy_image(ctx, q, clean, shadow=100)
    b = fg.finish_numpy_image(ctx, q, noisy, shadow=100)
    lab_in = ok.to_oklab((noisy - clean + 0.5).reshape(-1, 3))
    lab_out = ok.to_oklab((b - a + 0.5).reshape(-1, 3))
    assert lab_out[:, 1:].std() <= lab_in[:, 1:].std() * 1.001


def test_gpu_golden_ramp_stays_monotone(gpu):
    ctx, q, g = gpu
    x = np.linspace(0, 1, 2048, dtype=np.float32)
    ramp = np.repeat((x[:, None] * np.array((1.0, 0.96, 0.86), np.float32))[None], 24, 0)
    out = fg.finish_numpy_image(ctx, q, ramp, golden=100)
    L = ok.to_oklab(out[12]).reshape(-1, 3)[:, 0]
    assert np.all(np.diff(L) > -1e-5)


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #

def _u16(seed=5, shape=(270, 405, 3)):
    x = _scene(shape[0], shape[1], seed)
    return (x * 65535).astype(np.uint16)


KW = dict(golden_highlights=70, shadow_neutralise=60, glow=50, contrast=10, saturation=10)


def test_render_on_gpu_matches_numpy_finishing(monkeypatch):
    _gpu()
    img = _u16()
    monkeypatch.setenv("FREECCR_GPU_FINISH", "0")
    ref = cp.adjust_image_opencl(img, **KW).astype(np.int64)
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    got = cp.adjust_image_opencl(img, **KW).astype(np.int64)
    d = np.abs(ref - got)
    assert d.max() <= 3 and np.mean(d <= 1) > 0.999


def test_the_gpu_finish_is_used_and_numpy_is_not(monkeypatch):
    _gpu()
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    for name in ("apply_golden", "apply_shadow_neutralise", "apply_glow"):
        monkeypatch.setattr(cp, name, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("numpy finishing ran")))
    cp.adjust_image_opencl(_u16(), **KW)


def test_a_gpu_failure_falls_back_to_numpy(monkeypatch):
    ctx, queue = _gpu()
    img = _u16()
    monkeypatch.setenv("FREECCR_GPU_FINISH", "0")
    ref = cp.adjust_image_opencl(img, **KW)
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    monkeypatch.setattr(fg.FinishGPU, "finish",
                        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(cp, "_gpu_finish_broken", False)
    got = cp.adjust_image_opencl(img, **KW)
    np.testing.assert_array_equal(got, ref)
    assert cp._gpu_finish_broken is True            # not retried every render


def test_auto_runs_only_on_a_gpu(monkeypatch):
    class Dev:
        def __init__(self, t):
            self.type = t
    if fg.cl is None:
        pytest.skip("no pyopencl")
    monkeypatch.delenv("FREECCR_GPU_FINISH", raising=False)
    assert fg.enabled_for(Dev(int(fg.cl.device_type.GPU))) is True
    assert fg.enabled_for(Dev(int(fg.cl.device_type.CPU))) is False
    monkeypatch.setenv("FREECCR_GPU_FINISH", "0")
    assert fg.enabled_for(Dev(int(fg.cl.device_type.GPU))) is False
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    assert fg.enabled_for(Dev(int(fg.cl.device_type.CPU))) is True
