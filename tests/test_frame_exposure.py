#!/usr/bin/env python3
"""Each frame's own exposure (spec/frame-exposure.md): the frame guide on a
frame, the Live histogram inside it, the live estimate, a RAW read inside the
guide, the suggestion through the film base's ladder, Meter end to end, Use
for This Frame, and conversion following each frame's exposure."""

import math
import os
import sys
import types

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import base_exposure as be  # noqa: E402
from core import capture_light as cl  # noqa: E402
from core import frame_meter as fm  # noqa: E402
from core import live_feed as lf  # noqa: E402
from test_film_base_exposure import SPEEDS, ISOS  # noqa: E402

GUIDE = {"cx": 0.5, "cy": 0.55, "w": 0.6, "h": 0.6, "angle": 0.0, "format": None}


def _linear_to_8(v):
    v = np.clip(v, 0, 1)
    s = np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)
    return np.round(s * 255).astype(np.uint8)


def _negative(base8=200, frame_lin_ratio=0.5, w=800, h=600):
    """A live frame: film base everywhere, a picture inside GUIDE whose
    brightest part is `frame_lin_ratio` of the base in linear light."""
    img = np.full((h, w, 3), base8, np.uint8)
    base_lin = fm.srgb_linear(base8)
    top = int(_linear_to_8(base_lin * frame_lin_ratio))
    m = fm.guide_mask(GUIDE, w, h) > 0
    img[m] = 40                                     # the picture: denser than the base
    y0, x0 = int(0.45 * h), int(0.40 * w)           # its thinnest part, well inside
    img[y0:y0 + h // 8, x0:x0 + w // 6] = top
    return img


def _noisy(img, sigma=2.0, seed=0):
    """A live view's noise (8-bit, σ levels)."""
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, img.shape), 0, 255
                   ).astype(np.uint8)


def _band_rows(guide=GUIDE, w=800, h=600):
    """Rows of the band just above the guide, as find_base_beside reads it."""
    gh, gw = guide["h"] * h, guide["w"] * w
    top = guide["cy"] * h - gh / 2
    short = min(gw, gh)
    return int(top - fm.BESIDE_FAR * short) + 1, int(top - fm.BESIDE_NEAR * short)


# --------------------------------------------------------------------------- #
# the guide on a frame
# --------------------------------------------------------------------------- #
def test_the_guide_mask_follows_a_turned_guide():
    g = dict(GUIDE, angle=20.0)
    m = fm.guide_mask(g, 400, 300)
    assert m[int(0.55 * 300), 200] == 255                      # the centre
    assert m[5, 5] == 0
    pts = fm.guide_corners(g, 400, 300)
    assert pts.shape == (4, 2)
    # the mask agrees with the point test
    ys, xs = np.nonzero(m)
    inside = fm.inside_guide(g, (xs + 0.5) / 400, (ys + 0.5) / 300, 400, 300)
    assert inside.mean() > 0.97
    assert fm.inside_guide(g, 0.5, 0.55, 400, 300) is True


def test_the_histogram_counts_only_inside_the_guide():
    img = np.zeros((300, 400, 3), np.uint8)
    m = fm.guide_mask(GUIDE, 400, 300) > 0
    img[m] = 128
    img[~m] = 250
    h = fm.guide_histogram(img, GUIDE)
    assert h[:, 250].sum() == 0 and h[0, 128] == m.sum()


def test_the_processor_reads_the_whole_frame_inside_the_guide_and_holds_when_asked():
    img = np.full((540, 960, 3), 250, np.uint8)
    m = fm.guide_mask(GUIDE, 960, 540) > 0
    img[m] = 90
    p = lf.Processor()
    r = p.process(img, lf.ViewParams(canvas_w=960, canvas_h=540, hist_area=GUIDE))
    assert r.hist[:, 250].sum() == 0 and r.hist[1, 90] > 0
    # zoomed into a corner that shows only base: the guide's histogram is the same
    p = lf.Processor()
    r2 = p.process(img, lf.ViewParams(canvas_w=960, canvas_h=540, scale=4.0, cx=20, cy=20,
                                      hist_area=GUIDE))
    assert np.array_equal(r.hist, r2.hist)
    p = lf.Processor()
    r3 = p.process(img, lf.ViewParams(canvas_w=960, canvas_h=540, hist_area=GUIDE,
                                      hist_paused=True))
    assert r3.hist is None


