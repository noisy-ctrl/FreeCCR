#!/usr/bin/env python3
"""Auto crop learning (spec/auto-crop-learning.md): the crop memory, priors in
detection (size, habit, soft sides, the usual-frame fallback), what counts as
a correction, and the roll check."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

import test_auto_crop as tac  # noqa: E402  (the synthetic scans)
from core import auto_crop as ac  # noqa: E402
from core import crop_memory as cm  # noqa: E402
from core.crop_memory import CropMemory, box_of, inward_shifts, det_record  # noqa: E402

KEY = "TEST CAM|1080x810|single"


def _truth(fc, habit, w, h):
    """The detection's box with each side pulled in by `habit` (L, R, T, B)."""
    cx, cy, bw, bh, a = box_of(fc.rect, fc.angle, w, h)
    L, R, T, B = habit
    t = np.deg2rad(a)
    qx, qy = (L - R) / 2 * bw, (T - B) / 2 * bh
    return [cx + np.cos(t) * qx - np.sin(t) * qy, cy + np.sin(t) * qx + np.cos(t) * qy,
            bw * (1 - L - R), bh * (1 - T - B), a]


def _err(truth, fc, w, h):
    sh, _ = inward_shifts(truth, box_of(fc.rect, fc.angle, w, h))
    return max(abs(v) for v in sh)


def _teach(mem, raw, habit):
    h, w = raw.shape[:2]
    fc = ac.detect_frame(raw, priors=mem.priors(KEY))
    truth = _truth(ac.detect_frame(raw), habit, w, h)
    mem.record(KEY, truth, w, h, det=det_record(fc), strip=fc.strip, fmt=fc.fmt)
    return fc


@pytest.fixture
def mem(tmp_path):
    return CropMemory(str(tmp_path / "m.json"))


# --- the store -----------------------------------------------------------------

def test_store_round_trip_cap_and_forget(tmp_path):
    p = str(tmp_path / "m.json")
    m = CropMemory(p)
    assert m.counts() == (0, 0) and m.priors(KEY) == []
    for i in range(cm.MAX_PER_SETUP + 5):
        m.record(KEY, [500, 400, 900, 600, 0.3], 1080, 810)
    m.record("OTHER|1080x810|single", [500, 400, 600, 600, 0.0], 1080, 810)
    again = CropMemory(p)                                  # reread from disk
    assert again.counts() == (cm.MAX_PER_SETUP + 1, 2)
    ex = again.examples(KEY)[-1]
    assert ex["final"] == [500, 400, 900, 600, 0.3] and "file" not in ex
    again.forget()
    assert CropMemory(p).counts() == (0, 0)


def test_corrupt_or_old_file_starts_fresh(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"version": 0, "setups": {"x": [1]}}')
    assert CropMemory(str(p)).counts() == (0, 0)
    p.write_text("not json")
    assert CropMemory(str(p)).counts() == (0, 0)


def test_setup_key():
    class Img:
        info = {"camera_make": "FUJIFILM", "camera_model": "GFX50S II"}
        is_merged = False
    assert cm.setup_key(Img(), 1080, 809) == "FUJIFILM GFX50S II|1080x809|single"
    Img.is_merged, Img.info = True, None
    assert cm.setup_key(Img(), 1080, 720) == "unknown camera|1080x720|merged"


def test_inward_shifts_in_the_box_frame():
    det = [500.0, 400.0, 800.0, 600.0, 20.0]
    t = np.deg2rad(20.0)
    # pull the left edge in by 40 px along the (rotated) box x axis
    final = [500 + 20 * np.cos(t), 400 + 20 * np.sin(t), 760.0, 600.0, 20.0]
    sh, da = inward_shifts(det, final)
    assert sh == pytest.approx((0.05, 0.0, 0.0, 0.0), abs=1e-9) and da == 0.0


