#!/usr/bin/env python3
"""The frame guide finds the frame by itself (spec/auto-frame-guide.md):
core/live_frame.find_frame and GuideFollower, the user's two recordings when
they are on this machine, and Capture's wiring."""

import glob
import math
import os
import sys

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import frame_meter as fm  # noqa: E402
from core import live_frame as lfr  # noqa: E402

BASE = (67, 140, 231)                    # B, G, R of clear film in the user's live view
W, H = 1024, 768


def _picture(w, h, seed=0):
    """A textured negative: orange-brown, darker and lighter areas."""
    rng = np.random.default_rng(seed)
    n = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 6)
    n = (n - n.min()) / (n.max() - n.min())
    fine = rng.normal(0, 0.04, (h, w)).astype(np.float32)
    t = np.clip(n + fine, 0, 1)
    b = 15 + 40 * t
    g = 40 + 70 * t
    r = 110 + 90 * t
    return np.dstack([b, g, r]).astype(np.uint8)


def _live(rect=(60, 40, 980, 740), angle=0.0, gap=None, rebate=3, seed=0):
    """A live picture: the carrier's black mask, the frame (rect, turned by
    `angle` degrees about its centre) with a thin rebate strip of clear film
    round it; `gap` = (y0, y1) rows of clear film across it (between frames)."""
    img = np.zeros((H, W, 3), np.uint8)
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pic = _picture(W, H, seed)
    if gap is not None:
        pic[gap[0]:gap[1], :] = BASE

    def poly(dx0, dy0, dx1, dy1):
        a = math.radians(angle)
        pts = []
        for px, py in ((dx0, dy0), (dx1, dy0), (dx1, dy1), (dx0, dy1)):
            px, py = px - cx, py - cy
            pts.append((cx + px * math.cos(a) - py * math.sin(a),
                        cy + px * math.sin(a) + py * math.cos(a)))
        return np.round(np.array(pts)).astype(np.int32)
    film = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(film, poly(x0 - rebate, y0 - rebate, x1 + rebate, y1 + rebate), 255)
    inner = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(inner, poly(x0, y0, x1, y1), 255)
    img[film > 0] = BASE
    img[inner > 0] = pic[inner > 0]
    return img


def _truth(rect):
    x0, y0, x1, y1 = rect
    return {"cx": (x0 + x1) / 2 / W, "cy": (y0 + y1) / 2 / H,
            "w": (x1 - x0) / W, "h": (y1 - y0) / H}


# --------------------------------------------------------------------------- #
# finding
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("angle", [0.0, 0.8, -1.5])
def test_a_frame_in_the_carrier_is_found_with_its_turn(angle):
    rect = (60, 40, 980, 740)
    f = lfr.find_frame(_live(rect, angle))
    assert f.complete and not f.cut and not f.between, f.reason
    t = _truth(rect)
    px = 1024 / lfr.WORK_SIDE                     # one working pixel in live pixels
    # a rebate strip thinner than a few working pixels can't be told from
    # the picture: the guide may land on its outer edge (3 px here, ≤ 0.5 %)
    for k, scale, tol in (("cx", W, 1.5 * px), ("cy", H, 1.5 * px),
                          ("w", W, 2 * (3 + 1.5 * px)), ("h", H, 2 * (3 + 1.5 * px))):
        assert abs(f.guide[k] - t[k]) * scale <= tol, (k, f.guide[k], t[k])
    assert f.guide["angle"] == pytest.approx(angle, abs=0.3)


def test_clear_film_across_the_frame_is_between_frames_and_its_colour_kept():
    f = lfr.find_frame(_live(gap=(330, 430)))
    # the largest piece of picture is found, and it is not a whole frame:
    # either a band crosses it, or it is a part (the follower then waits)
    assert f.base8 is not None
    assert np.allclose(f.base8, BASE, atol=3)
    whole = lfr.find_frame(_live())
    assert f.between or f.guide["h"] < 0.8 * whole.guide["h"]


def test_a_frame_bigger_than_the_view_is_cut_not_whole():
    f = lfr.find_frame(_live(rect=(60, 40, 1030, 740), rebate=0))
    assert f.found and f.cut == ("right",) and not f.complete
    assert "runs off the picture (right)" in f.reason


def test_nothing_to_find():
    dark = lfr.find_frame(np.zeros((H, W, 3), np.uint8))
    assert not dark.found and "light on" in dark.reason
    assert lfr.find_frame(None).reason == "No live picture."


