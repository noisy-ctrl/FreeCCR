#!/usr/bin/env python3
"""The capture log and capture file names (spec/capture-log-and-names.md)."""

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import capture_log as cl  # noqa: E402
from core import camera_backends as cb  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    cl.clear()
    yield
    cl.clear()


# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #
def test_names():
    assert cl.shot_stem("R0034", "645", "Portra 400", 13, "red") == "R0034_645_Portra400_013_RED"
    assert cl.shot_stem("R0034", "", "", 13, "green") == "R0034_013_GREEN"
    assert cl.shot_stem("R0034", "6x7", "", 2, "white") == "R0034_6x7_002_WHITE"
    assert cl.shot_stem("R0034", "", "HP5+ @800", 1234, "bw") == "R0034_HP5800_1234_BW"
    assert cl.shot_stem("", "", "", 1, "blue") == "R0001_001_BLUE"
    assert cl.single_slot("bw") == "bw" and cl.single_slot("colour") == "white"


@pytest.mark.parametrize("last, want", [
    ("", "R0001"), ("R0034", "R0035"), ("R0999", "R1000"), ("Cornwall", "Cornwall-2"),
    ("Cornwall-2", "Cornwall-3"), ("R 9/", "R10")])
def test_next_roll(last, want):
    assert cl.next_roll(last) == want


def test_next_shot_from_log_and_names(tmp_path):
    f = str(tmp_path)
    assert cl.next_shot(f, "R0001") == 1
    cl.append(f, {"roll": "R0001", "shot": 4, "files": {}})
    cl.append(f, {"roll": "R0002", "shot": 9, "files": {}})
    assert cl.next_shot(f, "R0001") == 5
    (tmp_path / "R0001_645_Portra400_007_RED.RAF").write_bytes(b"x")
    (tmp_path / "R0001_645_Portra400_011_BLUE_1.RAF").write_bytes(b"x")   # a retake
    (tmp_path / "R00010_001_RED.RAF").write_bytes(b"x")                   # another roll
    assert cl.next_shot(f, "R0001") == 12
    assert cl.next_shot(f, "R0003") == 1


def test_log_last_line_wins_and_bad_lines_are_skipped(tmp_path):
    f = str(tmp_path)
    cl.append(f, {"roll": "R1", "shot": 1, "files": {"red": "a"}})
    cl.append(f, {"roll": "R1", "shot": 2, "files": {}})
    with open(cl.log_path(f), "a") as fh:
        fh.write("not json\n[1, 2]\n\n")
    cl.append(f, {"roll": "R1", "shot": 1, "files": {"red": "a", "green": "b"}})
    got = cl.read_log(f)
    assert [(e["shot"], len(e["files"])) for e in got] == [(1, 2), (2, 0)]
    assert cl.read_log(str(tmp_path / "nope")) == [] and cl.read_log("") == []


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
def test_files_take_the_colours_in_order_and_late_files_join(tmp_path):
    f = str(tmp_path)
    cl.begin_shot(f, {"roll": "R0034", "shot": 3, "scan_type": "trichrome",
                      "format": "645", "film": "Portra 400", "route": "usb"})
    assert cl.add_file(os.path.join(f, "R0034_645_Portra400_003_RED.RAF"), now=0)
    assert not cl.add_file(os.path.join(f, "R0034_645_Portra400_003_RED.RAF"), now=0)
    assert cl.add_file(os.path.join(f, "x_GREEN.RAF"), now=1)
    assert cl.end_shot("stopped", now=2)
    assert cl.add_file(os.path.join(f, "late.RAF"), now=2 + cl.GRACE_S - 1)   # in grace
    assert not cl.add_file(os.path.join(f, "more.RAF"), now=3)              # all filled
    (e,) = cl.read_log(f)
    assert e["files"] == {"red": "R0034_645_Portra400_003_RED.RAF",
                          "green": "x_GREEN.RAF", "blue": "late.RAF"}
    assert e["done"] and e["stopped"] == "stopped" and e["v"] == 1
    assert e["format"] == "645" and e["film"] == "Portra 400" and e["time"]


def test_after_the_grace_nothing_joins_and_a_single_has_one_slot(tmp_path):
    f = str(tmp_path)
    cl.begin_shot(f, {"roll": "R1", "shot": 1, "scan_type": "colour"})
    assert cl.end_shot("done", now=0)
    assert not cl.add_file("late.RAF", now=cl.GRACE_S + 1)
    cl.begin_shot(f, {"roll": "R1", "shot": 2, "scan_type": "bw"})
    assert cl.add_file("one.RAF") and not cl.add_file("two.RAF")
    assert cl.read_log(f)[-1]["files"] == {"bw": "one.RAF"}
    assert "stopped" not in cl.read_log(f)[0]
    cl.clear()
    assert not cl.add_file("x.RAF")


