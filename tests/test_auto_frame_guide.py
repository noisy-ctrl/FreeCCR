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


def _live(rect=(60, 40, 980, 740), angle=0.0, gap=None, rebate=3, seed=0, patch=None,
          stripe=None, noise=0.0):
    """A live picture: the carrier's black mask, the frame (rect, turned by
    `angle` degrees about its centre) with a rebate strip of clear film
    round it (`rebate` px, or a dict side -> px); `gap` = (y0, y1) rows of
    clear film across it (between frames); `patch` = (x0, y0, x1, y1) of the
    picture in the clear film's exact colour (deep shadows: a tree trunk in
    the user's live view reads B 64 G 139 R 230, the rebate 65 / 139 / 230);
    `stripe` = (x0, x1) a column of it, lightly textured, top to bottom;
    `noise`: the camera's noise (sd, 8-bit)."""
    img = np.zeros((H, W, 3), np.uint8)
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pic = _picture(W, H, seed)
    if gap is not None:
        pic[gap[0]:gap[1], :] = BASE
    if patch is not None:
        pic[patch[1]:patch[3], patch[0]:patch[2]] = BASE
    if stripe is not None:
        rng = np.random.default_rng(seed + 7)
        tex = rng.normal(0, 12, (H, stripe[1] - stripe[0], 1))
        pic[:, stripe[0]:stripe[1]] = np.clip(np.array(BASE) + tex, 0, 255).astype(np.uint8)
    rb = rebate if isinstance(rebate, dict) else {k: rebate for k in ("top", "bottom", "left", "right")}

    def poly(dx0, dy0, dx1, dy1):
        a = math.radians(angle)
        pts = []
        for px, py in ((dx0, dy0), (dx1, dy0), (dx1, dy1), (dx0, dy1)):
            px, py = px - cx, py - cy
            pts.append((cx + px * math.cos(a) - py * math.sin(a),
                        cy + px * math.sin(a) + py * math.cos(a)))
        return np.round(np.array(pts)).astype(np.int32)
    film = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(film, poly(x0 - rb["left"], y0 - rb["top"], x1 + rb["right"],
                                  y1 + rb["bottom"]), 255)
    inner = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(inner, poly(x0, y0, x1, y1), 255)
    img[film > 0] = BASE
    img[inner > 0] = pic[inner > 0]
    if noise:
        rng = np.random.default_rng(seed + 99)
        img = np.clip(img + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
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
    # breathing: 1.5 % bigger; the guide is the median of the last finds, so
    # it moves once most of them say so
    b = _find((53, 35, 987, 745))
    assert not fol.update(b, 0.5)                   # [a, a, b]: still a
    assert fol.update(b, 0.55)                      # [a, a, b, b]: half way
    assert g0["w"] < fol.guide["w"] < b.guide["w"]
    fol.update(b, 0.6)
    assert fol.guide["w"] == pytest.approx(b.guide["w"], abs=1e-9)
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
    # after waiting, even one where the old one was takes two finds (winding
    # on can pass a part frame that happens to sit there)
    fol2 = lfr.GuideFollower()
    fol2.update(a, 0.0)
    fol2.update(a, 0.25)
    fol2.update(gap, 0.5)
    assert fol2.state == "waiting"
    back = _find((62, 42, 982, 742))
    fol2.update(back, 0.75)
    assert fol2.state == "waiting"
    fol2.update(back, 1.0)
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


def test_capture_follows_the_frame_waits_and_hands_over_to_the_hand(film_mon):
    mon, usb, light, lm, path = film_mon
    assert mon._guide_auto                       # on by default
    mon.actions["guide"].setChecked(True)
    mon.feed.full = _live()
    mon._auto_guide_tick()
    mon._auto_guide_tick()
    t = _truth((60, 40, 980, 740))
    assert mon.canvas.guide["w"] == pytest.approx(t["w"], abs=0.015)
    assert mon.canvas.guide["cx"] == pytest.approx(t["cx"], abs=0.005)
    assert mon.canvas.guide_waiting is None
    # winding on: the guide waits, dashed with its note, and the estimate stops
    mon.feed.full = _live(gap=(330, 430))
    mon._auto_guide_tick()
    assert mon.canvas.guide_waiting
    assert mon._remembered_base is not None
    mon.film_base_box.setVisible(True)
    mon._update_frame_estimate()
    assert mon._frame_est == [] and "whole frame" in mon._frame_live_reason
    # the frame back: following, and a new frame clears an armed exposure
    mon._frame_exposures = {"w": {"shutter": "1/2", "iso": "100", "levels": None}}
    mon.feed.full = _live()
    mon._auto_guide_tick()
    mon._auto_guide_tick()
    assert mon.canvas.guide_waiting is None and mon._frame_exposures is None
    # the menu offers it; a drag turns it off
    from PySide6.QtCore import QPoint
    mon._guide_menu(QPoint(10, 10))
    first = mon._popup.actions()[0]
    assert first.text() == "Follow the Frame" and first.isChecked()
    mon._popup.close()
    mon.canvas.guide_changed.emit(dict(mon.canvas.guide))
    assert not mon._guide_auto
    assert "no longer follows the frame" in mon.capture_caption.text()
    before = dict(mon.canvas.guide)
    mon.feed.full = _live((100, 80, 900, 700))
    mon._auto_guide_tick()
    assert mon.canvas.guide == before            # off: left where the hand put it
    mon.set_guide_auto(True)
    assert lm._settings().value("live/guide_auto") in (True, "true")
