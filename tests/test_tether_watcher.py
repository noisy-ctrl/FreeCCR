#!/usr/bin/env python3
"""Tests for watch-folder tethering (spec/camera-tethering.md).

Covers the hardware-free logic: FolderScanner's size-stability gate and
within-tick RAW/JPEG dedupe, extension classification, B/W-point persistence
encode/decode, and a single worker-wiring integration test (decode → emit).
The real camera path is deliberately absent — there is no camera SDK.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.tether_watcher import (  # noqa: E402
    FolderScanner, TetherWatchWorker, is_supported, is_raw,
    encode_bwpoint, decode_bwpoint)
from core.ccr_backend import ccr_backend  # noqa: E402


# --- extension classification ----------------------------------------------
def test_is_supported():
    assert is_supported("DSC_0001.NEF")
    assert is_supported("scan.jpg")
    assert is_supported("frame.TIFF")
    assert not is_supported("notes.txt")
    assert not is_supported("clip.mp4")
    assert not is_supported("noext")


def test_is_raw():
    assert is_raw("a.cr3")
    assert is_raw("a.NEF")
    assert not is_raw("a.jpg")
    assert not is_raw("a.tiff")
    assert not is_raw("a.png")


# --- FolderScanner: size-stability gate ------------------------------------
def test_new_file_held_until_size_stable():
    s = FolderScanner()
    assert s.feed([("a.nef", 100)]) == []   # first sight: no prior size
    assert s.feed([("a.nef", 200)]) == []   # still growing
    assert s.feed([("a.nef", 200)]) == ["a.nef"]  # stable across a tick
    assert s.feed([("a.nef", 200)]) == []   # handled once, never again


def test_zero_size_never_ready():
    s = FolderScanner()
    assert s.feed([("a.nef", 0)]) == []
    assert s.feed([("a.nef", 0)]) == []


def test_preseeded_files_ignored():
    s = FolderScanner(initial_names=["old.nef"])
    assert s.feed([("old.nef", 100)]) == []
    assert s.feed([("old.nef", 100)]) == []


def test_unsupported_ignored():
    s = FolderScanner()
    assert s.feed([("readme.txt", 50)]) == []
    assert s.feed([("readme.txt", 50)]) == []


# --- FolderScanner: RAW + JPEG dedupe (within a tick) ----------------------
def test_raw_preferred_over_jpeg_same_tick():
    s = FolderScanner()
    s.feed([("shot1.cr3", 1000), ("shot1.jpg", 200)])      # prime sizes
    ready = s.feed([("shot1.cr3", 1000), ("shot1.jpg", 200)])
    assert ready == ["shot1.cr3"]                           # RAW wins
    assert s.feed([("shot1.cr3", 1000), ("shot1.jpg", 200)]) == []  # both seen


def test_distinct_basenames_both_import():
    s = FolderScanner()
    s.feed([("a.nef", 10), ("b.nef", 20)])
    assert sorted(s.feed([("a.nef", 10), ("b.nef", 20)])) == ["a.nef", "b.nef"]


def test_jpeg_only_imports_when_no_raw_sibling():
    s = FolderScanner()
    s.feed([("solo.jpg", 300)])
    assert s.feed([("solo.jpg", 300)]) == ["solo.jpg"]


def test_deleted_file_can_reimport_after_recreate():
    s = FolderScanner()
    s.feed([("a.nef", 100)])
    assert s.feed([("a.nef", 100)]) == ["a.nef"]   # imported once stable
    assert s.feed([]) == []                          # deleted → forgotten
    s.feed([("a.nef", 120)])                         # recreated, same name
    assert s.feed([("a.nef", 120)]) == ["a.nef"]     # re-imports


def test_duplicate_name_coalesced_in_one_tick():
    s = FolderScanner()
    s.feed([("a.nef", 100), ("a.nef", 100)])
    assert s.feed([("a.nef", 100), ("a.nef", 100)]) == ["a.nef"]  # not doubled


# --- B/W point persistence encode/decode -----------------------------------
def test_bwpoint_roundtrip():
    pt = (51234.0, 48010.5, 52001.25)
    enc = encode_bwpoint(pt)
    assert isinstance(enc, str)
    assert decode_bwpoint(enc) == pt


def test_bwpoint_none_and_empty():
    assert encode_bwpoint(None) is None
    assert decode_bwpoint(None) is None
    assert decode_bwpoint("") is None


def test_bwpoint_malformed():
    assert decode_bwpoint("not json") is None
    assert decode_bwpoint("[1, 2]") is None          # wrong length
    assert decode_bwpoint('{"b": 1}') is None         # wrong type
    assert decode_bwpoint('[1, 2, "x"]') is None      # non-numeric


# --- worker wiring: decode → emit captured ---------------------------------
def test_worker_imports_unconverted(tmp_path):
    """With no B/W point set, the worker decodes the file and emits a captured
    CCRImage that is NOT converted."""
    ccr_backend.black_point_bgr = None
    ccr_backend.white_point_bgr = None
    ccr_backend.positive_mode = False

    p = tmp_path / "capture01.png"
    px = (np.random.RandomState(7).rand(120, 180, 3) * 255).astype(np.uint8)
    cv2.imwrite(str(p), px)

    worker = TetherWatchWorker()
    got, errs = [], []
    worker.captured.connect(lambda im: got.append(im))
    worker.captureError.connect(lambda path, msg: errs.append((path, msg)))
    worker.process(str(p))  # direct (same-thread) call → synchronous signals

    assert not errs, errs
    assert len(got) == 1
    assert os.path.normpath(got[0].file_path) == os.path.normpath(str(p))
    assert got[0].converted is False


def test_worker_converts_with_bwpoint(tmp_path):
    """With a B/W point set and Positive mode off, the worker bakes the
    conversion and records conversion_inputs for export/catalog replay."""
    ccr_backend.positive_mode = False
    ccr_backend.black_point_bgr = (60000.0, 60000.0, 60000.0)  # clear base (high)
    ccr_backend.white_point_bgr = (5000.0, 5000.0, 5000.0)     # dense (low)
    try:
        p = tmp_path / "neg.png"
        px = (np.random.RandomState(3).rand(100, 140, 3) * 255).astype(np.uint8)
        cv2.imwrite(str(p), px)

        worker = TetherWatchWorker()
        got = []
        worker.captured.connect(lambda im: got.append(im))
        worker.process(str(p))

        assert len(got) == 1
        img = got[0]
        assert img.converted is True
        assert img.conversion_inputs is not None
        assert img.conversion_inputs["mode"] == "bw"
        bw = img.conversion_inputs["bw"]
        assert tuple(bw[0]) == (60000.0, 60000.0, 60000.0)
        assert tuple(bw[1]) == (5000.0, 5000.0, 5000.0)
    finally:
        ccr_backend.black_point_bgr = None
        ccr_backend.white_point_bgr = None


def test_worker_converts_with_black_point_only(tmp_path):
    """With only a black point set (no white point), the worker still converts
    using the calibrated default slope and records white as None."""
    ccr_backend.positive_mode = False
    ccr_backend.black_point_bgr = (60000.0, 60000.0, 60000.0)  # clear base (high)
    ccr_backend.white_point_bgr = None                         # default slope
    try:
        p = tmp_path / "neg_default.png"
        px = (np.random.RandomState(9).rand(100, 140, 3) * 255).astype(np.uint8)
        cv2.imwrite(str(p), px)

        worker = TetherWatchWorker()
        got = []
        worker.captured.connect(lambda im: got.append(im))
        worker.process(str(p))

        assert len(got) == 1
        img = got[0]
        assert img.converted is True
        assert img.conversion_inputs["mode"] == "bw"
        bw = img.conversion_inputs["bw"]
        assert tuple(bw[0]) == (60000.0, 60000.0, 60000.0)
        assert bw[1] is None       # no white point → default-slope mode
    finally:
        ccr_backend.black_point_bgr = None
        ccr_backend.white_point_bgr = None


def test_worker_positive_mode_skips_conversion(tmp_path):
    """Positive mode → captures import as-is even when a B/W point is set."""
    ccr_backend.positive_mode = True
    ccr_backend.black_point_bgr = (60000.0, 60000.0, 60000.0)
    ccr_backend.white_point_bgr = (5000.0, 5000.0, 5000.0)
    try:
        p = tmp_path / "pos.png"
        px = (np.random.RandomState(4).rand(90, 90, 3) * 255).astype(np.uint8)
        cv2.imwrite(str(p), px)

        worker = TetherWatchWorker()
        got = []
        worker.captured.connect(lambda im: got.append(im))
        worker.process(str(p))

        assert len(got) == 1
        assert got[0].converted is False
    finally:
        ccr_backend.positive_mode = False
        ccr_backend.black_point_bgr = None
        ccr_backend.white_point_bgr = None


# --- capture inherits adjustments from the active image (bug fix #3) --------
def test_capture_inherits_adjustments(tmp_path):
    from ui.main_window import MainWindow
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    ccr_backend.black_point_bgr = None
    ccr_backend.white_point_bgr = None
    ccr_backend.positive_mode = False

    w = MainWindow()
    a_path = tmp_path / "a.png"
    cv2.imwrite(str(a_path), (np.random.RandomState(11).rand(80, 100, 3) * 255).astype(np.uint8))
    a = CCRImage(str(a_path))
    a.adjustment_settings = {"brightness": 25, "contrast": 10}
    a.rotation_angle = 90
    ccr_backend.images.append(a)
    ccr_backend.file_paths.append(a.file_path)
    w.thumbnail_list.append_image_item(0, select=True)   # A becomes current
    assert w.image_preview.current_idx == 0

    w._tether_active = True
    w._tether_folder = str(tmp_path)
    b_path = tmp_path / "b.png"
    cv2.imwrite(str(b_path), (np.random.RandomState(12).rand(80, 100, 3) * 255).astype(np.uint8))
    b = CCRImage(str(b_path))
    w._on_capture(b)

    assert b.adjustment_settings == {"brightness": 25, "contrast": 10}
    assert b.adjustment_settings is not a.adjustment_settings   # isolated copy
    assert b.rotation_angle == 90
    w.stop_tethering()


def test_capture_no_template_when_active_image_is_default(tmp_path):
    from ui.main_window import MainWindow
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    ccr_backend.black_point_bgr = None
    ccr_backend.white_point_bgr = None
    ccr_backend.positive_mode = False

    w = MainWindow()
    a_path = tmp_path / "a.png"
    cv2.imwrite(str(a_path), (np.random.RandomState(13).rand(60, 60, 3) * 255).astype(np.uint8))
    a = CCRImage(str(a_path))   # untouched look
    ccr_backend.images.append(a)
    ccr_backend.file_paths.append(a.file_path)
    w.thumbnail_list.append_image_item(0, select=True)
    assert w._tether_template() is None   # nothing worth inheriting


def test_bw_prompt_fires_once_on_first_new_capture(tmp_path):
    """The B/W-point prompt is flagged after the first genuinely-new capture,
    not for files imported at session start, and only once per session."""
    from ui.main_window import MainWindow
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    ccr_backend.black_point_bgr = None
    ccr_backend.white_point_bgr = None
    ccr_backend.positive_mode = False

    w = MainWindow()
    w._prompt_bwpoint_for_session = lambda: None   # neutralize the modal
    w._tether_active = True
    w._tether_folder = str(tmp_path)
    w._tether_prompted = False

    def _png(name, seed):
        p = tmp_path / name
        cv2.imwrite(str(p), (np.random.RandomState(seed).rand(50, 50, 3) * 255).astype(np.uint8))
        return CCRImage(str(p))

    # A file imported at session start must NOT trigger the prompt.
    existing = _png("existing.png", 1)
    w._tether_import_existing = {os.path.normpath(existing.file_path)}
    w._on_capture(existing)
    assert w._tether_prompted is False

    # The first genuinely-new capture flags the prompt.
    w._on_capture(_png("new1.png", 2))
    assert w._tether_prompted is True

    # A second new capture does not re-flag (single prompt per session).
    w._on_capture(_png("new2.png", 3))
    assert w._tether_prompted is True

    w.stop_tethering()


def test_session_start_clears_bwpoint(tmp_path, monkeypatch):
    """Entering a tethering session clears any prior B/W point so the user must
    re-sample from this roll's lead; the banner reflects the uncalibrated state."""
    from ui.main_window import MainWindow
    from PySide6.QtWidgets import QFileDialog
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    ccr_backend.positive_mode = False
    # A stale point from a "previous roll".
    ccr_backend.black_point_bgr = (60000.0, 60000.0, 60000.0)
    ccr_backend.white_point_bgr = (5000.0, 5000.0, 5000.0)

    w = MainWindow()
    roll = tmp_path / "roll"          # empty → no "import existing?" prompt
    roll.mkdir()
    w._tether_start_options = lambda n, d, m=False: {
        "start": True, "folder": str(roll), "scan_type": "colour",
        "close_open": False, "import_existing": False, "dont_ask": False}
    w.enter_tethering()
    try:
        assert w._tether_active is True
        assert ccr_backend.black_point_bgr is None
        assert ccr_backend.white_point_bgr is None
        assert "No black point" in w.tether_banner._note.text()
    finally:
        w.stop_tethering()


def test_named_hot_folder_tether(tmp_path, monkeypatch):
    """The feature is called Hot Folder Tether in the menu, the banner and
    its messages (spec/camera-tethering.md, renamed 2026-10-24)."""
    from PySide6.QtWidgets import QFileDialog
    from ui.main_window import MainWindow
    from widgets.tether_banner import TetherBanner
    w = MainWindow()
    assert w.tether_action.text() == "Hot Folder Tether…"
    monkeypatch.setattr(w._settings, "setValue", lambda *a, **k: None)
    w._tether_start_options = lambda n, d, m=False: {
        "start": True, "folder": str(tmp_path), "scan_type": None,
        "close_open": False, "import_existing": False, "dont_ask": False}
    w.enter_tethering()
    try:
        assert w.tether_action.text() == "Stop Hot Folder Tether"
        assert w.tether_banner._status.text().startswith("Hot folder tether: ")
    finally:
        w.stop_tethering()
    assert w.tether_action.text() == "Hot Folder Tether…"
    b = TetherBanner()
    b.set_status("Roll 1", 3)
    assert b._status.text() == "Hot folder tether: “Roll 1”  ·  3 captured"
    assert "Tethering" not in b._status.text()
