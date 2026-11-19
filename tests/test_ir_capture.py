#!/usr/bin/env python3
"""The IR shot for dust: Capture takes it after the colour shot(s), names it
_4IR, logs it, and an import / Hot Folder Tether pairs it with its frame
instead of loading it (spec/ir-dust-and-flat-sets.md)."""
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import cv2  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import capture_light as cl  # noqa: E402
from core import capture_log  # noqa: E402
from core import capture_sequence as cs  # noqa: E402
from test_live_capture_light import Clock, FakeLight, Timers, PRESET  # noqa: E402

TIMING = {"focus_ms": 150, "pulse_ms": 300, "hold_ms": 200, "period_ms": 0}
IR = {"shutter": "8s", "iso": "3200"}


class _Light(FakeLight):
    def __init__(self, levels):
        super().__init__()
        self._lv = dict(levels)

    def set_levels(self, lv):
        super().set_levels(lv)
        self._lv = dict(lv)

    def levels(self):
        return dict(self._lv)


class _Shutter:
    def __init__(self, light):
        self.light = light
        self.fired = []

    def fire(self, exposure=None):
        self.fired.append(exposure)
        self.light.log.append(("fire", exposure))


def _seq(levels):
    clock = Clock()
    timers = Timers(clock)
    light = _Light(levels)
    done = []
    seq = cs.CaptureSequence(light, schedule=timers.schedule, clock=clock,
                             on_finished=done.append)
    return seq, light, clock, timers, done, _Shutter(light)


def _run(seq, timers, clock, shots):
    for _ in range(shots):
        timers.run(until=clock.t + 0.2)
        clock.t += 1.0
        seq.frame_done("f")
    timers.run(until=clock.t + 0.2)


def test_a_trichrome_shot_ends_with_the_ir_shot():
    seq, light, clock, timers, done, sh = _seq(cl.OFF)
    assert seq.start(TIMING, PRESET, trichrome=True, shutter=sh, ir=dict(IR))
    _run(seq, timers, clock, 4)
    assert done == ["done"]
    assert sh.fired == [None, None, None, IR]          # colours at the camera's, IR at its own
    levels = [e[1] for e in light.log if e[0] == "levels"]
    assert levels[3] == dict(cl.OFF, ir=255)          # the IR LED alone
    assert levels[-1]["w"] and not levels[-1]["ir"]    # then white, as after any trichrome
    order = [e[0] for e in light.log]
    assert order == ["levels", "fire"] * 4 + ["levels"]


def test_a_single_shot_then_ir_puts_the_light_back():
    white = dict(cl.OFF, w=200)
    seq, light, clock, timers, done, sh = _seq(white)
    assert seq.start(TIMING, PRESET, trichrome=False, shutter=sh, ir=dict(IR))
    _run(seq, timers, clock, 2)
    assert done == ["done"] and sh.fired == [None, IR]
    levels = [e[1] for e in light.log if e[0] == "levels"]
    assert levels == [dict(cl.OFF, ir=255), white]     # IR alone, then as it was
    # the IR shot needs the light, a plain single shot doesn't
    light.open = False
    done.clear()
    assert not seq.start(TIMING, PRESET, trichrome=False, shutter=sh, ir=dict(IR))
    assert seq.start(TIMING, PRESET, trichrome=False, shutter=sh)


def test_no_ir_without_a_camera_shutter():
    seq, light, clock, timers, done, sh = _seq(cl.OFF)
    assert seq.start(TIMING, PRESET, trichrome=True, ir=dict(IR))
    assert seq._ir is None and "ir" not in seq._queue


def test_names_slots_and_pairing(tmp_path):
    stem = capture_log.shot_stem("R0011", "645", "XP2", 1, "ir")
    assert stem == "R0011_645_XP2_001_4IR"
    assert sorted(["R1_001_4IR", "R1_001_3BLUE", "R1_001_1RED", "R1_001_2GREEN"]) == \
        ["R1_001_1RED", "R1_001_2GREEN", "R1_001_3BLUE", "R1_001_4IR"]
    assert capture_log.slots_of({"scan_type": "trichrome", "ir": True}) == \
        ("red", "green", "blue", "ir")
    assert capture_log.slots_of({"scan_type": "bw", "ir": True}) == ("bw", "ir")
    assert capture_log.slots_of({"scan_type": "bw"}) == ("bw",)
    d = str(tmp_path)
    frames = [os.path.join(d, f"R0011_645_XP2_001_{c}.raf") for c in ("1RED", "2GREEN", "3BLUE")]
    ir1 = os.path.join(d, "R0011_645_XP2_001_4IR.raf")
    ir2 = os.path.join(d, "R0011_645_XP2_002_4IR.raf")
    keep, irs = capture_log.split_ir(frames + [ir1, ir2])
    assert keep == frames and irs == [ir1, ir2]
    assert capture_log.ir_for(frames, irs, look_in_folder=False) == ir1
    assert capture_log.ir_for([os.path.join(d, "R0011_645_XP2_002_BW.raf")], irs,
                              look_in_folder=False) == ir2
    assert capture_log.ir_for([os.path.join(d, "DSCF0001.RAF")], irs, look_in_folder=False) is None
    # beside the frame on disk
    open(ir1, "wb").close()
    assert capture_log.ir_for(frames) == ir1
    assert capture_log.next_shot(d, "R0011") == 2