def test_priors_cluster_by_frame_size(mem):
    for cx in (500, 505, 498):
        mem.record(KEY, [cx, 400, 900, 600, 0.3], 1080, 810)
    mem.record(KEY, [520, 405, 700, 700, 0.3], 1080, 810)    # another format
    pri = mem.priors(KEY)
    assert [p.n for p in pri] == [3, 1]
    assert pri[0].size == pytest.approx((900, 600)) and pri[0].fallback
    assert pri[0].spread[0] == pytest.approx(0.01 * 1080)  # floored spread


# --- detection with priors ---------------------------------------------------------

def test_empty_priors_change_nothing():
    raw, _ = tac.scan_645(0.4)
    a, b = ac.detect_frame(raw), ac.detect_frame(raw, priors=[])
    assert a.rect == b.rect and a.angle == b.angle and a.confidence == b.confidence
    assert not b.learned


def test_your_habit_is_applied_after_two_crops(mem):
    habit = (0.03, 0.0, 0.01, 0.0)          # always pull the left edge in 3%, top 1%
    for tilt in (0.0, 0.3):
        _teach(mem, tac.scan_645(tilt)[0], habit)
    p = mem.priors(KEY)[0]
    assert p.n == 2 and p.habit[0] == pytest.approx(0.03, abs=0.002)
    raw, _ = tac.scan_645(0.6)
    h, w = raw.shape[:2]
    truth = _truth(ac.detect_frame(raw), habit, w, h)
    before = ac.detect_frame(raw)
    after = ac.detect_frame(raw, priors=mem.priors(KEY))
    assert _err(truth, before, w, h) > 0.025
    assert _err(truth, after, w, h) < 0.005
    assert after.learned == "matched" and "matched your frame" in after.summary()


def test_another_format_on_the_same_setup_is_not_pulled(mem):
    for tilt in (0.0, 0.3):
        _teach(mem, tac.scan_645(tilt)[0], (0.03, 0.0, 0.0, 0.0))
    raw, _ = tac.scan_leader()               # a 3:2 frame in a holder
    a, b = ac.detect_frame(raw), ac.detect_frame(raw, priors=mem.priors(KEY))
    assert b.rect == a.rect and not b.learned


def test_strip_and_holder_frames_do_not_share_a_habit():
    p = ac.Prior(gate=(960, 640), habit=(0.05, 0, 0, 0), strip=True, out=(940, 620))
    raw, _ = tac.scan_leader()
    a, b = ac.detect_frame(raw), ac.detect_frame(raw, priors=[p])
    assert b.rect == a.rect and not b.learned


def test_soft_side_snaps_inward_only():
    box = (100.0, 50.0, 1000.0, 700.0)      # 900 x 650
    assert ac._snap_soft(box, {"R"}, (880.0, None)) == (100.0, 50.0, 980.0, 700.0)
    assert ac._snap_soft(box, {"R"}, (920.0, None)) == box          # never outward
    assert ac._snap_soft(box, {"L", "R"}, (880.0, None)) == box     # both unseen
    assert ac._snap_soft(box, {"T"}, (None, 640.0)) == (100.0, 60.0, 1000.0, 700.0)
    assert ac._snap_soft(box, {"R"}, (700.0, None)) == box          # too far: not this frame


def test_usual_frame_when_nothing_is_found():
    v = tac._picture(tac.H, tac.W, seed=11)                 # picture fills the scan
    raw = tac._to_raw(v)
    assert not ac.detect_frame(raw).usable
    p = ac.Prior(gate=(900, 620), centre=(540, 405), spread=(5, 5), size=(880, 600),
                 box_centre=(540, 405), angle=0.4, fallback=True, n=3, fmt="35mm 3:2")
    fc = ac.detect_frame(raw, priors=[p])
    assert fc.usable and fc.learned == "usual" and fc.confidence == "medium"
    x1, y1, x2, y2 = fc.rect
    assert (x2 - x1) * tac.W == pytest.approx(880) and fc.angle == pytest.approx(0.4)
    p.fallback = False
    assert not ac.detect_frame(raw, priors=[p]).usable


# --- what counts as a correction (backend) --------------------------------------------

