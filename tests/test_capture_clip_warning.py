"""Capture warns when a shot clips inside the frame
(spec/capture-clip-warning.md)."""
import json
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import capture_log as cl  # noqa: E402
from core import frame_meter as fm  # noqa: E402

BLACK, WHITE = 1024, 16383
GUIDE = {"cx": 0.5, "cy": 0.5, "w": 0.5, "h": 0.5, "angle": 0.0}


def _mosaic(h=400, w=600, level=0.85, seed=2):
    """An RGGB Bayer mosaic, every colour at `level` of black..white with noise."""
    rng = np.random.default_rng(seed)
    v = BLACK + (WHITE - BLACK) * level + rng.normal(0, 60, (h, w))
    cols = np.zeros((h, w), np.uint8)
    cols[0::2, 1::2] = 1
    cols[1::2, 0::2] = 3
    cols[1::2, 1::2] = 2
    return np.clip(v, 0, WHITE).astype(np.uint16), cols


def _clip(m, cols, idx, y0, y1, x0, x1, at):
    sel = np.zeros_like(cols, bool)
    sel[y0:y1, x0:x1] = True
    sel &= np.isin(cols, idx)
    m[sel] = at


def test_an_unclipped_frame_says_nothing():
    m, cols = _mosaic(level=0.85)
    assert fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, GUIDE) == {}
    assert fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, None) == {}


def test_a_clip_below_the_white_level_inside_the_guide():
    m, cols = _mosaic(level=0.80)
    at = int(BLACK + (WHITE - BLACK) * 0.93)          # the sensor's own clip, under white
    _clip(m, cols, [0], 180, 220, 280, 320, at)        # a red patch in the middle
    out = fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, GUIDE)
    assert set(out) == {"R"}
    # the guide's inset area: 0.5 x 0.94 of 400 x 600 -> 188 x 282 px; red is a quarter
    inner = (0.5 * 0.94 * 400) * (0.5 * 0.94 * 600) / 4
    assert out["R"] == pytest.approx(20 * 20 / inner, rel=0.15)


def test_a_clip_outside_the_guide_is_not_the_frame():
    m, cols = _mosaic(level=0.80)
    _clip(m, cols, [0, 1, 2, 3], 0, 40, 0, 600, WHITE)  # open light at the top
    assert fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, GUIDE) == {}
    assert fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, None) == {}  # middle 60 %
    _clip(m, cols, [1, 3], 150, 250, 200, 400, WHITE)   # green, in the middle
    assert set(fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, None)) == {"G"}


def test_a_lone_maximum_and_a_low_top_are_not_clips():
    m, cols = _mosaic(level=0.80)
    m[200, 300] = WHITE                                 # one photosite at the top
    assert fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, GUIDE) == {}
    m2, cols2 = _mosaic(level=0.30)
    _clip(m2, cols2, [0], 150, 250, 250, 350, int(BLACK + (WHITE - BLACK) * 0.4))
    assert fm.clipping_in_guide(m2, cols2, "RGBG", [BLACK] * 4, WHITE, GUIDE) == {}


def test_a_turned_guide_and_a_pattern_read_by_masks_agree():
    m, cols = _mosaic(level=0.80)
    _clip(m, cols, [2], 190, 210, 290, 310, WHITE)
    turned = dict(GUIDE, angle=20.0)
    a = fm.clipping_in_guide(m, cols, "RGBG", [BLACK] * 4, WHITE, turned)
    # the same mosaic cut one column in: the 2x2 test fails on a 1-wide strip, so
    # pad it with a non-repeating column to force the mask path
    cols2 = cols.copy()
    cols2[0, 0] = 2
    b = fm.clipping_in_guide(m, cols2, "RGBG", [BLACK] * 4, WHITE, turned)
    assert set(a) == {"B"} and set(b) == {"B"}
    assert a["B"] == pytest.approx(b["B"], rel=0.02)


def test_the_warning_text():
    assert fm.clip_text({}) == "" and fm.clip_text({"a.raf": {}}) == ""
    t = fm.clip_text({"a_1RED.raf": {"R": 0.023}, "a_2GREEN.raf": {"G": 0.004, "R": 0.01}})
    assert t.startswith("This shot clips inside the frame: red 2.3 %, green 0.4 %.")
    assert "Measure it again" in t
    assert "in the shot:" in fm.clip_text({"a": {"B": 0.0005}}, in_frame=False)
    assert "blue a little" in fm.clip_text({"a": {"B": 0.0005}})


