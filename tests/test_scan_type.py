#!/usr/bin/env python3
"""Scan type (spec/scan-type.md): colour, trichrome or black and white, and
how Hot Folder Tether loads each: triplets merged, a shot that can't be
merged greyed out under the frames, B&W frames in Black & White."""

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

from core import scan_type as st  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402


@pytest.fixture(autouse=True)
def _scan(monkeypatch):
    monkeypatch.setattr(ccr_backend, "scan_type", "colour", raising=False)
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


# --------------------------------------------------------------------------- #
# grouping
# --------------------------------------------------------------------------- #
class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_triplets_in_name_order_and_stalls():
    c = Clock()
    g = st.TripletGrouper(clock=c, stall_s=30)
    g.add("/f/DSCF0002.RAF")
    g.add("/f/DSCF0001.RAF")
    assert g.take() == []
    g.add("/f/DSCF0003.RAF")
    g.add("/f/DSCF0004.RAF")
    assert g.take() == [("/f/DSCF0001.RAF", "/f/DSCF0002.RAF", "/f/DSCF0003.RAF")]
    assert g.pending == ["/f/DSCF0004.RAF"]
    c.t += 29
    assert g.stalled() == []
    c.t += 2
    assert g.stalled() == ["/f/DSCF0004.RAF"] and g.pending == []
    trips, left = st.group_existing([f"/f/{i}.RAF" for i in "abcdefgh"])
    assert len(trips) == 2 and left == ["/f/g.RAF", "/f/h.RAF"]


# --------------------------------------------------------------------------- #
# the light check
# --------------------------------------------------------------------------- #
def _bayer(r, g, b, black=512, h=60, w=80):
    """An RGGB mosaic with these channel levels above black."""
    colors = np.zeros((h, w), np.uint8)
    colors[0::2, 1::2] = 1
    colors[1::2, 0::2] = 3
    colors[1::2, 1::2] = 2
    lev = np.array([r, g, b, g], np.float64) + black
    rng = np.random.default_rng(0)
    mosaic = lev[colors] + rng.normal(0, 20, (h, w))
    return mosaic, colors


def test_shares_tell_the_light_apart():
    m, c = _bayer(9000, 900, 300)
    red = st.shares_from_mosaic(m, c, "RGBG", [512] * 4)
    assert red[0] > 0.85
    m, c = _bayer(1200, 7000, 1500)
    assert st.describe(st.shares_from_mosaic(m, c, "RGBG", [512] * 4)) == "lit green"
    # white light through an orange negative: red leads, but not by enough
    m, c = _bayer(5200, 3600, 2400)
    white = st.shares_from_mosaic(m, c, "RGBG", [512] * 4)
    assert st.describe(white) == "lit by white or mixed light"


def test_judge_a_triplet():
    red, green, blue = (0.85, 0.1, 0.05), (0.15, 0.7, 0.15), (0.05, 0.2, 0.75)
    white = (0.46, 0.32, 0.22)
    assert st.judge_triplet([red, green, blue]) == (True, None, "")
    ok, k, why = st.judge_triplet([red, white, blue], ["a.RAF", "b.RAF", "c.RAF"])
    assert not ok and k == 1 and "b.RAF" in why and "green" in why and "white" in why
    ok, k, _ = st.judge_triplet([green, blue, red])       # shifted by one
    assert not ok and k == 0
    assert st.judge_triplet([None, green, None])[0]        # mono / unreadable: not judged


# --------------------------------------------------------------------------- #
# the worker
# --------------------------------------------------------------------------- #
def _png(tmp_path, name, seed):
    p = tmp_path / name
    cv2.imwrite(str(p), (np.random.RandomState(seed).rand(60, 80, 3) * 255).astype(np.uint8))
    return str(p)