# --------------------------------------------------------------------------- #
# following
# --------------------------------------------------------------------------- #
def _find(rect=(60, 40, 980, 740), **kw):
    return lfr.find_frame(_live(rect, **kw))


def test_the_follower_takes_a_frame_eases_breathing_and_waits_while_winding_on():
    fol = lfr.GuideFollower()
    a = _find()
    assert not fol.update(a, 0.0) and fol.state == "none"      # one find: not yet
    assert fol.update(a, 0.25) and fol.state == "following"
    g0 = dict(fol.guide)
    # breathing: 1.5 % bigger, eased half way each find
    b = _find((53, 35, 987, 745))
    assert fol.update(b, 0.5)
    assert g0["w"] < fol.guide["w"] < b.guide["w"]
    # winding on: part of a frame (the gap just left), then the gap across
    part = _find((60, 40, 980, 500))
    assert not fol.update(part, 0.75) and fol.state == "waiting"
    assert "No whole frame" in fol.reason
    gap = lfr.find_frame(_live(gap=(330, 430)))
    fol.update(gap, 1.0)
    assert fol.state == "waiting"
    held = dict(fol.guide)
    # the next frame arrives further along: two agreeing finds, then followed
    nxt = _find((95, 40, 1015, 740))
    fol.update(nxt, 1.25)
    assert fol.state == "waiting" and fol.guide == held
    fol.update(nxt, 1.5)
    assert fol.state == "following"
    assert fol.guide["cx"] == pytest.approx(nxt.guide["cx"], abs=1e-6)
    # one where the old one was is followed at once
    fol2 = lfr.GuideFollower()
    fol2.update(a, 0.0)
    fol2.update(a, 0.25)
    fol2.update(gap, 0.5)
    assert fol2.state == "waiting"
    fol2.update(_find((62, 42, 982, 742)), 0.75)
    assert fol2.state == "following"


def test_a_part_frame_that_stays_becomes_the_frame():
    fol = lfr.GuideFollower()
    whole = _find()
    fol.update(whole, 0.0)
    fol.update(whole, 0.25)
    small = _find((200, 150, 840, 630))          # a smaller frame (another format)
    t = 0.5
    while t < 3.0:
        fol.update(small, t)
        t += 0.25
    assert fol.state == "following"
    assert fol.guide["w"] == pytest.approx(small.guide["w"], abs=0.01)


def test_the_film_format_shape_is_checked_and_a_cut_frame_is_followed():
    fol = lfr.GuideFollower()
    f = _find()                                   # 920 x 700: ratio 1.314
    fol.update(f, 0.0, ratio=1.5, frame_size=(W, H))
    fol.update(f, 0.25, ratio=1.5, frame_size=(W, H))
    assert fol.state == "none" and "film format" in fol.reason
    fol.update(f, 0.5, ratio=1.31, frame_size=(W, H))
    fol.update(f, 0.75, ratio=1.31, frame_size=(W, H))
    assert fol.state == "following"
    cut = lfr.find_frame(_live(rect=(60, 40, 1030, 740), rebate=0))
    fol.update(cut, 1.0)
    fol.update(cut, 1.25)
    assert fol.state == "following" and "runs off" in fol.reason


# --------------------------------------------------------------------------- #
# the user's recordings, as extracted frames (on the machine they were made on)
# --------------------------------------------------------------------------- #
REC = os.environ.get("FREECCR_AFG_FRAMES", "")


@pytest.mark.skipif(not REC or not os.path.isdir(os.path.join(REC, "r1")),
                    reason="the recordings' frames aren't here (FREECCR_AFG_FRAMES)")
def test_the_users_recordings():
    def states(d):
        fol = lfr.GuideFollower()
        out, guides = [], []
        for i, f in enumerate(sorted(glob.glob(os.path.join(REC, d, "f_*.png")))):
            fol.update(lfr.find_frame(cv2.imread(f)), i * 0.25)
            out.append({"following": "F", "waiting": "w", "none": "-"}[fol.state])
            guides.append(fol.guide)
        return "".join(out), guides
    r1, _g = states("r1")
    assert "F" * 15 in r1[15:35]                   # the frame before winding on
    assert set(r1[36:47]) == {"w"}                 # winding on: waiting throughout
    assert "F" * 15 in r1[52:]                     # the new frame followed
    r2, g2 = states("r2")
    assert set(r2[2:]) == {"F"}                    # focusing: followed throughout
    ws = [g["w"] for g in g2 if g]
    assert max(ws) - min(ws) > 0.02                # it did follow the breathing


