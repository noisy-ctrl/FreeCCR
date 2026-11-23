"""The dust heal rebuilds only the speck, from many small patches
(spec/dust-patch-fill.md, core/dust_fill.py)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from core import ccr_processor as cp  # noqa: E402
from core import dust_fill as dfl  # noqa: E402


def _grain(h, w, seed=1, level=0.45, amp=0.03, sigma=0.8):
    rng = np.random.default_rng(seed)
    g = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), sigma)
    return level + amp * g / g.std()


def _rgb16(f):
    return (np.clip(np.repeat(f[..., None], 3, 2), 0, 1) * 65535).astype(np.uint16)


def _fibre(h, w, x0, y0, x1, y1, width=2, amp=0.4):
    m = np.zeros((h, w), np.float32)
    cv2.line(m, (x0, y0), (x1, y1), 1.0, width, cv2.LINE_AA)
    return amp * m


def _spot(kind, pts, r, w, h):
    return {"kind": kind, "pts": [[x / w, y / h] for x, y in pts], "r": r / w}


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch):
    monkeypatch.setattr(cp, "DUST_ENGINE", "patch")


# --------------------------------------------------------------------------- #
# the fill
# --------------------------------------------------------------------------- #
def test_no_spots_is_a_no_op_and_nothing_outside_the_holes_moves():
    img = _rgb16(_grain(120, 120))
    assert dfl.heal(img, []) is img
    dirty = img.copy()
    dirty[58:62, 40:80] = 60000
    out = dfl.heal(dirty, [_spot("ir", [(40, 60), (80, 60)], 6, 120, 120)])
    changed = (out != dirty).any(2)
    yy, xx = np.nonzero(changed)
    assert changed.sum() > 0
    # only round the fibre (its hole and soft edge), never elsewhere
    assert yy.min() >= 50 and yy.max() <= 70 and xx.min() >= 30 and xx.max() <= 90


def test_a_fibre_across_an_edge_leaves_the_edge_where_it_was():
    """The user's case: a hair over a wire's edge. The old heal pasted one
    disc from elsewhere and broke the edge; the fill must carry it on."""
    H = W = 160
    base = _grain(H, W, seed=3)
    base[:, 80:] += 0.30                                   # a vertical step edge
    clean = _rgb16(base)
    dirty = _rgb16(base + _fibre(H, W, 60, 70, 100, 90))
    out = dfl.heal(dirty, [_spot("ir", [(60, 70), (100, 90)], 7, W, H)])
    sm = lambda a: cv2.GaussianBlur(a[..., 1].astype(np.float32) / 257, (0, 0), 2.0)  # noqa: E731
    err = np.abs(sm(out) - sm(clean))[60:100, 55:105]
    before = np.abs(sm(dirty) - sm(clean))[60:100, 55:105]
    assert before.max() > 25                               # the fibre showed
    assert err.max() < 9, err.max()                        # edge carried on, no disc
    assert err.mean() < 1.5


def test_the_grain_in_the_hole_is_grain_not_a_smooth_patch():
    H = W = 140
    base = _grain(H, W, seed=5)
    yy, xx = np.ogrid[:H, :W]
    d = np.hypot(xx - 70, yy - 70)
    dirty = _rgb16(base + 0.4 * np.exp(-(d / 4.0) ** 2))
    out = dfl.heal(dirty, [_spot("ir", [(70, 70)], 12, W, H)])
    f = out[..., 1].astype(np.float32)
    hp = f - cv2.blur(f, (3, 3))
    inner = d <= 5
    ring = (d > 20) & (d < 40)
    ratio = hp[inner].std() / hp[ring].std()
    assert 0.7 < ratio < 1.3, ratio
    # and the speck has gone: its middle is at the picture's level
    assert abs(f[inner].mean() - f[ring].mean()) / 257 < 4


def test_a_found_spot_replaces_only_the_speck_a_painted_one_what_was_painted():
    H = W = 160
    base = _grain(H, W, seed=7)
    yy, xx = np.ogrid[:H, :W]
    d = np.hypot(xx - 80, yy - 80)
    dirty = _rgb16(base + 0.5 * (d <= 2.5))
    found = dfl.heal(dirty, [_spot("ir", [(80, 80)], 15, W, H)])
    painted = dfl.heal(dirty, [_spot("brush", [(80, 80)], 15, W, H)])
    n_found = int((found != dirty).any(2).sum())
    n_painted = int((painted != dirty).any(2).sum())
    area = np.pi * 15 ** 2
    assert n_found < 0.25 * area                      # just the speck and its edge
    assert n_painted > 0.9 * area                     # the whole painted dab


def test_nothing_standing_out_replaces_the_whole_area_or_where_the_ir_saw_it():
    """A painted spot with nothing in it: what was painted goes. An IR spot:
    the IR saw dust there (just not standing out in the picture), so its own
    extent goes (spec/dust-detect-v3.md §3), not the generous area round it."""
    img = _rgb16(_grain(100, 100, seed=9))
    info = []
    out = dfl.heal(img, [_spot("brush", [(50, 50)], 8, 100, 100)], info=info)
    assert info[0].get("whole_area")
    assert int((out != img).any(2).sum()) > 0.8 * np.pi * 64
    info = []
    out = dfl.heal(img, [_spot("ir", [(50, 50)], 8, 100, 100)], info=info)
    n = int((out != img).any(2).sum())
    core = np.pi * (8 * dfl.IR_CORE) ** 2
    assert 0.5 * core < n < 0.5 * np.pi * 64, (n, core)


def test_nothing_is_sampled_from_outside_the_crop():
    H = W = 160
    base = _grain(H, W, seed=11)
    img = np.repeat(base[..., None], 3, 2)
    img[:, :60, 0] = 0.95                                  # red outside the crop
    img[:, :60, 1:] = 0.05
    dirty = (np.clip(img, 0, 1) * 65535).astype(np.uint16)
    dirty[78:83, 75:85] = 60000
    keep = np.zeros((H, W), np.uint8)
    keep[:, 60:] = 1
    out = dfl.heal(dirty, [_spot("ir", [(80, 80)], 9, W, H)], keep=keep)
    hole = (out != dirty).any(2)
    assert hole.any()
    assert out[hole][:, 0].max() < 0.8 * 65535             # no red came in


def test_a_windowed_base_heals_in_its_own_codes_and_keeps_headroom():
    from core.ccr_processor import WS_B, WS_W
    H = W = 120
    disp = _grain(H, W, seed=13, level=0.9, amp=0.05)
    disp[:, :] += 0.4                                      # above white: headroom
    codes = (WS_B + disp * (WS_W - WS_B)).astype(np.float32)
    img = np.repeat(codes[..., None], 3, 2)
    img[58:62, 50:70] += 0.6 * (WS_W - WS_B)
    img16 = np.clip(img, 0, 65535).astype(np.uint16)
    out = cp.apply_dust_removal(img16, [_spot("ir", [(50, 60), (70, 60)], 6, W, H)],
                                ws_windowed=True)
    hole = (out != img16).any(2)
    assert hole.any()
    # the fill sits at the picture's level, above display white, as copied codes
    fill = out[hole][:, 1].astype(np.float32)
    around = img16[40:50, 40:80, 1].astype(np.float32)
    assert abs(fill.mean() - around.mean()) < 0.03 * (WS_W - WS_B)
    assert fill.mean() > WS_W


def test_the_same_input_heals_the_same_way():
    img = _rgb16(_grain(120, 120, seed=17))
    img[55:65, 55:58] = 62000
    sp = [_spot("ir", [(56, 60)], 9, 120, 120)]
    assert np.array_equal(dfl.heal(img, sp), dfl.heal(img, sp))


def test_mono_buffers_heal_too():
    f = _grain(100, 100, seed=19)
    img = (np.clip(f, 0, 1) * 65535).astype(np.uint16)
    img[48:52, 30:70] = 63000
    out = dfl.heal(img, [_spot("ir", [(30, 50), (70, 50)], 5, 100, 100)])
    assert out.ndim == 2 and out.dtype == np.uint16
    assert out[48:52, 35:65].mean() < 0.6 * 65535


def test_a_spot_a_few_pixels_wide_at_the_preview_size_is_diffused_with_grain():
    H = W = 200
    base = _grain(H, W, seed=21)
    dirty = _rgb16(base + _fibre(H, W, 40, 100, 160, 104, width=1))
    info = []
    out = dfl.heal(dirty, [_spot("ir", [(40, 100), (160, 104)], 2, W, H)], info=info)
    assert info[0].get("small")
    f = out[..., 1].astype(np.float32) / 257
    assert np.abs(f[99:106, 45:155].mean() - f[80:95, 45:155].mean()) < 3
    hp = f - cv2.blur(f, (3, 3))
    assert hp[100:104, 50:150].std() > 0.4 * hp[80:95, 50:150].std()   # not a smooth line


def test_the_speck_is_followed_where_it_runs_out_of_the_area():
    H = W = 200
    base = _grain(H, W, seed=23)
    dirty = _rgb16(base + _fibre(H, W, 40, 100, 150, 100, width=2))
    # the finder's stroke stops 15 px short of the fibre's end (his frame 004)
    out = dfl.heal(dirty, [_spot("ir", [(40, 100), (135, 100)], 6, W, H)])
    f = out[..., 1].astype(np.float32) / 257
    assert f[98:103, 138:148].max() - f[80:95, 138:148].mean() < 15


# --------------------------------------------------------------------------- #
# the app
# --------------------------------------------------------------------------- #
def test_the_heal_is_remembered_for_the_same_base_and_spots(monkeypatch):
    from core.ccr_image import CCRImage
    img = CCRImage.__new__(CCRImage)
    base = _rgb16(_grain(200, 200, seed=29))
    base[98:102, 60:140] = 62000
    spots = [_spot("ir", [(60, 100), (140, 100)], 6, 200, 200)]
    calls = []
    real = cp.apply_dust_removal

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(cp, "apply_dust_removal", counting)
    a = img._patch_heal_cached(base, spots, None, False, repr(spots))
    b = img._patch_heal_cached(base.copy(), spots, None, False, repr(spots))
    assert len(calls) == 1 and np.array_equal(a, b)
    spots2 = spots + [_spot("brush", [(30, 30)], 5, 200, 200)]
    img._patch_heal_cached(base, spots2, None, False, repr(spots2))
    assert len(calls) == 2


def test_the_old_clone_heal_is_still_there_to_compare(monkeypatch):
    img = _rgb16(_grain(100, 100, seed=31))
    img[48:52, 48:52] = 62000
    sp = [_spot("brush", [(50, 50)], 6, 100, 100)]
    seen = []
    monkeypatch.setattr(cp, "_patch_heal", lambda *a, **k: seen.append(1) or a[0])
    monkeypatch.setattr(cp, "DUST_ENGINE", "clone")
    out = cp.apply_dust_removal(img, sp)
    assert not seen and out[50, 50, 0] < 50000
    monkeypatch.setattr(cp, "DUST_ENGINE", "patch")
    cp.apply_dust_removal(img, sp)
    assert seen


def test_the_panel_hides_feather_and_heal_sources_with_the_patch_fill():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.dust_panel import DustRemovalPanel
    import inspect
    src = inspect.getsource(DustRemovalPanel)
    assert "self._patch_fill" in src
    assert "heal sources" not in DustRemovalPanel.MORE_TEXT