def test_worker_merges_a_good_triplet_and_holds_a_bad_one(tmp_path, monkeypatch):
    from core.tether_watcher import TetherWatchWorker
    from core.ccr_image import CCRImage
    import core.catalog as catalog
    shares = {"r": (0.85, 0.1, 0.05), "g": (0.15, 0.7, 0.15), "b": (0.05, 0.2, 0.75),
              "w": (0.46, 0.32, 0.22)}
    monkeypatch.setattr(st, "light_shares", lambda p: shares[os.path.basename(p)[0]])
    monkeypatch.setattr(st, "thumb_bgr", lambda p: np.zeros((10, 12, 3), np.uint8))
    made = []

    def fake_merge(paths, merge_demosaic=True, catalog_data=None, merge_mono=False):
        img = CCRImage(_png(tmp_path, "m.png", 1))
        img.is_merged, img.merge_sources = True, list(paths)
        made.append(list(paths))
        return [img]
    monkeypatch.setattr(catalog, "create_images_for_merge", fake_merge)
    w = TetherWatchWorker()
    got, held = [], []
    w.captured.connect(got.append)
    w.needsAttention.connect(held.append)
    w.process_triplet({"paths": ["/x/r1.RAF", "/x/g1.RAF", "/x/b1.RAF"], "check": True})
    assert len(got) == 1 and made == [["/x/r1.RAF", "/x/g1.RAF", "/x/b1.RAF"]]
    w.process_triplet({"paths": ["/x/r2.RAF", "/x/w2.RAF", "/x/b2.RAF"], "check": True})
    assert len(got) == 1 and len(held) == 1
    assert "frame 2" in held[0]["reason"] and len(held[0]["thumbs"]) == 3
    w.process_triplet({"paths": ["/x/r2.RAF", "/x/w2.RAF", "/x/b2.RAF"], "check": False})
    assert len(got) == 2                                   # Merge Anyway


# --------------------------------------------------------------------------- #
# the main window
# --------------------------------------------------------------------------- #
@pytest.fixture
def mw(monkeypatch):
    from ui.main_window import MainWindow
    from core.scan_type import TripletGrouper
    w = MainWindow()
    monkeypatch.setattr(w._settings, "setValue", lambda *a, **k: None)
    w._tether_active = True
    w._tether_folder = "/x"
    w._tether_grouper = TripletGrouper()
    w._tether_worker = object()          # requests go to the collectors below
    w.singles, w.trips = [], []
    monkeypatch.setattr(w, "_dispatch_to_worker", lambda p: w.singles.append(p))
    w._tether_triplet_req.connect(lambda job: w.trips.append(job))
    yield w
    w._tether_active = False
    w._tether_worker = None


def test_trichrome_files_go_in_threes_and_jpegs_alone(mw):
    mw.set_scan_type("trichrome")
    assert ccr_backend.scan_type == "trichrome"
    for n in ("DSCF0002.RAF", "DSCF0001.RAF"):
        mw._route_tether_file(f"/x/{n}")
    assert mw.trips == [] and "2 of 3" in mw._tether_note()
    mw._route_tether_file("/x/DSCF0003.RAF")
    assert mw.trips == [{"paths": ["/x/DSCF0001.RAF", "/x/DSCF0002.RAF", "/x/DSCF0003.RAF"],
                         "check": True}]
    mw._route_tether_file("/x/DSCF0004.JPG")
    assert mw.singles == ["/x/DSCF0004.JPG"]


def test_switching_away_from_trichrome_loads_the_part_filled_shot_singly(mw):
    seen = []
    mw.scan_type_changed.connect(seen.append)
    mw.set_scan_type("trichrome")
    mw._route_tether_file("/x/DSCF0001.RAF")
    mw.set_scan_type("colour")
    assert mw.singles == ["/x/DSCF0001.RAF"] and seen == ["trichrome", "colour"]
    assert mw.tether_banner.scan_type() == "colour"
    mw._route_tether_file("/x/DSCF0002.RAF")
    assert mw.singles[-1] == "/x/DSCF0002.RAF"              # colour: one frame each


def test_arriving_frames_record_how_they_were_scanned(mw, tmp_path):
    from core.ccr_image import CCRImage
    mw.set_scan_type("bw")
    a = CCRImage(_png(tmp_path, "a.png", 2))
    mw._on_capture(a)
    assert a.capture_type == "bw" and a.color_profile == "bw"
    mw.set_scan_type("trichrome")
    m = CCRImage(_png(tmp_path, "m.png", 3))
    m.is_merged = True
    mw._on_capture(m)
    assert m.capture_type == "trichrome" and m.color_profile == "color"
    s = CCRImage(_png(tmp_path, "s.png", 4))
    mw._on_capture(s)                                        # a lone file of a trichrome scan
    assert s.capture_type is None
    mw.set_scan_type("colour")
    c = CCRImage(_png(tmp_path, "c.png", 5))
    mw._on_capture(c)
    assert c.capture_type == "colour" and c.color_profile == "color"