# --------------------------------------------------------------------------- #
# Open takes logged sets exactly
# --------------------------------------------------------------------------- #
def test_plan_takes_logged_sets_first_and_the_rest_by_light(tmp_path):
    from core import triplet_plan as tp
    f = str(tmp_path)
    names = ["A_RED.RAF", "A_GREEN.RAF", "A_BLUE.RAF", "B_RED.RAF", "B_GREEN.RAF",
             "x1.RAF", "x2.RAF", "x3.RAF"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    cl.append(f, {"roll": "R1", "shot": 1, "scan_type": "trichrome",
                  "files": {"red": "A_RED.RAF", "green": "A_GREEN.RAF", "blue": "A_BLUE.RAF"}})
    # a logged shot with a file missing from the selection is not taken
    cl.append(f, {"roll": "R1", "shot": 2, "scan_type": "trichrome",
                  "files": {"red": "B_RED.RAF", "green": "B_GREEN.RAF", "blue": "B_BLUE.RAF"}})
    paths = [os.path.join(f, n) for n in reversed(names)]
    read = []
    light = {"x1": (0.8, 0.1, 0.1), "x2": (0.1, 0.8, 0.1), "x3": (0.1, 0.1, 0.8),
             "B_RED": (0.8, 0.1, 0.1), "B_GREEN": (0.1, 0.8, 0.1)}

    def shares(p):
        read.append(os.path.basename(p))
        return light[os.path.splitext(os.path.basename(p))[0]]
    plan = tp.plan_triplets(paths, shares_fn=shares, workers=1, time_fn=lambda p: None,
                            quality=lambda p, c: (0.0, 1.0))
    assert plan.sets[0] == tuple(os.path.join(f, n) for n in names[:3])
    assert plan.sets[1:] == [tuple(os.path.join(f, f"x{i}.RAF") for i in (1, 2, 3))]
    assert not any(n.startswith("A_") for n in read)            # never read
    assert any("B_RED.RAF" in str(n["paths"]) for n in plan.notes)


# --------------------------------------------------------------------------- #
# the backend names the file it downloads
# --------------------------------------------------------------------------- #
def test_named_path_and_the_fake_backend_uses_the_name_once(tmp_path):
    f = str(tmp_path)
    assert cb.named_path(f, "DSCF0001.RAF", "R1_001_RED") == os.path.join(f, "R1_001_RED.RAF")
    (tmp_path / "R1_001_RED.RAF").write_bytes(b"x")
    assert cb.named_path(f, "DSCF0001.RAF", "R1_001_RED") == os.path.join(f, "R1_001_RED_1.RAF")
    assert cb.named_path(f, "DSCF0001.RAF") == os.path.join(f, "DSCF0001.RAF")
    t = [0.0]
    b = cb.FakeBackend(clock=lambda: t[0])
    b.open("0", f)
    b.next_name = "R1_002_GREEN"
    b.fire()
    t[0] += 1
    (_k, path), = b.poll(0)
    assert os.path.basename(path) == "R1_002_GREEN.jpg"
    b.fire()                                    # nobody named this one
    t[0] += 1
    (_k, path2), = b.poll(0)
    assert os.path.basename(path2).startswith("FAKE")


def test_the_gphoto2_download_uses_the_name_once(tmp_path):
    from test_capture_routes import _gp_backend
    b, gp, clock = _gp_backend(tmp_path)
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    b.next_name = "R0034_645_Portra400_001_RED"
    p1 = b._download("/store_00010001", "DSCF0001.RAF")
    p2 = b._download("/store_00010001", "DSCF0002.RAF")
    assert os.path.basename(p1) == "R0034_645_Portra400_001_RED.RAF"
    assert os.path.basename(p2) == "DSCF0002.RAF"


# --------------------------------------------------------------------------- #
# the Start window and the main window
# --------------------------------------------------------------------------- #
def test_start_window_fields(tmp_path):
    from widgets.tether_start_dialog import TetherStartDialog
    d = TetherStartDialog(None, folder=str(tmp_path), roll="R0034", film_format="645",
                          film="Portra 400", films=["Portra 400", "HP5"])
    o = d.options(True)
    assert (o["roll"], o["format"], o["film"]) == ("R0034", "645", "Portra 400")
    d.roll_edit.setText(" my roll/2 ")
    d.format_combo.setCurrentIndex(0)
    d.film_combo.setEditText("")
    o = d.options(True)
    assert (o["roll"], o["format"], o["film"]) == ("myroll2", "", "")
    d.roll_edit.setText("")
    assert d.options(True)["roll"] == "R0001"


def test_session_defaults_and_new_roll_moves_it_on(tmp_path, monkeypatch):
    from ui.main_window import MainWindow
    from PySide6.QtCore import QSettings
    w = MainWindow()
    st = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    w._settings = st
    assert w._capture_defaults()["roll"] == "R0001"
    w._set_capture_session("R0034", "645", "Portra 400")
    d = w._capture_defaults()
    assert (d["roll"], d["format"], d["film"], d["films"]) == \
        ("R0035", "645", "Portra 400", ["Portra 400"])
    w.new_roll()
    assert w.capture_session == {"roll": "R0035", "format": "645", "film": "Portra 400"}
    w.new_roll(confirmed=True, session=True)          # Close Session keeps the roll
    assert w.capture_session["roll"] == "R0035"
    _KEEP.append(w)                 # closing would ask Quit?


_KEEP = []


# --------------------------------------------------------------------------- #
# Capture names and logs a USB trichrome shot
# --------------------------------------------------------------------------- #
def test_capture_names_and_logs_a_usb_trichrome_shot(tmp_path, monkeypatch):
    from test_capture_routes import mon_usb, _wait  # noqa: F401
    from core.ccr_backend import ccr_backend
    gen = mon_usb.__wrapped__(tmp_path, monkeypatch)
    mon, feed, light, usb, mw, asked, lm = next(gen)
    try:
        lm._settings().setValue("live/shutter", "usb")
        usb.opened()
        _app.processEvents()
        monkeypatch.setattr(ccr_backend, "scan_type", "trichrome", raising=False)
        mon._apply_scan_type("trichrome")
        light.open_port("/dev/cu.usbmodem1")
        light.link.tick()
        mw._tether_active = True
        mw._tether_folder = mw.folder
        mw.capture_session = {"roll": "R0034", "format": "645", "film": "Portra 400"}
        mon._refresh_status_strip()
        assert mon.roll_note.text() == "Roll R0034 · 120: 6x4.5 · Portra 400 · next shot 001"
        mon.shoot()
        assert mon.sequence.running
        for word in ("RED", "GREEN", "BLUE"):
            n = len(getattr(usb, "names", []))
            assert _wait(lambda: len(getattr(usb, "names", [])) > n)
            name = usb.names[-1]
            assert name == f"R0034_645_Portra400_001_{word}"
            usb.file_saved.emit(os.path.join(mw.folder, name + ".RAF"))
            _app.processEvents()
        assert _wait(lambda: not mon.sequence.running)
        (e,) = cl.read_log(mw.folder)
        assert e["files"] == {c: f"R0034_645_Portra400_001_{c.upper()}.RAF"
                              for c in ("red", "green", "blue")}
        assert e["done"] and e["route"] == "usb" and e["scan_type"] == "trichrome"
        assert "next shot 002" in mon.roll_note.text()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_no_hot_folder_no_names(tmp_path, monkeypatch):
    from test_capture_routes import mon_usb, _wait  # noqa: F401
    gen = mon_usb.__wrapped__(tmp_path, monkeypatch)
    mon, feed, light, usb, mw, asked, lm = next(gen)
    try:
        assert mon.capture_session() == (None, None)
        assert mon._shot_file_stem("red") is None
        assert mon.roll_note.text() == ""
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


# --- 2026-11-09: FreeCCR's names sort red, green, blue -----------------------
def test_slot_order_and_the_tether_grouper_takes_red_green_blue():
    from core.scan_type import TripletGrouper
    from core import triplet_plan as tp
    assert cl.slot_order("/x/R0034_645_Portra400_013_GREEN.RAF") == \
        ("r0034_645_portra400_013", 1, 0)
    assert cl.slot_order("R1_002_RED_1.RAF") == ("r1_002", 0, 1)
    assert cl.slot_order("DSCF0001.RAF") is None and cl.slot_order("R1_002_WHITE.RAF") is None
    names = [f"/h/R0034_645_Portra400_{n:03d}_{c}.RAF"
             for n in (1, 2) for c in ("BLUE", "RED", "GREEN")]
    g = TripletGrouper()
    for n in reversed(names):                       # any arrival order
        g.add(n)
    trips = g.take()
    words = [[p.rsplit("_", 1)[1][:-4] for p in t] for t in trips]
    assert words == [["RED", "GREEN", "BLUE"], ["RED", "GREEN", "BLUE"]]
    assert [t[0][-11:-8] for t in trips] == ["001", "002"]
    order = sorted(names, key=tp.natural_key)
    assert [p.rsplit("_", 1)[1][:-4] for p in order][:3] == ["RED", "GREEN", "BLUE"]
    # camera names keep their old order
    g2 = TripletGrouper()
    for n in ("/h/DSCF0003.RAF", "/h/DSCF0001.RAF", "/h/DSCF0002.RAF"):
        g2.add(n)
    assert [p[-12:] for p in g2.take()[0]] == ["DSCF0001.RAF", "DSCF0002.RAF", "DSCF0003.RAF"]