# --------------------------------------------------------------------------- #
# the live estimate
# --------------------------------------------------------------------------- #
def test_a_frame_a_stop_under_the_base_reads_a_stop_longer():
    img = _negative(frame_lin_ratio=0.5)
    est = fm.live_estimate(img, GUIDE, (0.5, 0.08, 800, 600))
    assert est.ok and est.stops == pytest.approx(1.0, abs=0.06)
    img = _negative(frame_lin_ratio=0.25)
    assert fm.live_estimate(img, GUIDE, (0.5, 0.08, 800, 600)).stops == \
        pytest.approx(2.0, abs=0.08)


def test_the_live_estimate_says_why_it_has_none():
    img = _negative()
    # an old click inside the guide (another roll; a gap since wound on) is
    # ignored: the base is looked for beside the guide (2026-11-01)
    inside = fm.live_estimate(img, GUIDE, (0.5, 0.5, 800, 600))
    beside = fm.live_estimate(img, GUIDE)
    assert inside.source == beside.source and inside.ok == beside.ok
    assert inside.reason == beside.reason
    assert "clipped" in fm.live_estimate(_negative(base8=255), GUIDE,
                                         (0.5, 0.08, 800, 600)).reason
    assert "too dark" in fm.live_estimate(_negative(base8=8, frame_lin_ratio=0.9), GUIDE,
                                          (0.5, 0.08, 800, 600)).reason
    assert "frame guide on" in fm.live_estimate(img, None, (0.5, 0.08, 800, 600)).reason


# --- revision 2: the film base found beside the guide, no click ------------
def test_the_base_is_found_beside_the_guide_with_no_click():
    img = _negative(frame_lin_ratio=0.5)
    r0, r1 = _band_rows()
    img[r0:r1, 300:340] = 60                        # edge lettering in the top band
    img[:60] = 255                                  # bare light further out (sprocket holes)
    img = _noisy(img)
    est = fm.live_estimate(img, GUIDE)
    assert est.ok and est.source == "beside"
    assert est.stops == pytest.approx(1.0, abs=0.1)
    clicked = fm.live_estimate(img, GUIDE, (0.05, 0.5, 800, 600))
    assert clicked.source == "click"
    assert est.stops == pytest.approx(clicked.stops, abs=0.05)
    small = fm._reduce(img, fm.LIVE_MAX_SIDE)
    blur = cv2.blur(small, (3, 3))
    found = fm.find_base_beside(small, blur, GUIDE, fm._thinnest(small, blur, GUIDE, img))
    assert found.ok and found.sides == ("top", "right", "bottom", "left")
    assert np.all(np.abs(found.base8 - 200) <= 2)


def test_the_base_beside_a_turned_guide():
    g = dict(GUIDE, angle=12.0)
    h, w = 600, 800
    img = np.full((h, w, 3), 200, np.uint8)
    img[fm.guide_mask(g, w, h) > 0] = 40
    top = int(_linear_to_8(fm.srgb_linear(200) * 0.5))
    img[int(0.5 * h):int(0.6 * h), int(0.45 * w):int(0.55 * w)] = top
    est = fm.live_estimate(_noisy(img), g)
    assert est.ok and est.stops == pytest.approx(1.0, abs=0.1)


