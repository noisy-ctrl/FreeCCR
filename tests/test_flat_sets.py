#!/usr/bin/env python3
"""Flat-field sets: a flat per light and format, chosen per frame and moved
with the carrier opening (spec/ir-dust-and-flat-sets.md §5)."""
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core import flat_field as ff  # noqa: E402

H, W = 600, 900
OPEN = {"left": 0.10, "right": 0.90, "top": 0.08, "bottom": 0.92}


def _field(h=H, w=W, cx=0.56, cy=0.47, s=1.0, dx=0.0, dy=0.0):
    """The light the optics deliver: an off-centre hotspot times fall-off,
    as seen through a camera moved by (s, dx, dy)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # the flat's own coordinates of each frame pixel
    fx = 0.5 + ((xx + 0.5) / w - 0.5 - dx) / s
    fy = 0.5 + ((yy + 0.5) / h - 0.5 - dy) / s
    hot = 1.0 + 0.7 * np.exp(-((fx - cx) ** 2 + (fy - cy) ** 2) / (2 * 0.12 ** 2))
    fall = 1.0 - 0.35 * ((fx - 0.5) ** 2 + (fy - 0.5) ** 2) / 0.5
    inside = ((fx > OPEN["left"]) & (fx < OPEN["right"])
              & (fy > OPEN["top"]) & (fy < OPEN["bottom"]))
    return (hot * fall).astype(np.float32), inside


def _shot(level=26000, **move):
    f, inside = _field(**move)
    img = np.where(inside, f * level, 300.0)
    img = cv2.GaussianBlur(img.astype(np.float32), (0, 0), 1.0)
    return np.repeat(np.clip(img, 0, 65535)[..., None], 3, 2).astype(np.uint16), inside


def _flat(light="white", fmt="645"):
    arr, _inside = _shot()
    return ff.build_tagged_profile(arr, f"{light} {fmt}", light=light, fmt=fmt)


def _evenness(arr, inside):
    """Spread (IQR / median) of the frame's level inside the opening."""
    v = arr[..., 1].astype(np.float32)[inside]
    q1, q3 = np.percentile(v, [25, 75])
    return (q3 - q1) / np.median(v)


@pytest.fixture(autouse=True)
def _clean_globals():
    ff.set_library([])
    ff.set_active_profile(None)
    yield
    ff.set_library([])
    ff.set_active_profile(None)


def test_a_flat_shot_through_the_carrier_records_its_opening_and_measures_inside():
    p = _flat()
    assert ff.profile_light(p) == "white" and ff.profile_format(p) == "645"
    op = ff.profile_opening(p)
    for k, v in OPEN.items():
        assert abs(op[k] - v) < 0.01, (k, op[k])
    # the black mask didn't push the map to its limit: the corners are no
    # brighter-corrected than the opening's own fall-off asks
    assert float(p.gain.max()) < 2.5
    arr, inside = _shot()
    flat = ff.apply_field(arr, p)
    assert _evenness(flat, inside) < 0.02 < _evenness(arr, inside)


def test_each_frame_gets_the_flat_of_its_light_and_format():
    w645, w35, rgb, ir = _flat("white", "645"), _flat("white", "35mm"), _flat("rgb", "645"), _flat("ir", "645")
    plain = ff.build_profile(_shot()[0], "old")
    ff.set_library([w645, w35, rgb, ir, plain])
    ff.set_active_profile(plain)
    frame = types.SimpleNamespace(film_format="645", merge_sources=None, is_merged=False)
    assert ff.profile_for(frame) is w645
    frame.film_format = "35mm"
    assert ff.profile_for(frame) is w35
    trip = types.SimpleNamespace(film_format="645", merge_sources=["r", "g", "b"], is_merged=True)
    assert ff.profile_for(trip) is rgb
    trip.film_format = "6x7"                       # no RGB flat for 6x7: never another
    assert ff.profile_for(trip) is plain           # format's (spec/flat-format-match.md)
    guided = types.SimpleNamespace(film_format=None, capture_guide={"format": "35mm"},
                                   merge_sources=None)
    assert ff.profile_for(guided) is w35
    irf = types.SimpleNamespace(film_format="645", scan_light="ir", merge_sources=None)
    assert ff.profile_for(irf) is ir
    # no tagged flat in the library: the active profile, as before
    ff.set_library([plain])
    assert ff.profile_for(frame) is plain
    # the library is in the grading signature
    ff.set_library([w645])
    assert "set:" in ff.active_field_signature()
    ff.set_library([])
    assert ff.active_field_signature() == "ff:" + (plain.content_id or plain.name)


