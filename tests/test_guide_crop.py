#!/usr/bin/env python3
"""The crop from Capture's frame guide (spec/guide-follow-and-crop.md §3-4):
each side of the guide snaps to an edge auto crop finds close by, otherwise
stays where you put it; the capture carries the guide onto its scan; the
roll check and crop learning leave it alone; the setting turns it off."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core import auto_crop as ac  # noqa: E402
from core import capture_guide as cg  # noqa: E402
from test_auto_crop import H, W, HOLDER, BASE, scan_645, _image  # noqa: E402


def _guide_from(rect, angle=0.0, inset=(0, 0, 0, 0)):
    """A guide (scan fractions) on the pixel box `rect`, each side moved in
    by `inset` (L, T, R, B px)."""
    x0, y0, x1, y1 = rect
    x0, y0, x1, y1 = x0 + inset[0], y0 + inset[1], x1 - inset[2], y1 - inset[3]
    return {"cx": (x0 + x1) / 2 / W, "cy": (y0 + y1) / 2 / H,
            "w": (x1 - x0) / W, "h": (y1 - y0) / H, "angle": angle, "format": "645"}


def _box_px(fc):
    x1, y1, x2, y2 = fc.rect
    return x1 * W, y1 * H, x2 * W, y2 * H


def test_a_guide_close_to_the_frame_takes_the_frame_found():
    raw, truth = scan_645()
    plain = ac.detect_frame(raw)
    assert plain.usable
    g = _guide_from(truth, inset=(-6, 5, 7, -4))           # a few px off on every side
    fc = ac.detect_guided(raw, g)
    assert fc.learned == "guide" and fc.usable
    assert fc.geom["snapped"] == 4
    assert np.allclose(fc.rect, plain.rect, atol=1e-9) and fc.angle == pytest.approx(plain.angle)
    assert "Capture's frame guide" in fc.summary()


def test_a_side_you_placed_inside_stays_there():
    raw, truth = scan_645()
    plain = ac.detect_frame(raw)
    g = _guide_from(truth, inset=(0, 0, 40, 0))            # right side 40 px (~5 %) in
    fc = ac.detect_guided(raw, g)
    assert fc.geom["snapped"] == 3 and fc.confidence == "medium"
    x0, y0, x1, y1 = _box_px(fc)
    p0, q0, p1, q1 = _box_px(plain)
    assert x0 == pytest.approx(p0, abs=0.5) and y0 == pytest.approx(q0, abs=0.5)
    assert y1 == pytest.approx(q1, abs=0.5)
    assert x1 == pytest.approx(truth[2] - 40, abs=0.5)   # where you put it


def test_no_frame_found_gives_the_guide():
    flat = np.full((H, W, 3), int(BASE * 65535), np.uint16)
    assert not ac.detect_frame(flat).usable
    g = _guide_from((100, 80, 980, 730), angle=0.6)
    for fc in (ac.detect_guided(flat, g), ac.snap_to_guide(None, ac.guide_box(g, W, H), W, H)):
        assert fc.usable and fc.learned == "guide" and fc.geom["snapped"] == 0
        assert fc.angle == pytest.approx(0.6)
        cx, cy = (fc.rect[0] + fc.rect[2]) / 2, (fc.rect[1] + fc.rect[3]) / 2
        assert cx == pytest.approx(g["cx"]) and cy == pytest.approx(g["cy"])
        assert fc.rect[2] - fc.rect[0] == pytest.approx(g["w"])
        assert fc.rect[3] - fc.rect[1] == pytest.approx(g["h"])


def test_a_guide_too_small_or_off_the_scan_is_not_used():
    raw, _truth = scan_645()
    assert ac.detect_guided(raw, {"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.8}) is None
    assert ac.detect_guided(raw, {"cx": 1.4, "cy": 0.5, "w": 0.8, "h": 0.8}) is None
    assert ac.detect_guided(raw, {"cx": 0.5}) is None


# --------------------------------------------------------------------------- #
# the backend
# --------------------------------------------------------------------------- #
@pytest.fixture
def backend():
    from core.ccr_backend import ccr_backend
    saved = (ccr_backend.images, ccr_backend.file_paths, ccr_backend.crop_from_guide,
             ccr_backend.auto_crop_learn)
    cg.clear()
    yield ccr_backend
    (ccr_backend.images, ccr_backend.file_paths, ccr_backend.crop_from_guide,
     ccr_backend.auto_crop_learn) = saved
    cg.clear()


def test_every_auto_crop_uses_the_guide_and_the_setting_turns_it_off(backend, tmp_path):
    raw, truth = scan_645()
    img = _image(tmp_path, raw)
    plain = backend.detect_frame_crop(img, raw=raw)
    assert plain.learned != "guide"
    img.capture_guide = _guide_from(truth, inset=(0, 0, 40, 0))
    fc = backend.detect_frame_crop(img, raw=raw)
    assert fc.learned == "guide" and img.auto_crop_last is fc
    frame, applied = backend.auto_crop_one(img)
    assert applied and frame.learned == "guide" and img.crop_rect == tuple(frame.rect)
    backend.crop_from_guide = False
    off = backend.detect_frame_crop(img, raw=raw)
    assert off.learned != "guide" and np.allclose(off.rect, plain.rect)


def test_a_guided_crop_is_never_learned_from(backend, tmp_path):
    raw, truth = scan_645()
    img = _image(tmp_path, raw)
    backend.auto_crop_learn = True
    img.capture_guide = _guide_from(truth)
    backend.detect_frame_crop(img, raw=raw)
    img.crop_rect = img.auto_crop_last.rect
    assert backend.learn_confirmed_crop(img) is False


def test_the_roll_check_leaves_guided_crops_alone(backend, tmp_path):
    raw, truth = scan_645()
    imgs, frames = [], []
    for i in range(4):
        img = _image(tmp_path / f"{i}", raw) if (tmp_path / f"{i}").mkdir() is None else None
        imgs.append(img)
        frames.append(backend.detect_frame_crop(img, raw=raw))
    imgs[3].capture_guide = _guide_from(truth, inset=(0, 0, 40, 0))
    guided = backend.detect_frame_crop(imgs[3], raw=raw)
    frames[3] = guided
    out, _n = backend._roll_consistent_crops(imgs, frames, [raw] * 4)
    assert out[3] is guided


# --------------------------------------------------------------------------- #
# the capture carries it onto its scan
# --------------------------------------------------------------------------- #
def _live_of(raw):
    """What Capture's live view showed: the scan, display-encoded, 1024 wide."""
    v = np.power(raw.astype(np.float32) / 65535.0, 1 / 2.2) * 255.0
    return cv2.resize(v, (1024, 768), interpolation=cv2.INTER_AREA).astype(np.uint8)