def test_bare_light_on_one_side_is_not_taken_for_the_base():
    """Unclipped bare light in one band (the guide laid past the film on that
    side) agrees with nothing on another side: the base, on three, wins."""
    img = _negative(frame_lin_ratio=0.5)
    x_right = int((GUIDE["cx"] + GUIDE["w"] / 2) * 800)
    img[:, x_right + 3:] = 235
    est = fm.live_estimate(_noisy(img), GUIDE)
    assert est.ok and est.stops == pytest.approx(1.0, abs=0.1)


def test_no_base_beside_the_guide_says_why():
    # holder all round (darker than the picture's brightest part), base on
    # one side only
    img = _negative(frame_lin_ratio=0.5)
    m = fm.guide_mask(GUIDE, 800, 600) > 0
    img[~m] = 20
    x_right = int((GUIDE["cx"] + GUIDE["w"] / 2) * 800)
    img[:, x_right + 3:] = 200
    est = fm.live_estimate(_noisy(img), GUIDE)
    assert not est.ok and "only one side" in est.reason
    # nothing brighter than the picture beside it at all
    img[~m] = 20
    est = fm.live_estimate(_noisy(img), GUIDE)
    assert not est.ok and "Couldn't find the film base" in est.reason
    # a base brighter than... the picture: a light leak inside the guide
    est = fm.live_estimate(_noisy(_negative(frame_lin_ratio=1.6)), GUIDE)
    assert not est.ok and "Couldn't find" in est.reason
    # clipped in the live view
    est = fm.live_estimate(_negative(base8=255), GUIDE)
    assert not est.ok and "clipped in the live view" in est.reason
    # a guide over the whole picture
    whole = dict(GUIDE, cx=0.5, cy=0.5, w=1.0, h=1.0)
    est = fm.live_estimate(_noisy(_negative()), whole)
    assert not est.ok and "Couldn't find" in est.reason


def test_a_film_base_click_still_wins():
    img = _negative(frame_lin_ratio=0.5)
    img[:40, :40] = 150                              # the user clicked somewhere else
    est = fm.live_estimate(_noisy(img), GUIDE, (0.02, 0.03, 800, 600))
    assert est.source == "click"
    want = -math.log2(fm.srgb_linear(int(_linear_to_8(fm.srgb_linear(200) * 0.5)))
                      / fm.srgb_linear(150))
    assert est.stops == pytest.approx(want, abs=0.1)


# --------------------------------------------------------------------------- #
# a RAW read inside the guide
# --------------------------------------------------------------------------- #
def _mosaic(w=640, h=480, inside=(0.30, 0.50, 0.20), outside=0.9, black=64, white=4095):
    """An RGGB mosaic: `inside` R, G, B shares inside GUIDE, `outside` beyond."""
    desc = "RGBG"
    cols = np.zeros((h, w), np.uint8)
    cols[0::2, 0::2] = 0
    cols[0::2, 1::2] = 1
    cols[1::2, 1::2] = 2
    cols[1::2, 0::2] = 3
    share = np.full((h, w), outside, np.float32)
    m = fm.guide_mask(GUIDE, w, h) > 0
    per = {0: inside[0], 1: inside[1], 2: inside[2], 3: inside[1]}
    for i, v in per.items():
        share[(cols == i) & m] = v
    mosaic = (black + share * (white - black)).astype(np.uint16)
    return mosaic, cols, desc, [black] * 4, white


def test_a_raw_is_read_inside_the_guide_only_in_strips():
    mosaic, cols, desc, black, white = _mosaic()
    r = fm.reading_in_guide(mosaic, cols, desc, black, white, GUIDE, block=16, strip_rows=64)
    assert r.levels["R"] == pytest.approx(0.30, abs=0.01)
    assert r.levels["G"] == pytest.approx(0.50, abs=0.01)
    assert r.levels["B"] == pytest.approx(0.20, abs=0.01)
    assert r.level("g") == r.levels["G"] and r.level("w") == r.levels["G"]
    whole = fm.reading_in_guide(mosaic, cols, desc, black, white, GUIDE, block=16,
                                strip_rows=4096)
    assert whole.levels == r.levels


