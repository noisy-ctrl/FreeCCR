#!/usr/bin/env python3
"""Capture's frame guide is saved with the shot in the capture log, so Open
and Merge Anyway find it later (spec/guide-in-log.md)."""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core import capture_guide as cg  # noqa: E402
from core import capture_log as clog  # noqa: E402
from test_auto_crop import scan_645  # noqa: E402
from test_guide_crop import _guide_from, _live_of  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    cg.clear()
    clog.clear()
    yield
    cg.clear()
    clog.clear()


def _shot(folder, files, record, roll="R0007", shot=3, scan="colour"):
    entry = {"v": 1, "roll": roll, "shot": shot, "scan_type": scan, "files": files,
             "done": True}
    g = cg.save_for_log(str(folder), roll, shot, record)
    if g is not None:
        entry["guide"] = g
    clog.append(str(folder), entry)
    return entry


def test_saved_with_the_shot_and_read_back(tmp_path):
    raw, truth = scan_645()
    g = _guide_from(truth, inset=(0, 0, 40, 0))
    rec = cg.make_record(g, _live_of(raw), following=True)
    e = _shot(tmp_path, {"white": "R0007_003_WHITE.RAF"}, rec)
    assert e["guide"]["live"] == ".freeccr-guides/R0007_003.png"
    assert (tmp_path / ".freeccr-guides" / "R0007_003.png").exists()
    json.dumps(e)                                        # a plain JSON line
    back = cg.record_from_log([str(tmp_path / "R0007_003_WHITE.RAF")])
    assert back is not None and back["from_log"] and back["following"]
    for k in ("cx", "cy", "w", "h", "angle"):
        assert back["guide"][k] == pytest.approx(g[k])
    assert back["format"] == "645" and back["live_size"] == tuple(rec["live_size"])
    assert np.array_equal(back["live"], rec["live"])
    assert cg.save_for_log(str(tmp_path), "R1", 1, None) is None


def test_found_by_any_file_of_the_shot_and_only_with_a_guide(tmp_path):
    raw, truth = scan_645()
    rec = cg.make_record(_guide_from(truth), _live_of(raw))
    files = {"red": "a_RED.RAF", "green": "a_GREEN.RAF", "blue": "a_BLUE.RAF"}
    _shot(tmp_path, files, rec, scan="trichrome")
    _shot(tmp_path, {"white": "b.RAF"}, None, shot=4)
    logs = {}
    assert cg.record_from_log([str(tmp_path / "a_BLUE.RAF")], logs) is not None
    assert cg.record_from_log([str(tmp_path / "x.RAF"), str(tmp_path / "a_GREEN.RAF")],
                              logs) is not None
    assert list(logs) == [os.path.dirname(os.path.abspath(tmp_path / "x.RAF"))]
    assert cg.record_from_log([str(tmp_path / "b.RAF")]) is None       # no guide
    assert cg.record_from_log([str(tmp_path / "nothing.RAF")]) is None
    os.remove(tmp_path / ".freeccr-guides" / "R0007_003.png")
    assert cg.record_from_log([str(tmp_path / "a_RED.RAF")]) is None   # picture gone


def test_attach_lines_it_up_and_prefers_the_sessions_record(tmp_path):
    from test_auto_crop import _image
    raw, truth = scan_645()
    g = _guide_from(truth, inset=(0, 0, 40, 0))
    img = _image(tmp_path, raw)
    _shot(tmp_path, {"white": os.path.basename(img.file_path)},
          cg.make_record(g, _live_of(raw)))
    assert cg.attach(img, [img.file_path])
    got = img.capture_guide
    assert got["how"] == "matched"
    for k in ("cx", "cy", "w", "h"):
        assert got[k] == pytest.approx(g[k], abs=0.003), k
    # the session's own record wins over the log's
    g2 = _guide_from(truth, inset=(30, 0, 0, 0))
    cg.remember(img.file_path, record=cg.make_record(g2, _live_of(raw)))
    assert cg.attach(img, [img.file_path])
    assert img.capture_guide["cx"] == pytest.approx(g2["cx"], abs=0.003)


def test_tether_worker_falls_back_to_the_log(tmp_path):
    from core.tether_watcher import TetherWatchWorker
    from test_auto_crop import _image
    raw, truth = scan_645()
    img = _image(tmp_path, raw)
    _shot(tmp_path, {"white": os.path.basename(img.file_path)},
          cg.make_record(_guide_from(truth), _live_of(raw)))
    TetherWatchWorker._guide_from_capture(img, [img.file_path])
    assert img.capture_guide is not None


def test_open_attaches_it_and_leaves_other_files_alone(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    raw, truth = scan_645()
    g = _guide_from(truth, inset=(0, 0, 40, 0))
    a = tmp_path / "R0007_003_WHITE.tif"
    b = tmp_path / "other.tif"
    for p in (a, b):
        cv2.imwrite(str(p), cv2.cvtColor(raw, cv2.COLOR_RGB2BGR))
    _shot(tmp_path, {"white": a.name}, cg.make_record(g, _live_of(raw)))
    saved = (ccr_backend.images, ccr_backend.file_paths)
    monkeypatch.setattr(ccr_backend, "rgb_merge_mode", False, raising=False)
    try:
        n = ccr_backend.load_images_from_files([str(a), str(b)])
        assert n == 2
        by = {os.path.basename(i.file_path): i for i in ccr_backend.images}
        got = by[a.name].capture_guide
        assert got is not None and got["how"] == "matched"
        assert got["cx"] == pytest.approx(g["cx"], abs=0.005)
        assert getattr(by[b.name], "capture_guide", None) is None
    finally:
        ccr_backend.images, ccr_backend.file_paths = saved


def test_capture_writes_the_guide_into_its_log_line(tmp_path):
    """Capture's _begin_log_shot puts the shot's guide in the log."""
    from widgets import live_monitor as lm
    raw, truth = scan_645()
    rec = cg.make_record(_guide_from(truth), _live_of(raw))

    class Mon:
        _begin_log_shot = lm.LiveCaptureMonitor._begin_log_shot

        def capture_session(self):
            return str(tmp_path), {"roll": "R0009", "format": "645", "film": "Portra 400"}

        def scan_type(self):
            return "colour"

        def _refresh_roll_note(self, n):
            pass
    m = Mon()
    m._begin_log_shot({"tri": False, "usb": True, "exposures": None, "guide": rec})
    e = clog.read_log(str(tmp_path))[-1]
    assert e["roll"] == "R0009" and e["guide"]["live"] == ".freeccr-guides/R0009_001.png"
    clog.add_file(str(tmp_path / "R0009_645_Portra400_001_WHITE.RAF"))
    assert cg.record_from_log([str(tmp_path / "R0009_645_Portra400_001_WHITE.RAF")]) is not None
    m._begin_log_shot({"tri": False, "usb": True, "exposures": None, "guide": None})
    assert "guide" not in clog.read_log(str(tmp_path))[-1]
