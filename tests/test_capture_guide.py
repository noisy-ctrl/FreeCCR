#!/usr/bin/env python3
"""Capture's frame guide goes with the shot (spec/guide-follow-and-crop.md
§3): the registry that ties a shot's files to the guide, and lining the live
picture up with the RAW."""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core import capture_guide as cg  # noqa: E402
from test_guide_tracker import GUIDE, scene  # noqa: E402

RW, RH = 1620, 1215          # the scan preview (4:3, like the live view)


@pytest.fixture(autouse=True)
def _clean():
    cg.clear()
    yield
    cg.clear()


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
def test_a_shot_ties_its_files_to_its_record(tmp_path):
    a, b, c = (str(tmp_path / n) for n in ("a.raf", "b.raf", "c.raf"))
    rec = cg.make_record(GUIDE, scene(), following=True, now=0)
    assert rec["live"].shape == (360, 480) and rec["live_size"] == (1024, 768)
    assert rec["guide"]["angle"] == GUIDE["angle"] and rec["format"] == "6x7"
    assert not cg.remember(a, now=0)                  # no shot running: nothing
    cg.begin_shot(rec, now=0)
    assert cg.active(now=500) is rec                  # while it runs, however long
    assert cg.remember(a, now=1)
    cg.end_shot(now=10)
    assert cg.stamp(b, now=10 + cg.GRACE_S - 1)       # late file, within the grace
    assert not cg.stamp(c, now=10 + cg.GRACE_S + 1)   # too late
    assert cg.lookup([c, b]) is rec and cg.lookup([a]) is rec and cg.lookup([c]) is None


def test_stamp_never_replaces_an_exact_tie(tmp_path):
    p = str(tmp_path / "x.raf")
    first = cg.make_record(GUIDE, scene(), now=0)
    second = cg.make_record(dict(GUIDE, cx=0.3), scene(), now=1)
    cg.remember(p, record=first)
    cg.begin_shot(second, now=1)
    assert not cg.stamp(p, now=2)
    assert cg.lookup([p]) is first


def test_the_registry_is_bounded(tmp_path):
    rec = cg.make_record(GUIDE, scene(), now=0)
    for i in range(cg.MAX_PATHS + 20):
        cg.remember(str(tmp_path / f"{i}.raf"), record=rec)
    assert cg.lookup([str(tmp_path / "0.raf")]) is None
    assert cg.lookup([str(tmp_path / f"{cg.MAX_PATHS + 19}.raf")]) is rec


# --------------------------------------------------------------------------- #
# lining up
# --------------------------------------------------------------------------- #
# the RAW sees 1.5 % less than the live view, a few px off centre
FIELD, OFF = 1.015, (6.0, -4.0)


def _to_raw(x, y, lw=1024, lh=768):
    a = FIELD * RW / float(lw)
    return (RW / 2 + a * (x - lw / 2) + OFF[0], RH / 2 + a * (y - lh / 2) + OFF[1])


def _raw_scan(live_bgr, gamma=1.8, noise=300.0, seed=3):
    lh, lw = live_bgr.shape[:2]
    a = FIELD * RW / float(lw)
    m = np.float32([[a, 0, RW / 2 - a * lw / 2 + OFF[0]], [0, a, RH / 2 - a * lh / 2 + OFF[1]]])
    up = cv2.warpAffine(live_bgr.astype(np.float32), m, (RW, RH), flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
    lin = np.power(up / 255.0, gamma) * 60000.0
    lin += np.random.default_rng(seed).normal(0, noise, lin.shape)
    return np.clip(lin, 0, 65535).astype(np.uint16)


def _want(turn=0):
    pts = [_to_raw(x, y) for x, y in cg.corners(GUIDE, 1024, 768)]
    w, h = RW, RH
    if turn:
        pts = [cg._turn_point(x, y, RW, RH, 1) for x, y in pts]
        w, h = RH, RW
    cx, cy, bw, bh, ang = cg.box_from_corners(pts)
    return {"cx": cx / w, "cy": cy / h, "w": bw / w, "h": bh / h, "angle": ang}


def _close(got, want, tol=0.003):
    assert got is not None
    for k in ("cx", "cy", "w", "h"):
        assert abs(got[k] - want[k]) < tol, (k, got[k], want[k])
    assert abs(got["angle"] - want["angle"]) < 0.25


def test_the_guide_lands_on_the_scan():
    live = scene()
    rec = cg.make_record(GUIDE, live)
    got = cg.map_guide(rec, _raw_scan(live))
    assert got["how"] == "matched" and got["turn"] == 0 and got["score"] >= cg.ECC_MIN
    _close(got, _want())


def test_a_scan_stored_turned_is_found():
    live = scene()
    rec = cg.make_record(GUIDE, live)
    raw = np.ascontiguousarray(np.rot90(_raw_scan(live), 1))
    got = cg.map_guide(rec, raw)
    assert got is not None and got["turn"] == 90
    _close(got, _want(turn=1))


def test_another_picture_gives_no_guide():
    rec = cg.make_record(GUIDE, scene())
    rng = np.random.default_rng(9)
    other = cv2.GaussianBlur(rng.uniform(0, 60000, (RH, RW)).astype(np.float32), (0, 0), 6)
    assert cg.map_guide(rec, np.dstack([other] * 3).astype(np.uint16)) is None


def test_corners_and_box_round_trip():
    pts = cg.corners(dict(GUIDE, angle=-3.0), 1000, 800)
    cx, cy, w, h, ang = cg.box_from_corners(pts)
    assert abs(cx - GUIDE["cx"] * 1000) < 1e-6 and abs(cy - GUIDE["cy"] * 800) < 1e-6
    assert abs(w - GUIDE["w"] * 1000) < 1e-6 and abs(h - GUIDE["h"] * 800) < 1e-6
    assert abs(ang + 3.0) < 1e-9
    # a box turned a quarter is the same box with its sides swapped
    turned = [cg._turn_point(x, y, 1000, 800, 1) for x, y in pts]
    _cx, _cy, w2, h2, a2 = cg.box_from_corners(turned)
    assert abs(w2 - h) < 1e-6 and abs(h2 - w) < 1e-6 and abs(a2 + 3.0) < 1e-6
    assert math.isclose(cg._turn_point(*cg._turn_point(10, 20, 100, 50, 1), 50, 100, 3)[0], 10)