# --------------------------------------------------------------------------- #
# the suggestion
# --------------------------------------------------------------------------- #
def _base(trichrome=True, shutter="1/4", base=0.85):
    keys = be.COLOURS if trichrome else ("w",)
    return be.entry("Portra", {k: {"shutter": shutter, "iso": "100", "base": base,
                                   "levels": dict(cl.solo({k: 255}, k)) if k != "w"
                                   else {"r": 0, "g": 0, "b": 0, "w": 200, "ir": 0}}
                               for k in keys})


def test_metered_a_stop_under_gives_a_stop_longer_live_aims_a_third_under():
    base = _base()
    sug = fm.suggest(base, True, {c: 0.425 for c in be.COLOURS}, SPEEDS, ISOS)
    assert {c: e["shutter"] for c, e in sug.exposures.items()} == \
        {"r": "1/2", "g": "1/2", "b": "1/2"}
    assert all(s == pytest.approx(1.0) for s in sug.stops.values())
    assert sug.text() == "This frame, metered: R +1 · G +1 · B +1 stops"
    assert sug.changes and sug.exposures["r"]["levels"] == cl.solo({"r": 255}, "r")
    live = fm.suggest(base, True, fm.live_levels(base, True, 0.5), SPEEDS, ISOS, live=True)
    assert all(s == pytest.approx(math.log2((1 / 3) / 0.25)) for s in live.stops.values())
    assert live.text() == "This frame: ≈ +⅓ stops (live view)"


def test_past_a_second_iso_rises_and_brighter_than_the_base_goes_shorter():
    base = _base(trichrome=False, shutter="1/2")
    sug = fm.suggest(base, False, {"w": 0.85 / 8}, SPEEDS, ISOS)       # 3 stops denser
    assert sug.exposures["w"] == {"shutter": "1s", "iso": "400",
                                  "levels": {"r": 0, "g": 0, "b": 0, "w": 200, "ir": 0}}
    assert sug.stops["w"] == pytest.approx(3.0)
    short = fm.suggest(base, False, {"w": 1.7}, SPEEDS, ISOS)
    assert short.stops["w"] == pytest.approx(-1.0) and short.text().endswith("−1 stops")
    capped = fm.suggest(_base(False, shutter="1/250"), False, {"w": 1.9}, SPEEDS, ISOS)
    assert capped.exposures["w"]["shutter"] == "1/250" and "shortest" in capped.notes[0]
    same = fm.suggest(base, False, {"w": 0.84}, SPEEDS, ISOS)
    assert not same.changes
    assert fm.suggest(_base(True), False, {"w": 0.4}, SPEEDS, ISOS) is None   # not measured


def test_frame_metering_shoots_each_colour_at_the_film_bases_exposure():
    base = _base(shutter="1/3")
    fmr = fm.FrameMetering(base, True)
    seen = []
    while True:
        shot = fmr.next_shot()
        if shot is None:
            break
        c, levels, exp = shot
        seen.append((c, exp["shutter"]))
        assert levels == cl.solo({c: 255}, c)
        fmr.landed(be.Reading(levels={"R": 0.4, "G": 0.5, "B": 0.97}))
    assert seen == [("r", "1/3"), ("g", "1/3"), ("b", "1/3")]
    assert fmr.done and fmr.result == {"r": 0.4, "g": 0.5, "b": 0.97}
    assert fmr.clipped == ["b"] and fmr.shots == 3
    assert fm.FrameMetering(_base(False), True).error.startswith("Measure the film base")
    f2 = fm.FrameMetering(base, True)
    f2.next_shot()
    f2.cancel()
    assert f2.done and f2.error == "cancelled"