def test_note_records_on_the_shot(tmp_path):
    cl.clear()
    folder = str(tmp_path)
    cl.begin_shot(folder, {"roll": "R0001", "shot": 3, "scan_type": "trichrome",
                           "format": "645", "film": "Gold", "files": {}})
    name = cl.shot_stem("R0001", "645", "Gold", 3, "red") + ".raf"
    assert cl.add_file(os.path.join(folder, name))
    assert cl.note(os.path.join(folder, name), "clip", {"R": 0.02})
    assert not cl.note(os.path.join(folder, "other.raf"), "clip", {"R": 0.02})
    (e,) = cl.read_log(folder)
    assert e["clip"] == {"red": {"R": 0.02}}
    cl.clear()


# --------------------------------------------------------------------------- #
# in Capture
# --------------------------------------------------------------------------- #
@pytest.fixture
def shooting(tmp_path, monkeypatch):
    from test_capture_routes import mon_usb, _wait
    from core.ccr_backend import ccr_backend
    gen = mon_usb.__wrapped__(tmp_path, monkeypatch)
    mon, feed, light, usb, mw, asked, lm = next(gen)
    lm._settings().setValue("live/shutter", "usb")
    usb.opened()
    _app.processEvents()
    monkeypatch.setattr(ccr_backend, "scan_type", "colour", raising=False)
    mon._apply_scan_type("colour")
    mw._tether_active = True
    mw._tether_folder = mw.folder
    mw.capture_session = {"roll": "R0050", "format": "645", "film": "Gold 200"}
    mon._clip_threaded = False
    reads = []

    def fake_read(path, guide):
        reads.append((os.path.basename(path), guide))
        return dict(results.get(os.path.basename(path), {}))
    results = {}
    mon._read_clipping = fake_read
    yield mon, usb, mw, reads, results, _wait
    try:
        next(gen)
    except StopIteration:
        pass
    cl.clear()


def _shoot(mon, usb, _wait):
    mon.shoot()
    assert mon.sequence.running
    assert _wait(lambda: "fire" in usb.log)


def test_a_clipped_shot_warns_when_it_ends(shooting):
    mon, usb, mw, reads, results, _wait = shooting
    name = cl.shot_stem("R0050", "645", "Gold 200", 1, "white") + ".raf"
    results[name] = {"G": 0.012}
    _shoot(mon, usb, _wait)
    usb.file_saved.emit(os.path.join(mw.folder, name))
    _app.processEvents()
    assert [r[0] for r in reads] == [name]
    assert not mon.sequence.running
    text = mon.capture_caption.text()
    assert "clips" in text and "green 1.2 %" in text
    (e,) = [x for x in cl.read_log(mw.folder) if x.get("shot") == 1]
    assert e["clip"] == {"white": {"G": 0.012}}
    # the next Shoot starts afresh
    usb.log.clear()
    _shoot(mon, usb, _wait)
    assert mon._clip_shot == {} and mon.clip_warning_text() == ""


def test_an_unclipped_shot_says_nothing_and_ir_and_measure_files_are_not_read(shooting):
    mon, usb, mw, reads, results, _wait = shooting
    _shoot(mon, usb, _wait)
    name = cl.shot_stem("R0050", "645", "Gold 200", 1, "white") + ".raf"
    usb.file_saved.emit(os.path.join(mw.folder, name))
    _app.processEvents()
    assert "clips" not in mon.capture_caption.text()
    ir = cl.shot_stem("R0050", "645", "Gold 200", 1, "ir") + ".raf"
    mon._clip_check(os.path.join(mw.folder, ir))
    assert [r[0] for r in reads] == [name]               # the IR shot isn't read
    mon._measure = {"in_flight": False}                  # a Measure's file goes elsewhere
    mon._on_usb_file(os.path.join(mw.folder, "DSCF0009.raf"))
    mon._measure = None
    assert [r[0] for r in reads] == [name]


def test_no_check_before_any_shoot(shooting):
    mon, usb, mw, reads, results, _wait = shooting
    mon._clip_check(os.path.join(mw.folder, "DSCF0001.raf"))
    assert reads == []