# --------------------------------------------------------------------------- #
# the estimate's remembered base
# --------------------------------------------------------------------------- #
def test_the_estimate_uses_a_remembered_base_when_none_shows():
    img = _live()                                 # mask all round: no base beside
    guide = dict(lfr.find_frame(img).guide)
    est = fm.live_estimate(img, guide)
    assert not est.ok
    est = fm.live_estimate(img, guide, remembered=np.array(BASE, float))
    assert est.ok and est.source == "remembered" and est.stops > 0
    dark = fm.live_estimate(img, guide, remembered=np.array((10, 20, 40), float))
    assert not dark.ok and "seen earlier is darker" in dark.reason


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
from test_film_base_shoot import film_mon  # noqa: E402,F401


def _zoomed(img, s, dx=0.0, dy=0.0):
    """Focusing: the whole live picture scaled `s` about its centre and
    shifted (dx, dy) of the view."""
    m = np.float32([[s, 0, (1 - s) * W / 2 + dx * W], [0, s, (1 - s) * H / 2 + dy * H]])
    return cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))


def test_capture_keeps_your_guide_on_the_frame(film_mon):
    """spec/guide-follow-and-crop.md §2 (replaces revision 1's finder): you
    place the guide; Follow the Frame keeps it on the frame through focusing,
    and winding on (the carrier still) doesn't move it."""
    from PySide6.QtCore import QPoint
    from core import guide_tracker
    mon, usb, light, lm, path = film_mon
    assert mon._guide_auto                       # on by default
    mon.actions["guide"].setChecked(True)
    mon.feed.full = _live()
    placed = {"cx": 0.5, "cy": 0.51, "w": 0.86, "h": 0.88, "angle": 0.0, "format": None}
    mon.canvas.set_guide(placed)
    mon.canvas.guide_changed.emit(dict(mon.canvas.guide))      # a drag: anchors, stays on
    assert mon._guide_auto and mon._guide_placed and mon._tracker.anchored
    assert mon.canvas.guide["cx"] == placed["cx"]               # not moved by finding
    # focusing: everything 2 % bigger and a little off; the guide goes with it
    mon.feed.full = _zoomed(_live(), 1.02, 0.004, -0.003)
    mon._auto_guide_tick()
    want = guide_tracker.apply((1.02, 0.004, -0.003), placed)
    for k in ("cx", "cy", "w", "h"):
        assert mon.canvas.guide[k] == pytest.approx(want[k], abs=0.0015), k
    # winding on: clear film across the frame, the carrier still: no move
    before = dict(mon.canvas.guide)
    mon.feed.full = _zoomed(_live(gap=(330, 430), seed=3), 1.02, 0.004, -0.003)
    mon._last_base_find = 0.0                     # the film base is read once a second
    for _ in range(3):
        mon._auto_guide_tick()
    assert mon.canvas.guide == before and mon.canvas.guide_waiting is None
    assert mon._remembered_base is not None       # the film base is still remembered
    mon.film_base_box.setVisible(True)
    mon._update_frame_estimate()
    assert "whole frame" not in (mon._frame_live_reason or "")   # no "between frames" pause
    # the menu says what it does
    mon._guide_menu(QPoint(10, 10))
    first = mon._popup.actions()[0]
    assert first.text() == "Follow the Frame" and first.isChecked()
    assert "where you put it" in first.toolTip()
    mon._popup.close()
    # off: left where the hand put it
    mon.set_guide_auto(False)
    assert lm._settings().value("live/guide_auto") in (False, "false")
    mon.feed.full = _zoomed(_live(), 1.05)
    mon._auto_guide_tick()
    assert mon.canvas.guide == before
    # on again with no carrier in view: held, dashed, saying why
    mon.set_guide_auto(True)
    assert lm._settings().value("live/guide_auto") in (True, "true")
    mon.feed.full = np.full((H, W, 3), BASE, np.uint8)
    mon._auto_guide_tick()
    mon._auto_guide_tick()
    assert mon.canvas.guide == before
    assert "no edge of the carrier" in (mon.canvas.guide_waiting or "")