# --------------------------------------------------------------------------- #
# conversion follows each frame's exposure
# --------------------------------------------------------------------------- #
@pytest.fixture
def backend(monkeypatch):
    from core import ccr_merge
    from core.ccr_backend import ccr_backend
    exps = {}
    monkeypatch.setattr(ccr_merge, "shot_exposure", lambda p: exps.get(p))
    saved = (ccr_backend.black_point_bgr, ccr_backend.black_point_exposure,
             ccr_backend.white_point_bgr, list(ccr_backend.images))
    yield ccr_backend, exps
    (ccr_backend.black_point_bgr, ccr_backend.black_point_exposure,
     ccr_backend.white_point_bgr) = saved[:3]
    ccr_backend.images[:] = saved[3]


def _img(path, merged=None, bw=None, source="auto"):
    return types.SimpleNamespace(file_path=path, is_merged=bool(merged),
                                 merge_sources=merged, resized_raw=None,
                                 conversion_inputs=({"bw": (bw, None),
                                                     "auto_bp": {"source": source}}
                                                    if bw else None))


def test_a_frames_exposure_comes_from_its_file_and_a_merge_from_its_shortest(backend):
    b, exps = backend
    exps.update({"a.raf": 0.25, "r.raf": 0.33, "g.raf": 0.25, "b.raf": 1.0})
    assert b.image_exposure(_img("a.raf")) == 0.25
    assert b.image_exposure(_img("m.tif", merged=["r.raf", "g.raf", "b.raf"])) == 0.25
    assert b.image_exposure(_img("x.raf")) is None
    assert b.image_exposure(_img("m.tif", merged=["r.raf", "x.raf", "b.raf"])) is None


def test_the_sampled_black_point_follows_the_frames_exposure(backend):
    b, exps = backend
    exps.update({"lead.raf": 0.25, "dense.raf": 0.5})
    b.set_black_point((1000.0, 2000.0, 3000.0), b.image_exposure(_img("lead.raf")))
    b.white_point_bgr = (100.0, 200.0, 300.0)
    assert b.black_point_for(_img("dense.raf")) == (2000.0, 4000.0, 6000.0)
    assert b.white_point_for(_img("dense.raf")) == (200.0, 400.0, 600.0)
    same = _img("lead.raf")
    assert b.black_point_for(same) is b.black_point_bgr          # exactly as before
    assert b.black_point_for(_img("unknown.raf")) is b.black_point_bgr
    from core.auto_black_point import RebateResult
    low = RebateResult((65535.0, 65535.0, 65535.0), "low", reason="clipped")
    black, src = b.resolve_auto_black_point(low, None, None, img=_img("dense.raf"))
    assert src == "sampled" and black == (2000.0, 4000.0, 6000.0)
    b.set_black_point((1.0, 2.0, 3.0))                            # no exposure: no scaling
    assert b.black_point_exposure is None
    assert b.black_point_for(_img("dense.raf")) == (1.0, 2.0, 3.0)


def test_the_roll_is_taken_to_one_exposure(backend):
    b, exps = backend
    exps.update({"f1.raf": 0.25, "f2.raf": 0.25, "f3.raf": 0.5, "new.raf": 0.5})
    b.images[:] = [_img("f1.raf", bw=(100.0, 200.0, 300.0)),
                   _img("f2.raf", bw=(110.0, 210.0, 310.0)),
                   _img("f3.raf", bw=(200.0, 400.0, 600.0))]
    new = _img("new.raf")
    b.images.append(new)
    got = b.recorded_roll_black(new)
    assert got == pytest.approx((200.0, 400.0, 600.0))   # 200|220|200 at 0.5 s: the median
    from core.auto_black_point import RebateResult, HIGH
    res = [RebateResult((100.0, 200.0, 300.0), HIGH), RebateResult((104.0, 204.0, 304.0), HIGH),
           RebateResult((200.0, 400.0, 600.0), HIGH), None]
    cons = b._roll_consensus(res)
    assert res[2].confidence == HIGH                               # not a jump: a longer shot
    assert res[2].black_point == (200.0, 400.0, 600.0)             # given back unscaled
    # at 0.25 s the three read 100 | 104 | 100 (the third halved): median 100
    assert cons(b.images[0]) == pytest.approx((100.0, 200.0, 300.0))
    assert cons(new) == pytest.approx((200.0, 400.0, 600.0))
    exps.pop("f2.raf")                                            # one unknown: as before
    res = [RebateResult((100.0, 200.0, 300.0), HIGH), RebateResult((104.0, 204.0, 304.0), HIGH),
           RebateResult((200.0, 400.0, 600.0), HIGH), None]
    cons = b._roll_consensus(res)
    # the old check: the longer shot looks like light round the film and is dropped
    assert res[2].confidence == "low"
    assert cons(new) == pytest.approx((102.0, 202.0, 302.0))


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #
from test_film_base_shoot import film_mon, K  # noqa: E402,F401
from test_capture_routes import _wait  # noqa: E402