@pytest.fixture
def backend(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(cm, "_memory", CropMemory(str(tmp_path / "m.json")))
    saved = ccr_backend.auto_crop_learn
    ccr_backend.auto_crop_learn = True
    yield ccr_backend
    ccr_backend.auto_crop_learn = saved


class _Img:
    info = {"camera_make": "TEST", "camera_model": "CAM"}
    is_merged = False
    file_path = "x.raf"

    def __init__(self):
        self.crop_rect, self.crop_angle = None, 0.0


def test_confirmed_crop_is_learned_once_then_refined(backend):
    raw, _ = tac.scan_645(0.4)
    img = _Img()
    fc = backend.detect_frame_crop(img, raw=raw)
    assert img.auto_crop_last is fc and img.auto_crop_key.endswith("|1080x810|single")
    img.crop_rect, img.crop_angle = fc.rect, fc.angle
    assert backend.learn_confirmed_crop(img)
    first = img.auto_crop_example
    x1, y1, x2, y2 = fc.rect
    img.crop_rect = (x1 + 0.01, y1, x2, y2)                 # a small refinement
    assert backend.learn_confirmed_crop(img)
    assert img.auto_crop_example == first and cm.memory().counts() == (1, 1)
    img.crop_rect = (x1 + 0.2, y1 + 0.1, x2 - 0.1, y2)      # a creative re-crop
    assert not backend.learn_confirmed_crop(img)
    assert cm.memory().examples(img.auto_crop_key)[0]["final"][2] == pytest.approx(
        (x2 - x1 - 0.01) * 1080, abs=0.01)


def test_not_learned_without_auto_crop_small_or_switched_off(backend):
    img = _Img()
    img.crop_rect = (0.1, 0.1, 0.9, 0.9)
    assert not backend.learn_confirmed_crop(img)            # auto crop never saw it
    raw, _ = tac.scan_645(0.0)
    backend.detect_frame_crop(img, raw=raw)
    img.crop_rect = (0.4, 0.4, 0.6, 0.6)                    # 4% of the scan
    assert not backend.learn_confirmed_crop(img)
    img.crop_rect = (0.1, 0.1, 0.9, 0.9)
    backend.auto_crop_learn = False
    assert not backend.learn_confirmed_crop(img)
    assert cm.memory().counts() == (0, 0)


def test_learned_frames_reach_the_next_detection(backend):
    habit = (0.03, 0.0, 0.0, 0.0)
    for tilt in (0.0, 0.3):
        raw, _ = tac.scan_645(tilt)
        img = _Img()
        backend.detect_frame_crop(img, raw=raw)
        h, w = raw.shape[:2]
        cx, cy, bw, bh, a = _truth(ac.detect_frame(raw), habit, w, h)
        img.crop_rect = ((cx - bw / 2) / w, (cy - bh / 2) / h, (cx + bw / 2) / w, (cy + bh / 2) / h)
        img.crop_angle = a
        assert backend.learn_confirmed_crop(img)
    nxt = backend.detect_frame_crop(_Img(), raw=tac.scan_645(0.6)[0])
    assert nxt.learned == "matched"


def test_roll_check_refinds_an_odd_frame(backend):
    raws = [tac.scan_strip()[0] for _ in range(4)]
    imgs = [_Img() for _ in raws]
    frames = [backend.detect_frame_crop(i, raw=r) for i, r in zip(imgs, raws)]
    assert all(f.usable for f in frames)
    frames[2] = ac.FrameCrop(None, reason="pretend it was missed")
    out, n = backend._roll_consistent_crops(imgs, frames, raws)
    assert n == 1 and out[2].usable and out[2].learned == "roll"
    assert out[0] is frames[0]
    # too few frames to call it a roll
    out, n = backend._roll_consistent_crops(imgs[:2], frames[:2], raws[:2])
    assert n == 0


def test_roll_snaps_a_soft_side_inward():
    g = dict(gate=(960.0, 640.0), out=(100.0, 80.0, 1000.0, 700.0), size=(1080, 810),
             angle=0.0, out_rect=(100 / 1080, 80 / 810, 1000 / 1080, 700 / 810))
    fc = ac.FrameCrop(g["out_rect"], confidence="medium", soft=("R",), geom=g)
    snapped = ac.snap_soft_sides(fc, (880.0, None))
    assert snapped.learned == "roll"
    assert snapped.rect[2] * 1080 == pytest.approx(980.0)
    assert ac.snap_soft_sides(fc, (920.0, None)) is fc      # never outward


# --- Re-crop all, the Settings group, Done in the preview ---------------------------

class _CImg(_Img):
    """A frame Auto Crop All can work on: unconverted, its raw in resized_raw."""
    converted = False

    def __init__(self, raw):
        super().__init__()
        self.resized_raw = raw
        self.fine_rotation_angle = 0
        self.undo = 0

    def push_undo_state(self):
        self.undo += 1

    def update_thumbnail_and_preview(self):
        pass


def test_auto_crop_all_leaves_your_crops_alone(backend):
    raws = [tac.scan_645(t)[0] for t in (0.0, 0.2, 0.4, 0.6)]
    imgs = [_CImg(r) for r in raws]
    imgs[1].crop_rect = (0.2, 0.2, 0.7, 0.7)                 # drawn by hand
    imgs[2].auto_crop_example = "abc"                        # confirmed with Done
    imgs[2].crop_rect = (0.1, 0.1, 0.9, 0.9)
    saved = backend.images
    backend.images = imgs
    try:
        assert backend.auto_crop_candidates() == [imgs[0], imgs[3]]
        summ = backend.auto_crop_all()
        assert summ["cropped"] == 2 and imgs[0].crop_rect is not None
        assert imgs[1].crop_rect == (0.2, 0.2, 0.7, 0.7)
        assert imgs[2].crop_rect == (0.1, 0.1, 0.9, 0.9)
        assert imgs[0].auto_crop_rect == imgs[0].crop_rect      # set by auto crop...
        assert backend.auto_crop_candidates() == [imgs[0], imgs[3]]  # ...so still re-croppable
        undo = imgs[0].undo
        backend.auto_crop_all()
        assert imgs[0].undo == undo                              # unchanged: no undo step
        assert backend.auto_crop_all(should_cancel=lambda: True) is None
    finally:
        backend.images = saved


def test_settings_group_and_forget(backend, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
    QApplication.instance() or QApplication([])
    from widgets.settings_dialog import SettingsDialog
    cm.memory().record(KEY, [500, 400, 900, 600, 0.0], 1080, 810)

    class MW(QWidget):
        toggled = []

        def on_auto_crop_learn_toggled(self, on):
            self.toggled.append(on)
    mw = MW()
    dlg = SettingsDialog(mw)
    _KEEP.append((mw, dlg))
    assert dlg._cb_crop_learn.isChecked()
    assert "1 confirmed crop learned on 1 setup" in dlg._crop_memory_label.text()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dlg._on_forget_crops()
    assert cm.memory().counts() == (0, 0) and not dlg._btn_forget_crops.isEnabled()
    dlg._cb_crop_learn.setChecked(False)
    dlg._apply_pending()
    assert MW.toggled == [False]


_KEEP = []


def test_done_in_the_crop_panel_learns(backend, monkeypatch):
    import test_crop_panel as tcp
    ip, _host = tcp._make_preview()
    ip.crop_mode = False
    img = tcp._Img()
    seen = []
    monkeypatch.setattr(backend, "learn_confirmed_crop", lambda im: seen.append(im) or True)
    backend.images = [img]
    ip.update_preview = lambda *a, **k: None
    assert ip.enter_crop_mode()
    from PySide6.QtCore import QRectF
    ip._pending_crop_local = QRectF(20, 20, 300, 200)
    hints = []
    ip.parent().parent().sliders_panel.set_temporary_hint = lambda h, duration=0: hints.append(h)
    ip.confirm_crop()
    assert seen == [img] and img.crop_rect is not None
    assert hints and "remember this frame" in hints[-1]