def test_shoot_takes_the_guide_to_the_shots_files(film_mon):
    """spec/guide-follow-and-crop.md §3: Shoot records the placed guide and
    a USB shot's file is tied to it; with no guide placed, nothing."""
    from core import capture_guide as cg
    from test_capture_routes import _wait
    from test_frame_exposure import _with_base
    cg.clear()
    mon, usb, light, lm, path = film_mon
    _with_base(mon)
    mon.actions["guide"].setChecked(True)
    mon.feed.full = _live()
    mon._shutter_is_usb = lambda: True
    mon._ensure_hot_folder = lambda: True
    usb.dest = usb.dest or mon._usb_dest
    mon.set_guide_auto(False)
    mon._guide_placed = False
    assert mon._shot_guide_record() is None          # shown but never placed
    from widgets.live_canvas import GUIDE_DEFAULT
    mon.set_guide_auto(True)
    mon._set_guide(dict(GUIDE_DEFAULT))              # Reset Guide: the default box
    assert mon._tracker.anchored and mon._shot_guide_record() is None
    mon.set_guide_auto(False)
    placed = {"cx": 0.5, "cy": 0.5, "w": 0.9, "h": 0.9, "angle": 0.5, "format": "6x7"}
    mon.canvas.set_guide(placed)
    mon.canvas.guide_changed.emit(dict(mon.canvas.guide))
    saved = []
    usb.file_saved.connect(saved.append)
    mon.shoot()
    rec = cg.active()
    assert rec is not None and rec["guide"]["angle"] == 0.5 and rec["format"] == "6x7"
    assert rec["live_size"] == (W, H)
    assert _wait(lambda: not mon.sequence.running, 20)
    assert saved and all(cg.lookup([p]) is rec for p in saved)
    assert cg.active() is rec                          # late files: for a while yet
    mon.actions["guide"].setChecked(False)
    assert mon._shot_guide_record() is None            # the guide hidden: no record
    cg.clear()


def test_capture_snaps_the_guide_onto_a_new_frame_once_it_settles(film_mon):
    """spec/guide-follow-and-crop.md revision 2: a wound-on frame that lands
    5 px along takes the guide with it, your margins kept, once the picture
    has been still for three looks; following off leaves it."""
    from test_guide_tracker import HAND, WIND, strip
    mon, usb, light, lm, path = film_mon
    mon.actions["guide"].setChecked(True)
    mon.set_guide_auto(True)
    mon.feed.full = strip()
    mon.canvas.set_guide(dict(HAND))
    mon.canvas.guide_changed.emit(dict(mon.canvas.guide))      # placed: margins found
    assert set(mon._tracker.rel) == {"left", "right", "top", "bottom"}
    for _ in range(3):
        mon._auto_guide_tick()                                  # still: nothing to do
    assert mon.canvas.guide["cx"] == HAND["cx"]
    # winding on: the picture moves ...
    mon.feed.full = strip(offset=-WIND // 2)
    mon._auto_guide_tick()
    mon.feed.full = strip(offset=-WIND - 5)
    mon._auto_guide_tick()
    mon._auto_guide_tick()
    assert mon.canvas.guide["cx"] == HAND["cx"]                 # ... not settled yet
    mon._auto_guide_tick()
    mon._auto_guide_tick()                                      # still for three looks
    g = mon.canvas.guide
    assert (g["cx"] - HAND["cx"]) * W == pytest.approx(-5, abs=0.5)
    assert (g["cy"] - HAND["cy"]) * H == pytest.approx(0, abs=0.3)
    assert g["w"] == HAND["w"] and g["format"] == "135"
    assert "moved onto the new frame" in mon.capture_caption.text()
    # the menu offers it by hand while following
    from PySide6.QtCore import QPoint
    mon._guide_menu(QPoint(10, 10))
    snap = [a for a in mon._popup.actions() if a.text() == "Snap to the Frame"][0]
    assert snap.isEnabled()
    mon._popup.close()
    assert mon._snap_guide(by_hand=True) is False                 # on the frame already
    assert "on the frame" in mon.capture_caption.text()
    # following off: never moved
    mon.set_guide_auto(False)
    before = dict(mon.canvas.guide)
    mon.feed.full = strip(offset=-2 * WIND + 4)
    for _ in range(5):
        mon._auto_guide_tick()
    assert mon.canvas.guide == before
    mon._guide_menu(QPoint(10, 10))
    snap = [a for a in mon._popup.actions() if a.text() == "Snap to the Frame"][0]
    assert not snap.isEnabled()
    mon._popup.close()