def _with_base(mon, shutter="1/4"):
    entry = be.entry("Ektar", {c: {"shutter": shutter, "iso": "100", "base": 0.85,
                                   "levels": dict(cl.solo({c: 255}, c))}
                               for c in be.COLOURS})
    mon._bases = be.add(mon._bases, entry)
    mon._refresh_film_bases()


def test_the_histogram_looks_inside_the_guide_and_pauses_magnified(film_mon):
    mon, usb, light, lm, path = film_mon
    assert mon.use_frame_btn.isVisibleTo(mon)             # with the film base rows
    assert not mon.meter_btn.isVisibleTo(mon)             # revision 2: a setting
    assert mon.panel.minimumSizeHint().width() <= lm.theme.PANEL_W
    mon.actions["guide"].setChecked(True)
    assert mon.feed.params.hist_area == mon.canvas.guide
    assert mon.histogram._caption == "Live · frame guide"
    mon._cam_zoom = "5"
    mon._push_params()
    assert mon.feed.params.hist_paused and mon.feed.params.hist_area is None
    assert "paused" in mon.histogram._caption
    mon._cam_zoom = "1"
    mon.actions["guide"].setChecked(False)
    assert mon.feed.params.hist_area is None and mon.histogram._caption == "Live"


def test_the_live_line_then_use_for_this_frame_feeds_one_shoot(film_mon):
    mon, usb, light, lm, path = film_mon
    _with_base(mon)
    mon.actions["guide"].setChecked(True)
    mon.set_guide_auto(False)                    # a guide placed by hand
    mon.canvas.guide = dict(GUIDE)
    mon._neg_base_pos = (0.5, 0.08, 800, 600)
    mon.feed.full = _negative(frame_lin_ratio=0.25)
    mon._update_frame_estimate()
    assert mon.frame_note.text() == "This frame: ≈ +1⅔ stops (live view)"
    assert mon.use_frame_btn.isEnabled()
    mon.use_frame_btn.click()
    assert mon._frame_exposures["r"]["shutter"] == "1/1.3"
    assert "the next Shoot uses it" in mon.frame_note.text()
    mon._shutter_is_usb = lambda: True
    mon._ensure_hot_folder = lambda: True
    usb.dest = usb.dest or mon._usb_dest
    mon.film_base_use.setChecked(False)          # a one-off: works either way
    mon.shoot()
    assert _wait(lambda: not mon.sequence.running, 20)
    assert usb.fires == [{"shutter": "1/1.3", "iso": "100"}] * 3
    assert mon._frame_exposures is None
    usb.fires.clear()
    mon.shoot()
    assert _wait(lambda: not mon.sequence.running, 20)
    assert usb.fires == [None, None, None]       # back to the old Shoot


