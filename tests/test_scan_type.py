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


def test_measured_triplets_pass_and_their_white_sum_fails():
    """Shares measured on the user's first 30 trichrome RAFs (Bayer,
    2026-10-26): single-LED frames 0.68 and up; the three frames of a shot
    summed (light containing all three LEDs) peak at 0.44."""
    red, green, blue = (0.85, 0.11, 0.04), (0.08, 0.70, 0.21), (0.04, 0.27, 0.68)
    assert st.judge_triplet([red, green, blue])[0]
    for summed in ((0.33, 0.35, 0.32), (0.20, 0.40, 0.40), (0.23, 0.33, 0.44)):
        assert st.describe(summed) == "lit by white or mixed light"
        assert not st.judge_triplet([red, summed, blue])[0]


def test_dense_portra_frames_pass_and_white_light_does_not():
    """2026-11-02, every RAF the user has sent: dense Portra green and blue
    frames read 0.54-0.60 (flagged "Not red, green, blue" under the old 0.60);
    white light through a negative leads at 0.42-0.49 (spec/smart-triplets.md
    revision)."""
    red = (0.768, 0.172, 0.060)                                  # DSCF0007
    for green, blue in (((0.177, 0.547, 0.276), (0.112, 0.337, 0.552)),   # 0008/0009
                        ((0.172, 0.540, 0.288), (0.096, 0.336, 0.568))):  # 0078/0079
        assert st.judge_triplet([red, green, blue])[0]
    for white in ((0.271, 0.453, 0.276), (0.193, 0.487, 0.320),     # Capture One
                  (0.451, 0.382, 0.166), (0.265, 0.449, 0.286)):    # another camera
        assert st.describe(white) == "lit by white or mixed light"
        assert not st.judge_triplet([red, white, (0.04, 0.28, 0.68)])[0]


# --------------------------------------------------------------------------- #
# one picture per set
# --------------------------------------------------------------------------- #
LEDS = {0: (1.0, 0.13, 0.05), 1: (0.13, 1.0, 0.30), 2: (0.06, 0.40, 1.0)}   # R, G, B


def _scene(seed, h=400, w=600):
    """A negative's frame: blobs and edges, plus a dark holder border and
    'sprocket holes' that are the same on every shot."""
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 4) * 0.4 + 0.3
    for _ in range(30):
        cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))),
                   int(rng.integers(10, 60)), float(rng.uniform(0.1, 0.9)), -1)
    img[:40] = img[-40:] = 0.02
    for x in range(20, w, 70):
        img[8:30, x:x + 30] = img[-30:-8, x:x + 30] = 1.0
    return img


def _shot(scene, led, gamma=1.0, black=512, seed=0):
    """The scene under one LED as an RGGB mosaic: each dye layer renders the
    scene with its own tone (gamma), each photosite colour sees the LED by
    its own sensitivity."""
    h, w = scene.shape
    colors = np.zeros((h, w), np.uint8)
    colors[0::2, 1::2] = 1
    colors[1::2, 0::2] = 3
    colors[1::2, 1::2] = 2
    sens = np.array(LEDS[led] + (LEDS[led][1],))[colors]
    rng = np.random.default_rng(seed)
    mosaic = 12000 * sens * scene ** gamma + rng.normal(0, 30, (h, w)) + black
    return mosaic, colors


def _picture(scene, led, gamma=1.0, seed=0):
    m, c = _shot(scene, led, gamma, seed=seed)
    return st.picture_from_mosaic(m, c, [512] * 4)


def test_one_shot_under_three_leds_is_one_picture():
    sc = _scene(1)
    r, g, b = _picture(sc, 0, 1.0, 1), _picture(sc, 1, 0.7, 2), _picture(sc, 2, 1.4, 3)
    assert min(p.reliability for p in (r, g, b)) > 0.9
    assert st.same_picture(r, g) and st.same_picture(r, b) and st.same_picture(g, b)
    other = _picture(_scene(2), 2)
    assert st.same_picture(r, other) is False
    assert st.picture_match(r, other) < 0.25          # the shared border/holes left out


def test_a_featureless_frame_cant_be_told():
    m = np.full((400, 600), 4000.0) + np.random.default_rng(0).normal(0, 30, (400, 600))
    c = _shot(_scene(1), 0)[1]
    flat = st.picture_from_mosaic(m, c, [512] * 4)
    assert flat is None or flat.reliability < st.PICTURE_RELIABLE
    assert st.same_picture(flat, _picture(_scene(1), 0)) is None
    assert st.same_picture(None, _picture(_scene(1), 0)) is None


def test_the_tether_refuses_two_shots_and_rescues_a_dense_frame():
    one, two = _scene(1), _scene(2)
    red, green, blue = (0.80, 0.15, 0.05), (0.15, 0.62, 0.23), (0.08, 0.30, 0.62)
    pics = [_picture(one, 0), _picture(one, 1, 0.7), _picture(two, 2, 1.4)]
    ok, k, why = st.judge_triplet([red, green, blue], ["a", "b", "c"], pics)
    assert not ok and k == 2 and "different picture" in why and "frames 1 and 2" in why
    pics[2] = _picture(one, 2, 1.4)
    assert st.judge_triplet([red, green, blue], None, pics)[0]
    weak = (0.22, 0.46, 0.32)                     # denser than any frame measured
    assert not st.judge_triplet([red, weak, blue])[0]           # no pictures: the share rule
    assert st.judge_triplet([red, weak, blue], None, pics)[0]   # one picture: taken as green
    pics[2] = _picture(two, 2, 1.4)
    assert not st.judge_triplet([red, weak, blue], None, pics)[0]