def test_a_camera_named_ir_shot_is_paired_by_the_log(tmp_path):
    d = str(tmp_path)
    capture_log.begin_shot(d, {"roll": "R0002", "shot": 1, "scan_type": "bw", "ir": True})
    assert capture_log.add_file(os.path.join(d, "DSCF0101.RAF"), now=0)
    assert capture_log.add_file(os.path.join(d, "DSCF0102.RAF"), now=0)
    capture_log.end_shot("done", now=0)
    capture_log.clear()
    assert capture_log.read_log(d)[-1]["files"] == {"bw": "DSCF0101.RAF", "ir": "DSCF0102.RAF"}
    a, b = os.path.join(d, "DSCF0101.RAF"), os.path.join(d, "DSCF0102.RAF")
    assert capture_log.is_ir_file(b) and not capture_log.is_ir_file(a)
    assert capture_log.ir_for([a], [b], look_in_folder=False) == b


def test_open_pairs_the_ir_shot_and_doesnt_load_it(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "rgb_merge_mode", False)
    monkeypatch.setattr(ccr_backend, "images", [])
    frame = str(tmp_path / "R0003_645_XP2_005_BW.png")
    ir = str(tmp_path / "R0003_645_XP2_005_4IR.png")
    other = str(tmp_path / "R0003_645_XP2_006_BW.png")
    for p in (frame, ir, other):
        cv2.imwrite(p, np.full((40, 60, 3), 120, np.uint8))
    import core.catalog as catalog
    monkeypatch.setattr(catalog, "load_catalog", lambda: None)
    n = ccr_backend.load_images_from_files([frame, ir, other])
    names = sorted(os.path.basename(i.file_path) for i in ccr_backend.images)
    assert n == 2 and names == ["R0003_645_XP2_005_BW.png", "R0003_645_XP2_006_BW.png"]
    by = {os.path.basename(i.file_path): i for i in ccr_backend.images}
    assert by["R0003_645_XP2_005_BW.png"].ir_source == os.path.normpath(ir)
    assert by["R0003_645_XP2_006_BW.png"].ir_source is None
    # opening the frame alone still finds its IR shot beside it
    ccr_backend.load_images_from_files([frame])
    assert ccr_backend.images[0].ir_source == os.path.normpath(ir)


def test_hot_folder_ir_waits_for_its_frame(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    from ui.main_window import MainWindow
    found = []

    class W:
        _tether_ir = MainWindow._tether_ir
        _take_waiting_ir = MainWindow._take_waiting_ir
        dust_panel = types.SimpleNamespace(find_ir=found.append)

    monkeypatch.setattr(ccr_backend, "images", [])
    w = W()
    d = str(tmp_path)
    ir = os.path.join(d, "R0004_001_4IR.raf")
    w._tether_ir(ir)                                   # before its frame
    assert w._tether_ir_waiting == [ir]
    img = types.SimpleNamespace(merge_sources=[os.path.join(d, f"R0004_001_{c}.raf")
                                               for c in ("1RED", "2GREEN", "3BLUE")],
                                file_path=os.path.join(d, "R0004_001_1RED.raf"),
                                ir_source=None, dust_spots=[])
    assert w._take_waiting_ir(img)                     # _on_capture's call
    assert img.ir_source == os.path.normpath(ir) and found == [img]
    assert w._tether_ir_waiting == []
    # after its frame
    img2 = types.SimpleNamespace(merge_sources=None, file_path=os.path.join(d, "R0004_002_BW.raf"),
                                 ir_source=None, dust_spots=[])
    ccr_backend.images.append(img2)
    w._tether_ir(os.path.join(d, "R0004_002_4IR.raf"))
    assert img2.ir_source == os.path.normpath(os.path.join(d, "R0004_002_4IR.raf"))
    assert w._tether_ir_waiting == []


def test_captures_ir_row_defaults_and_names(monkeypatch):
    import test_live_monitor as T
    lm = T.lm
    s = lm._settings()
    for k in ("live/ir_shot", "live/ir_shutter", "live/ir_iso"):
        s.remove(k)
    mon, feed, light, dev = T._monitor()
    try:
        mon._on_exposure_choices({"shutter": ["1/60", "1s", "5s", "8s", "10s", "30s"],
                                  "iso": ["-1", "100", "3200", "4000"]})
        assert mon.ir_shutter_combo.currentData() == "8s"
        assert mon.ir_iso_combo.currentData() == "3200"
        assert mon.ir_iso_combo.findData("-1") < 0          # no Auto for the IR shot
        assert mon._ir_shot() is None                       # off by default
        mon.ir_shot_cb.setChecked(True)
        assert mon._ir_shot() == {"shutter": "8s", "iso": "3200",
                                  "levels": dict(cl.OFF, ir=255)}
        mon.ir_shutter_combo.setCurrentIndex(mon.ir_shutter_combo.findData("10s"))
        mon.ir_shutter_combo.activated.emit(mon.ir_shutter_combo.currentIndex())
        assert s.value("live/ir_shutter") == "10s"
        mon._on_exposure_choices({"shutter": ["8s", "10s"], "iso": ["3200"]})
        assert mon.ir_shutter_combo.currentData() == "10s"  # remembered
        mon._log_shot = {"roll": "R0007", "format": "645", "film": "XP2", "shot": 3,
                         "scan_type": "trichrome"}
        assert mon._shot_file_stem("ir") == "R0007_645_XP2_003_4IR"
        assert mon._shot_file_stem("g") == "R0007_645_XP2_003_2GREEN"
    finally:
        mon.ir_shot_cb.setChecked(False)
        for k in ("live/ir_shot", "live/ir_shutter", "live/ir_iso"):
            s.remove(k)
        T._close(mon)