def test_the_live_line_needs_no_click_and_meter_is_a_setting(film_mon):
    """Revision 2: the base is found beside the guide; Meter (test shots)
    shows only with Settings → Live Capture → Show Meter."""
    mon, usb, light, lm, path = film_mon
    _with_base(mon)
    mon.actions["guide"].setChecked(True)
    mon.set_guide_auto(False)                    # a guide placed by hand
    mon.canvas.guide = dict(GUIDE)
    assert mon._neg_base_pos is None
    mon.feed.full = _noisy(_negative(frame_lin_ratio=0.25))
    mon._update_frame_estimate()
    assert mon.frame_note.text() == "This frame: ≈ +1⅔ stops (live view)"
    assert "found beside the guide" in mon.frame_note.toolTip()
    assert mon.use_frame_btn.isEnabled()
    # nothing to compare with: the line and the button say why
    img = _negative(frame_lin_ratio=0.25)
    img[fm.guide_mask(GUIDE, 800, 600) == 0] = 20             # holder, no base beside
    mon.feed.full = _noisy(img)
    mon._update_frame_estimate()
    # (no warning on the line: the user, 2026-11-01; the button's tooltip says why)
    assert mon.frame_note.text() == "" and not mon.frame_note.isVisibleTo(mon)
    assert "Couldn't find the film base" in mon.use_frame_btn.toolTip()
    assert not mon.meter_btn.isVisibleTo(mon)
    lm._settings().setValue("live/frame_meter", True)
    mon._refresh_frame_line()
    assert mon.meter_btn.isVisibleTo(mon)
    lm._settings().setValue("live/frame_meter", False)
    mon._refresh_frame_line()
    assert not mon.meter_btn.isVisibleTo(mon)


def test_expose_each_frame_for_itself_uses_the_suggestion_every_shoot(film_mon):
    """Revision 3: with the box ticked, every Shoot uses the frame's own
    exposures, no Use click; no estimate → the film base's, said so."""
    mon, usb, light, lm, path = film_mon
    _with_base(mon)
    mon.actions["guide"].setChecked(True)
    mon.set_guide_auto(False)                    # a guide placed by hand
    mon.canvas.guide = dict(GUIDE)
    mon.feed.full = _noisy(_negative(frame_lin_ratio=0.25))
    mon._update_frame_estimate()
    assert mon.use_frame_btn.isVisibleTo(mon)
    mon.frame_auto.setChecked(True)
    assert lm._settings().value("live/frame_auto") in (True, "true")
    assert not mon.use_frame_btn.isVisibleTo(mon)            # the box does its job
    mon._shutter_is_usb = lambda: True
    mon._ensure_hot_folder = lambda: True
    usb.dest = usb.dest or mon._usb_dest
    for _ in range(2):                                        # not a one-off
        usb.fires.clear()
        mon.shoot()
        assert "this frame's own exposures: ≈ +1⅔ stops" in mon.capture_caption.text()
        assert _wait(lambda: not mon.sequence.running, 20)
        assert usb.fires == [{"shutter": "1/1.3", "iso": "100"}] * 3
    # no base beside the frame: the film base's exposures, and why
    img = _negative(frame_lin_ratio=0.25)
    img[fm.guide_mask(GUIDE, 800, 600) == 0] = 20
    mon.feed.full = _noisy(img)
    mon._frame_est = []
    mon._update_frame_estimate()
    usb.fires.clear()
    mon.shoot()
    assert "No estimate for this frame" in mon.capture_caption.text()
    assert "film base's exposures" in mon.capture_caption.text()
    assert _wait(lambda: not mon.sequence.running, 20)
    assert usb.fires == [{"shutter": "1/4", "iso": "100"}] * 3
    mon.frame_auto.setChecked(False)
    assert mon.use_frame_btn.isVisibleTo(mon)