def test_read_light_gives_shares_and_picture_from_one_read(monkeypatch):
    import types
    m, c = _shot(_scene(3), 1)
    mosaic = np.zeros((m.shape[0] * 5, m.shape[1] * 5), np.uint16)
    colors = np.zeros(mosaic.shape, np.uint8)
    mosaic[::5, ::5], colors[::5, ::5] = m.astype(np.uint16), c
    reads = []

    class FakeRaw:
        num_colors = 3
        color_desc = b"RGBG"
        raw_pattern = np.array([[0, 1], [3, 2]])
        black_level_per_channel = [512] * 4
        raw_image_visible = mosaic
        raw_colors_visible = colors

        def __enter__(self):
            reads.append(1)
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(imread=lambda p: FakeRaw()))
    r = st.read_light("frame.RAF")
    assert len(reads) == 1 and st.describe(r.shares) == "lit green"
    assert r.picture is not None and r.picture.reliability > 0.9
    assert st.same_picture(r.picture, _picture(_scene(3), 0))


def test_light_shares_reads_the_mosaic_before_the_file_closes(monkeypatch):
    """rawpy's raw_image_visible / raw_colors_visible are views into LibRaw's
    buffer, freed when the file closes; reading them afterwards segfaulted on
    a real RAF. Here closing wipes the buffer, so a view would read zeros."""
    import types
    m, c = _bayer(1200, 7000, 1500)
    mosaic, colors = m.astype(np.uint16), c.astype(np.uint8)

    class FakeRaw:
        num_colors = 3
        color_desc = b"RGBG"
        raw_pattern = np.array([[0, 1], [3, 2]])
        black_level_per_channel = [512] * 4
        raw_image_visible = mosaic
        raw_colors_visible = colors

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            mosaic[...] = 0          # the buffer is gone
            colors[...] = 0
            return False

    monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(imread=lambda p: FakeRaw()))
    shares = st.light_shares("frame.RAF")
    assert shares is not None and st.describe(shares) == "lit green"


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
    monkeypatch.setattr(st, "read_light",
                        lambda p: st.LightReading(shares[os.path.basename(p)[0]]))
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
    # one shot's red and green with the next shot's blue: held, not merged
    pic = {"1": _picture(_scene(1), 0), "2": _picture(_scene(2), 2)}
    monkeypatch.setattr(st, "read_light", lambda p: st.LightReading(
        shares[os.path.basename(p)[0]], pic[os.path.basename(p)[1]]))
    w.process_triplet({"paths": ["/x/r1.RAF", "/x/g1.RAF", "/x/b2.RAF"], "check": True})
    assert len(got) == 2 and "different picture" in held[-1]["reason"]


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
    assert not tl.attention_bar.isHidden() and tl.attention_list.count() == 1
    assert tl.attention_bar.text() == "1 rejected combination"
    item = tl.attention_list.item(0)
    assert item.text() == "Not red, green, blue" and "w.RAF" in item.toolTip()
    assert len(ccr_backend.images) == 0                      # not a frame in the list
    eid = mw._tether_attention[0]["id"]
    tl.attention_action.emit(eid, "merge")
    assert mw.trips[-1] == {"paths": ["/x/r.RAF", "/x/w.RAF", "/x/b.RAF"], "check": False}
    assert tl.attention_bar.isHidden()
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
    assert mw._tether_attention == [] and tl.attention_bar.isHidden()


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


def test_rejected_combinations_take_one_line_and_open_on_hover_or_click(mw):
    """spec/rejected-strip-compact.md: one line ("N rejected combinations");
    the tiles open over the list on hover and stay open when it is clicked."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtCore import QPointF
    tl = mw.thumbnail_list
    mw.resize(1300, 800)
    mw.show()
    for i in range(3):
        mw._on_needs_attention({"paths": [f"/x/{i}.RAF"], "reason": "x", "thumbs": []})
    _app.processEvents()
    assert tl.attention_bar.text() == "3 rejected combinations"
    assert tl.attention_bar.height() < 40                      # one line, not the tiles
    pop = tl.attention_popup
    assert not pop.isVisible()
    enter = QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2))
    QApplication.sendEvent(tl.attention_bar, enter)
    assert pop.isVisible() and tl.attention_list.count() == 3
    assert pop.geometry().bottom() <= tl.attention_bar.geometry().top()   # above the line
    QApplication.sendEvent(tl.attention_bar, QEvent(QEvent.Leave))
    tl._maybe_hide_attention()                                # the timer's job
    assert not pop.isVisible()
    tl.attention_bar.click()                                  # pinned
    assert pop.isVisible()
    QApplication.sendEvent(tl.attention_bar, QEvent(QEvent.Leave))
    tl._maybe_hide_attention()
    assert pop.isVisible()
    tl.attention_bar.click()                                  # unpinned
    assert not pop.isVisible()
    for e in list(mw._tether_attention):
        tl.attention_action.emit(e["id"], "remove")
    assert tl.attention_bar.isHidden() and not pop.isVisible()
    mw.hide()