def test_a_captured_frame_gets_the_guide_and_not_the_last_frames_crop(backend, tmp_path):
    from core.tether_watcher import TetherWatchWorker, apply_look_template
    from core.catalog import serialize_image, _restore_image
    raw, truth = scan_645()
    img = _image(tmp_path, raw)
    g = _guide_from(truth, inset=(0, 0, 40, 0))
    rec = cg.make_record(g, _live_of(raw), following=True)
    cg.remember(img.file_path, record=rec)
    TetherWatchWorker._guide_from_capture(img, [img.file_path])
    got = img.capture_guide
    assert got is not None and got["how"] == "matched" and got["format"] == "645"
    for k in ("cx", "cy", "w", "h"):
        assert got[k] == pytest.approx(g[k], abs=0.003), k
    # the frame before had a hand crop: this one gets its own, from the guide
    t = {"adjustment_settings": {}, "area_layers": [], "rotation_angle": 0,
         "fine_rotation_angle": 0, "horizontal_mirrored": False, "vertical_mirrored": False,
         "crop_rect": (0.2, 0.2, 0.8, 0.8), "crop_angle": 1.0, "crop_is_auto": False,
         "color_profile": "color"}
    apply_look_template(img, t)
    assert img.crop_rect is None and backend.is_crop_candidate(img)
    backend.crop_from_guide = False
    other = _image(tmp_path / "o" if (tmp_path / "o").mkdir() is None else tmp_path, raw)
    other.capture_guide = dict(got)
    apply_look_template(other, t)
    assert other.crop_rect == (0.2, 0.2, 0.8, 0.8)          # off: inherited as before
    # the catalogue keeps it, and keeps an otherwise untouched capture for it
    back = _restore_image(img.file_path, serialize_image(img))
    assert back.capture_guide == got
    from core.catalog import _is_pristine
    assert not _is_pristine(serialize_image(img))


def test_a_shot_with_no_guide_leaves_the_capture_alone(backend, tmp_path):
    from core.tether_watcher import TetherWatchWorker
    raw, _truth = scan_645()
    img = _image(tmp_path, raw)
    TetherWatchWorker._guide_from_capture(img, [img.file_path])
    assert getattr(img, "capture_guide", None) is None


def test_a_hot_folder_file_is_stamped_with_the_shot_running(backend, tmp_path):
    """The Scanlight's shutter cable: files come through the camera's own
    software; each is tied to the shot running as it becomes ready."""
    raw, truth = scan_645()
    rec = cg.make_record(_guide_from(truth), _live_of(raw))
    cg.begin_shot(rec)
    p = str(tmp_path / "DSCF0001.RAF")
    assert cg.stamp(p) and cg.lookup([p]) is rec


def test_a_tethered_capture_converts_and_crops_to_its_guide(backend, tmp_path):
    """End to end on the worker: the shot's guide onto the scan, the
    conversion (as Convert Current), then the crop from the guide."""
    from core.tether_watcher import TetherWatchWorker
    raw, truth = scan_645()
    img = _image(tmp_path, raw)
    saved = (backend.positive_mode, backend.black_point_bgr, backend.crop_after_convert,
             backend.images, backend.file_paths)
    try:
        backend.positive_mode = False
        backend.black_point_bgr = tuple(float(BASE * 65535) for _ in range(3))
        backend.crop_after_convert = True
        backend.images, backend.file_paths = [], []
        g = _guide_from(truth, inset=(0, 0, 40, 0))
        cg.remember(img.file_path, record=cg.make_record(g, _live_of(raw)))
        got = []
        w = TetherWatchWorker()
        w.captured.connect(got.append)
        w._finish([img], None, paths=[img.file_path])
        assert got == [img] and img.converted
        assert img.capture_guide is not None
        assert img.crop_rect is not None and img.crop_rect == img.auto_crop_rect
        assert img.auto_crop_last.learned == "guide" and img.auto_crop_last.geom["snapped"] == 3
        x1 = img.crop_rect[2] * W
        assert x1 == pytest.approx(truth[2] - 40, abs=3)          # your right side
    finally:
        (backend.positive_mode, backend.black_point_bgr, backend.crop_after_convert,
         backend.images, backend.file_paths) = saved