@pytest.mark.parametrize("move", [dict(s=1.06, dx=0.02, dy=-0.015), dict(s=0.95, dx=-0.03, dy=0.01)])
def test_the_flat_follows_the_carrier(move):
    p = _flat()
    arr, inside = _shot(**move)
    sides = ff.opening_of(arr)
    sol = ff.track(p, sides)
    assert sol is not None
    s, dx, dy = sol
    assert abs(s - move["s"]) < 0.01 and abs(dx - move["dx"]) < 0.006 and abs(dy - move["dy"]) < 0.006
    tracked = ff.apply_field(arr, ff.tracked_profile(p, sol))
    untracked = ff.apply_field(arr, p)
    assert _evenness(tracked, inside) < 0.025
    assert _evenness(tracked, inside) < 0.6 * _evenness(untracked, inside)


def test_no_opening_or_a_wild_answer_is_untracked():
    p = _flat()
    assert ff.track(p, None) is None
    assert ff.track(p, {"left": 0.4, "right": 0.6}) is None       # scale 0.25: a mis-find
    bare = ff.build_tagged_profile(np.full((H, W, 3), 30000, np.uint16), "bare", light="white")
    assert ff.profile_opening(bare) is None and ff.track(bare, OPEN) is None


def test_the_frame_decode_uses_its_own_tracked_flat(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv[:1])
    from core.ccr_image import CCRImage
    p = _flat()
    ff.set_library([p])
    move = dict(s=1.05, dx=0.025, dy=0.01)
    arr, inside = _shot(**move)
    img = CCRImage.__new__(CCRImage)
    img.file_path = str(tmp_path / "R0001_645_XP2_001_WHITE.raf")
    img.merge_sources = None
    img.film_format = "645"
    out = img._apply_field_correction(arr)
    assert _evenness(out, inside) < 0.025
    assert img.flat_opening and abs(img.flat_opening["left"] - (0.5 + 1.05 * (0.10 - 0.5) + 0.025)) < 0.01
    # the opening is found once per scan (an export reuses it)
    img._flat_opening_memo = (img._flat_opening_memo[0], dict(OPEN))
    out2 = img._apply_field_correction(arr)
    assert _evenness(out2, inside) > _evenness(out, inside)    # the memo was used


def test_the_wizard_orders_rgb_shots_by_their_light(monkeypatch):
    from widgets import field_correction_dialog as fcd
    from core import scan_type
    shares = {"a.raf": (0.1, 0.2, 0.7), "b.raf": (0.7, 0.2, 0.1), "c.raf": (0.15, 0.7, 0.15)}
    monkeypatch.setattr(scan_type, "read_light",
                        lambda p: scan_type.LightReading(shares[os.path.basename(p)], None))
    assert fcd.order_rgb(["a.raf", "b.raf", "c.raf"]) == ["b.raf", "c.raf", "a.raf"]


def test_the_ir_flat_reaches_the_dust_search():
    from core import ir_dust
    irp = _flat("ir", "645")
    ff.set_library([irp, _flat("white", "645")])
    img = types.SimpleNamespace(film_format="645", flat_opening=dict(OPEN), merge_sources=None)
    light = ir_dust.ir_flat_for(img, (300, 450))
    assert light.shape == (300, 450)
    # the light map is the flat's own light: bright where the hotspot is
    f, _ = _field(h=300, w=450)
    inner = (slice(60, 240), slice(80, 370))
    r = light[inner] / f[inner]
    assert r.std() / r.mean() < 0.02
    img.film_format = "6x9"                      # no IR flat for it: none (another
    assert ir_dust.ir_flat_for(img, (300, 450)) is None   # format's would misfit)
    ff.set_library([_flat("white", "645")])
    assert ir_dust.ir_flat_for(img, (300, 450)) is None


def test_a_saved_flat_keeps_its_light_format_and_opening(tmp_path):
    """The file's own "format" key names the file type: the film format is
    stored as "film_format" so it survives a save (it didn't, 2026-11-19)."""
    p = _flat("rgb", "645")
    path = ff.save_profile(p, str(tmp_path / "f.ffc"))
    back = ff.load_profile(path)
    assert ff.profile_light(back) == "rgb" and ff.profile_format(back) == "645"
    assert ff.profile_opening(back) == ff.profile_opening(p)
