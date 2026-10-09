#!/usr/bin/env python3
"""Auto crop (spec/auto-crop.md) on synthetic negative scans: a 645 frame in a
holder with a rebate strip, a 35mm strip with sprocket rows, a fogged leader
frame, a picture that fills the scan; the crop mapping; the backend and panel
hooks."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core.auto_crop import detect_frame, log_luminance, _rotate  # noqa: E402
from core.ccr_processor import apply_crop_to_image  # noqa: E402

H, W = 810, 1080
BASE = 0.30          # clear film (film base) in the raw scan, fraction of full scale
HOLDER = 0.0006      # opaque holder


def _picture(h, w, seed=1, lo=0.05, hi=0.22):
    """A textured 'negative' picture: denser than the base everywhere."""
    rng = np.random.default_rng(seed)
    n = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 18)
    n = (n - n.min()) / (n.max() - n.min())
    yy, xx = np.mgrid[0:h, 0:w]
    n = 0.7 * n + 0.3 * (xx / w)
    for _ in range(12):                                   # some hard-edged shapes
        x, y = rng.integers(0, w - 60), rng.integers(0, h - 60)
        n[y:y + rng.integers(20, 60), x:x + rng.integers(20, 60)] = rng.uniform(0, 1)
    return lo + (hi - lo) * n


def _to_raw(v, tilt=0.0, blur=1.2):
    v = cv2.GaussianBlur(v.astype(np.float32), (0, 0), blur)
    if tilt:
        m = cv2.getRotationMatrix2D((W / 2, H / 2), -tilt, 1.0)
        v = cv2.warpAffine(v, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    v = np.clip(v, 0, 1) * 65535
    return np.repeat(v[..., None], 3, axis=2).astype(np.uint16)


def scan_645(tilt=0.0):
    """Holder, a clear-film window, a rebate strip, then a 645 picture."""
    v = np.full((H, W), HOLDER, np.float32)
    v[30:H - 30, 40:W - 40] = BASE                       # film window (rebate)
    ph = H - 30 * 2 - 2 * 18                             # 18 px rebate strip
    pw = int(round(ph * 56 / 41.5))
    x0 = (W - pw) // 2
    y0 = (H - ph) // 2
    v[y0:y0 + ph, x0:x0 + pw] = _picture(ph, pw)
    return _to_raw(v, tilt), (x0, y0, x0 + pw, y0 + ph)


def scan_strip(invisible_top=False):
    """Horizontal 35mm strip: sprocket rows, a 3:2 frame, gaps and neighbours."""
    v = np.full((H, W), BASE, np.float32)
    for top in (20, H - 90):                             # sprocket rows (clipped)
        for x in range(10, W, 130):
            v[top:top + 70, x:x + 46] = 1.0
    fy0, fy1 = 120, H - 120                              # gate between the rows
    fh = fy1 - fy0
    fw = int(round(fh * 1.5))
    fx0 = (W - fw) // 2
    pic = _picture(fh, fw, seed=4)
    if invisible_top:
        pic[:40] = BASE                                  # picture as thin as the base
    v[fy0:fy1, fx0:fx0 + fw] = pic
    gap = 30
    v[fy0:fy1, :max(0, fx0 - gap)] = _picture(fh, max(0, fx0 - gap), seed=7)
    v[fy0:fy1, fx0 + fw + gap:] = _picture(fh, W - (fx0 + fw + gap), seed=8)
    return _to_raw(v), (fx0, fy0, fx0 + fw, fy1)


def scan_leader():
    """35mm in a masking holder; the left half of the frame is fogged leader
    (dense, featureless) — the crop must still be the full 3:2 frame."""
    v = np.full((H, W), HOLDER, np.float32)
    fh = 640
    fw = 960
    x0, y0 = (W - fw) // 2, (H - fh) // 2
    pic = _picture(fh, fw, seed=9)
    pic[:, :fw // 2] = 0.012                             # fog: very dense
    v[y0:y0 + fh, x0:x0 + fw] = pic
    return _to_raw(v), (x0, y0, x0 + fw, y0 + fh)


def _inside(det_rect, truth, tol):
    """det inside truth, missing at most `tol` (fraction) on each side."""
    x0, y0, x1, y1 = det_rect
    tx0, ty0, tx1, ty1 = truth
    tw, th = tx1 - tx0, ty1 - ty0
    return (tx0 - 2 <= x0 <= tx0 + tol * tw and tx1 - tol * tw <= x1 <= tx1 + 2
            and ty0 - 2 <= y0 <= ty0 + tol * th and ty1 - tol * th <= y1 <= ty1 + 2)


# --- detection ---------------------------------------------------------------

@pytest.mark.parametrize("tilt", [0.0, 0.6])
def test_645_in_holder_crops_past_holder_and_rebate(tilt):
    raw, truth = scan_645(tilt)
    fc = detect_frame(raw)
    assert fc.usable, fc.reason
    assert abs(fc.angle - tilt) < 0.06
    assert fc.fmt == "645" and not fc.strip
    assert _inside(fc.debug["deskewed_rect"], truth, 0.02), (fc.debug["deskewed_rect"], truth)


def test_strip_frame_between_sprocket_rows_is_3_2():
    raw, truth = scan_strip()
    fc = detect_frame(raw)
    assert fc.usable and fc.strip and fc.fmt == "35mm 3:2"
    x0, y0, x1, y1 = fc.debug["deskewed_rect"]
    assert abs((x1 - x0) / (y1 - y0) - 1.5) < 0.03
    assert _inside((x0, y0, x1, y1), truth, 0.02), ((x0, y0, x1, y1), truth)


def test_strip_with_an_invisible_gate_edge():
    raw, truth = scan_strip(invisible_top=True)
    fc = detect_frame(raw)
    assert fc.usable and fc.strip
    assert _inside(fc.debug["deskewed_rect"], truth, 0.03), (fc.debug["deskewed_rect"], truth)


def test_fogged_leader_keeps_the_whole_frame():
    raw, truth = scan_leader()
    fc = detect_frame(raw)
    assert fc.usable
    x0, y0, x1, y1 = fc.debug["deskewed_rect"]
    assert _inside((x0, y0, x1, y1), truth, 0.03), ((x0, y0, x1, y1), truth)
    assert (x1 - x0) > 0.95 * (truth[2] - truth[0])        # fog counts as picture


def test_picture_filling_the_scan_is_not_cropped():
    v = _picture(H, W, seed=11)
    fc = detect_frame(_to_raw(v))
    assert not fc.usable
    assert fc.rect is None or fc.confidence in ("low", "none")


def test_crop_reproduces_the_straightened_box():
    raw, _truth = scan_645(tilt=0.8)
    fc = detect_frame(raw)
    x0, y0, x1, y1 = fc.debug["deskewed_rect"]
    want = _rotate(log_luminance(raw), fc.angle)[y0:y1, x0:x1]
    got = log_luminance(apply_crop_to_image(raw, fc.rect, fc.angle))
    h, w = min(want.shape[0], got.shape[0]), min(want.shape[1], got.shape[1])
    assert abs(got.shape[0] - want.shape[0]) <= 1 and abs(got.shape[1] - want.shape[1]) <= 1
    assert float(np.median(np.abs(want[2:h - 2, 2:w - 2] - got[2:h - 2, 2:w - 2]))) < 0.01


# --- backend + UI hooks ---------------------------------------------------------

@pytest.fixture
def backend():
    from core.ccr_backend import ccr_backend
    saved = (ccr_backend.images, ccr_backend.file_paths, ccr_backend.auto_crop,
             ccr_backend.auto_black_point, ccr_backend.black_point_bgr,
             ccr_backend.white_point_bgr)
    yield ccr_backend
    (ccr_backend.images, ccr_backend.file_paths, ccr_backend.auto_crop,
     ccr_backend.auto_black_point, ccr_backend.black_point_bgr,
     ccr_backend.white_point_bgr) = saved


def _image(tmp_path, raw):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from core.ccr_image import CCRImage
    path = str(tmp_path / "scan.tif")
    cv2.imwrite(path, cv2.cvtColor(raw, cv2.COLOR_RGB2BGR))
    img = CCRImage(path)
    img.resized_raw = raw.copy()
    return img


def test_apply_auto_crop_sets_crop_and_is_one_undo(backend, tmp_path):
    from core.auto_crop import FrameCrop
    raw, _ = scan_645(0.5)
    img = _image(tmp_path, raw)
    img.fine_rotation_angle = 120
    fc = detect_frame(raw)
    n = len(img.undo_stack)
    assert backend.apply_auto_crop(img, fc)
    assert img.crop_rect == tuple(fc.rect) and img.crop_angle == pytest.approx(fc.angle)
    assert img.fine_rotation_angle == 0 and len(img.undo_stack) == n + 1
    img.pop_undo_state()
    assert img.crop_rect is None and img.fine_rotation_angle == 120
    low = FrameCrop((0.1, 0.1, 0.9, 0.9), confidence="low", reason="x")
    assert not backend.apply_auto_crop(img, low) and img.crop_rect is None


def test_convert_all_with_auto_crop(backend, tmp_path):
    raw, _ = scan_645(0.4)
    img = _image(tmp_path, raw)
    backend.images, backend.file_paths = [img], [img.file_path]
    backend.auto_black_point = False
    backend.black_point_bgr = tuple(float(BASE * 65535) for _ in range(3))
    backend.white_point_bgr = None
    backend.auto_crop = True
    backend.apply_bwpoint_to_all_images()
    assert img.converted and img.crop_rect is not None
    assert img.crop_angle == pytest.approx(0.4, abs=0.06)
    assert backend.last_auto_crop_summary["cropped"] == 1


def test_crop_panel_auto_sets_pending_box_without_committing(backend, tmp_path, monkeypatch):
    sys.path.insert(0, os.path.dirname(__file__))
    import test_full_res_zoom as fz
    from core.auto_crop import FrameCrop
    ip = fz._setup()
    img = backend.images[ip.current_idx]
    before = (getattr(img, "crop_rect", None), getattr(img, "crop_angle", 0.0))
    monkeypatch.setattr(ip, "_draw_crop_overlay", lambda: None)
    assert ip.auto_detect_crop() is None                   # only in crop mode
    ip.crop_mode = True                                    # (the stub can't re-render)
    monkeypatch.setattr(backend, "detect_frame_crop",
                        lambda im, raw=None: FrameCrop((0.1, 0.2, 0.8, 0.9), angle=0.5,
                                                       confidence="high", fmt="645"))
    res = ip.auto_detect_crop()
    assert res.confidence == "high"
    w, h = ip.current_pixmap.width(), ip.current_pixmap.height()
    box = ip._pending_crop_local
    assert box.left() == pytest.approx(0.1 * w) and box.bottom() == pytest.approx(0.9 * h)
    assert ip._pending_crop_angle == pytest.approx(0.5)
    after = (getattr(img, "crop_rect", None), getattr(img, "crop_angle", 0.0))
    assert after == before                                 # nothing committed
    ip.crop_mode = False