def test_a_shot_that_needs_attention_is_greyed_out_and_acted_on(mw, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    tl = mw.thumbnail_list
    mw._on_needs_attention({"paths": ["/x/r.RAF", "/x/w.RAF", "/x/b.RAF"],
                            "reason": "frame 2 (w.RAF) should be lit green but looks "
                                      "lit by white or mixed light",
                            "thumbs": [np.full((10, 12, 3), 200, np.uint8)] * 3})
    assert not tl.attention_list.isHidden() and tl.attention_list.count() == 1
    item = tl.attention_list.item(0)
    assert item.text() == "Not red, green, blue" and "w.RAF" in item.toolTip()
    assert len(ccr_backend.images) == 0                      # not a frame in the list
    eid = mw._tether_attention[0]["id"]
    tl.attention_action.emit(eid, "merge")
    assert mw.trips[-1] == {"paths": ["/x/r.RAF", "/x/w.RAF", "/x/b.RAF"], "check": False}
    assert tl.attention_list.isHidden()
    # left over: load singly, or choose the files by hand
    mw._on_needs_attention({"paths": ["/x/9.RAF"], "reason": "1 of the 3 frames of a shot "
                            "arrived; the rest didn't come within 30 s", "thumbs": []})
    assert tl.attention_list.item(0).text() == "Frames missing"
    eid = mw._tether_attention[0]["id"]
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        lambda *a, **k: (["/x/c.RAF", "/x/a.RAF", "/x/b.RAF"], ""))
    tl.attention_action.emit(eid, "choose")
    assert mw.trips[-1] == {"paths": ["/x/a.RAF", "/x/b.RAF", "/x/c.RAF"], "check": False}
    mw._on_needs_attention({"paths": ["/x/7.RAF", "/x/8.RAF"], "reason": "x", "thumbs": []})
    eid = mw._tether_attention[0]["id"]
    tl.attention_action.emit(eid, "singles")
    assert mw.singles[-2:] == ["/x/7.RAF", "/x/8.RAF"]
    mw._on_needs_attention({"paths": ["/x/6.RAF"], "reason": "x", "thumbs": []})
    tl.attention_action.emit(mw._tether_attention[0]["id"], "remove")
    assert mw._tether_attention == [] and tl.attention_list.isHidden()


def test_stopping_part_way_keeps_the_waiting_frames(mw):
    mw.set_scan_type("trichrome")
    mw._route_tether_file("/x/DSCF0001.RAF")
    mw.stop_tethering()
    assert len(mw._tether_attention) == 1
    assert mw._tether_attention[0]["paths"] == ["/x/DSCF0001.RAF"]


def test_capture_type_survives_the_catalog(tmp_path):
    from core.ccr_image import CCRImage
    from core.catalog import serialize_image, _restore_image
    img = CCRImage(_png(tmp_path, "a.png", 6))
    assert img.capture_type is None
    img.capture_type = "trichrome"
    back = _restore_image(img.file_path, serialize_image(img))
    assert back.capture_type == "trichrome"


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #
def test_monitor_follows_and_sets_the_scan_type(tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, Signal, QSettings
    import test_live_monitor as tlm
    from widgets import live_monitor as lm
    path = str(tmp_path / "live.ini")
    monkeypatch.setattr(lm, "_settings", lambda: QSettings(path, QSettings.IniFormat))

    class MW(tlm._MW):
        scan_type_changed = Signal(str)

        def set_scan_type(self, t):
            self.calls.append(("scan", t))
            ccr_backend.scan_type = t
            self.scan_type_changed.emit(t)
    mw = MW()
    mon, feed, light, dev = tlm._monitor(mw)
    try:
        mon._on_scan_type_chosen(mon.scan_combo.findData("bw"))
        assert ("scan", "bw") in mw.calls and mon.neg_bw.isChecked()
        mw.set_scan_type("trichrome")
        assert mon.scan_type() == "trichrome" and not mon.neg_bw.isChecked()
        assert "triplet" in mon.capture_caption.text()
        mw.set_scan_type("colour")
        assert "with the light as it is" in mon.capture_caption.text()
    finally:
        tlm._close(mon)