def test_meter_shoots_each_colour_reads_inside_the_guide_and_suggests(film_mon):
    mon, usb, light, lm, path = film_mon
    _with_base(mon)
    mon.actions["guide"].setChecked(True)
    reads = []

    def read_frame(p, guide):
        reads.append(guide)
        levels, exp = usb.shots[p]
        e = be.exposure_of(exp["shutter"], exp["iso"])
        # the frame's thinnest part: half the base at the base's exposure
        out = {ch: 0.85 * 0.5 * e / 0.25 * levels.get(c, 0) / 255.0
               for c, ch in (("r", "R"), ("g", "G"), ("b", "B"))}
        return be.Reading(levels=out)
    mon._read_frame = read_frame
    assert mon.start_frame_meter()
    assert mon.meter_btn.text() == "Stop Metering" and not mon.shoot_btn.isEnabled()
    assert _wait(lambda: mon._measure is None, 20)
    assert usb.fires == [{"shutter": "1/4", "iso": "100"}] * 3
    assert reads and reads[0] == mon.canvas.guide
    assert mon.frame_note.text() == "This frame, metered: R +1 · G +1 · B +1 stops"
    assert mon.meter_btn.text() == "Meter"
    mon.use_frame_btn.click()
    assert mon._frame_exposures["g"]["shutter"] == "1/2"
    # a new frame: the reading and the armed exposures were the old frame's
    mon._frame_changed()
    assert mon._frame_metered is None and mon._frame_exposures is None


def test_meter_stays_clickable_and_says_what_it_needs(film_mon):
    mon, usb, light, lm, path = film_mon
    mon._refresh_frame_line()
    assert mon.meter_btn.isEnabled()                       # not greyed: it explains
    assert "First: needs a film base measured for trichrome" in mon.meter_btn.toolTip()
    assert mon.frame_note.text() == ""                      # no warning on the line
    assert "needs a film base measured for trichrome first" in mon.use_frame_btn.toolTip()
    assert not mon.use_frame_btn.isEnabled()
    assert "Nothing to use yet" in mon.use_frame_btn.toolTip()
    # a film base measured for the other scan type says so
    entry = be.entry("Ektar W", {"w": {"shutter": "1/4", "iso": "100", "base": 0.85,
                                       "levels": None}})
    mon._bases = be.add(mon._bases, entry)
    mon._refresh_film_bases()
    mon._refresh_frame_line()
    assert "“Ektar W” was measured for one exposure; Measure Again for trichrome" in \
        mon.use_frame_btn.toolTip()
    _with_base(mon)
    mon.actions["guide"].setChecked(False)
    mon._refresh_frame_line()
    assert "First: the frame guide on" in mon.meter_btn.toolTip()
    mon.actions["guide"].setChecked(True)
    mon._refresh_frame_line()
    assert "First:" not in mon.meter_btn.toolTip()


def test_meter_needs_the_guide_and_a_film_base(film_mon):
    mon, usb, light, lm, path = film_mon
    assert not mon.start_frame_meter()
    assert "film base" in mon.capture_caption.text()
    _with_base(mon)
    mon.actions["guide"].setChecked(False)
    assert not mon.start_frame_meter()
    assert "frame guide" in mon.capture_caption.text()


def test_winding_on_with_a_following_guide_shoots_at_the_film_base(film_mon):
    """spec/auto-frame-guide.md + frame-exposure revision 3: while the guide
    waits for a whole frame, Expose each frame for itself has no estimate and
    shoots at the film base's exposures, saying why."""
    from test_auto_frame_guide import _live
    mon, usb, light, lm, path = film_mon
    _with_base(mon)
    mon.actions["guide"].setChecked(True)
    mon.film_base_box.setVisible(True)
    mon.frame_auto.setChecked(True)
    mon.feed.full = _live()
    mon._auto_guide_tick()
    mon._auto_guide_tick()
    mon.feed.full = _live(gap=(330, 430))         # the film is being wound on
    mon._auto_guide_tick()
    assert mon.canvas.guide_waiting
    mon._update_frame_estimate()
    mon._shutter_is_usb = lambda: True
    mon._ensure_hot_folder = lambda: True
    usb.dest = usb.dest or mon._usb_dest
    usb.fires.clear()
    mon.shoot()
    assert "No estimate for this frame (No whole frame in view" in mon.capture_caption.text()
    assert _wait(lambda: not mon.sequence.running, 20)
    assert usb.fires == [{"shutter": "1/4", "iso": "100"}] * 3
