#!/usr/bin/env python3
"""Shoot with a film base (spec/film-base-exposure.md): the sequence lights
each colour at its film base's levels and hands its exposure to the shutter;
the USB worker sets an exposure and fires in one turn, refusing to fire on a
value the camera didn't take, and holds live view when asked."""

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import camera_backends as cb  # noqa: E402
from core import capture_light as cl  # noqa: E402
from core import capture_sequence as cs  # noqa: E402
from core.usb_camera import UsbCamera  # noqa: E402
from test_capture_routes import _wait  # noqa: E402
from test_live_capture_light import Clock, FakeLight, Timers, PRESET  # noqa: E402

TIMING = {"focus_ms": 150, "pulse_ms": 300, "hold_ms": 200, "period_ms": 0}


def solo(c, v):
    lv = dict(cl.OFF)
    lv[c] = v
    return lv


EXPOSURES = {"r": {"shutter": "1/3", "iso": "100", "levels": solo("r", 255)},
             "g": {"shutter": "1/2.5", "iso": "100", "levels": solo("g", 255)},
             "b": {"shutter": "1s", "iso": "100", "levels": solo("b", 180)}}


class ExpShutter:
    def __init__(self, clock, light):
        self.clock, self.light = clock, light
        self.fired = []

    def fire(self, exposure=None):
        self.fired.append(exposure)
        self.light.log.append(("fire", exposure))


def _useq():
    clock = Clock()
    timers = Timers(clock)
    light = FakeLight()
    done, steps = [], []
    seq = cs.CaptureSequence(light, schedule=timers.schedule, clock=clock,
                             on_finished=done.append,
                             on_step=lambda ch, ms: steps.append((ch, ms)))
    return seq, light, clock, timers, done, steps, ExpShutter(clock, light)


# --------------------------------------------------------------------------- #
# the sequence
# --------------------------------------------------------------------------- #
def test_each_colour_lights_at_its_levels_and_fires_with_its_exposure():
    seq, light, clock, timers, done, steps, sh = _useq()
    assert seq.start(TIMING, PRESET, trichrome=True, shutter=sh, exposures=EXPOSURES)
    for _ in range(3):
        timers.run(until=clock.t + 0.2)
        clock.t += 4.5
        seq.frame_done("f")
    timers.run(until=clock.t + 0.2)
    assert done == ["done"]
    assert sh.fired == [{"shutter": "1/3", "iso": "100"}, {"shutter": "1/2.5", "iso": "100"},
                        {"shutter": "1s", "iso": "100"}]
    levels = [e[1] for e in light.log if e[0] == "levels"]
    assert levels[:3] == [solo("r", 255), solo("g", 255), solo("b", 180)]
    assert levels[3] == {"r": 0, "g": 0, "b": 0, "w": 230, "ir": 0}       # back to white
    order = [e[0] for e in light.log]
    assert order == ["levels", "fire"] * 3 + ["levels"]
    est = cs.settle_ms(TIMING) + cs.USB_SHOT_EST_MS
    assert [ms for _ch, ms in steps] == [pytest.approx(est + 1000 / 3),
                                         pytest.approx(est + 400), pytest.approx(est + 1000)]


def test_the_file_timeout_waits_for_the_exposure_too():
    seq, light, clock, timers, done, steps, sh = _useq()
    long = {"w": {"shutter": "20s", "iso": "100", "levels": None}}
    t0 = clock.t
    assert seq.start(TIMING, PRESET, trichrome=False, shutter=sh, exposures=long)
    timers.run(until=t0 + cs.FILE_TIMEOUT_S + 5)
    assert done == [] and seq.running                    # 30 s + 20 s
    timers.run()
    assert done == ["timeout"]
    assert clock.t == pytest.approx(t0 + 0.15 + cs.FILE_TIMEOUT_S + 20)


def test_a_single_shot_sets_its_levels_only_with_the_light_there():
    seq, light, clock, timers, done, steps, sh = _useq()
    one = {"w": {"shutter": "1/4", "iso": "100", "levels": {"r": 0, "g": 0, "b": 0,
                                                             "w": 200, "ir": 0}}}
    assert seq.start(TIMING, PRESET, trichrome=False, shutter=sh, exposures=one)
    timers.run(until=clock.t + 0.2)
    seq.frame_done("f")
    assert sh.fired == [{"shutter": "1/4", "iso": "100"}]
    assert ("levels", one["w"]["levels"]) in light.log
    seq, light, clock, timers, done, steps, sh = _useq()
    light.open = False
    assert seq.start(TIMING, PRESET, trichrome=False, shutter=sh, exposures=one)
    timers.run(until=clock.t + 0.2)
    assert sh.fired == [{"shutter": "1/4", "iso": "100"}]
    assert not [e for e in light.log if e[0] == "levels"]


def test_no_exposures_is_exactly_the_old_shoot():
    seq, light, clock, timers, done, steps, sh = _useq()
    calls = []
    sh.fire = lambda *a: calls.append(a)                 # an old-style fire()
    assert seq.start(TIMING, PRESET, trichrome=True, shutter=sh)
    for _ in range(3):
        timers.run(until=clock.t + 0.2)
        seq.frame_done("f")
    assert calls == [(), (), ()]
    levels = [e[1] for e in light.log if e[0] == "levels"]
    assert levels[:3] == [cl.solo(PRESET, c) for c in "rgb"]
    # exposures without a camera shutter (the Scanlight cable) are ignored
    seq2, light2, clock2, timers2, done2, steps2, _sh = _useq()
    assert seq2.start(TIMING, PRESET, trichrome=True, exposures=EXPOSURES)
    assert seq2.exposures is None


# --------------------------------------------------------------------------- #
# the USB worker
# --------------------------------------------------------------------------- #
def _worker(tmp_path):
    fake = cb.FakeBackend()
    usb = UsbCamera(backends=[fake])
    got = {"fired": [], "failed": [], "files": [], "states": []}
    usb.fired.connect(lambda: got["fired"].append(1))
    usb.fire_failed.connect(got["failed"].append)
    usb.file_saved.connect(got["files"].append)
    usb.state.connect(lambda k, t: got["states"].append(k))
    usb.start()
    usb.set_dest(str(tmp_path))
    usb.open("usb:fake:0")
    assert _wait(lambda: "open" in got["states"])
    return fake, usb, got


def test_fire_with_an_exposure_sets_it_then_fires(tmp_path):
    fake, usb, got = _worker(tmp_path)
    try:
        usb.fire({"shutter": "1/30", "iso": "200"})
        assert _wait(lambda: got["files"])
        assert fake.set_calls == [{"shutter": "1/30", "iso": "200"}]
        assert fake.settings == {"shutter": "1/30", "iso": "200"} and fake.fired == 1
        # a value the camera doesn't take: said, and nothing fired
        usb.fire({"shutter": "1/3"})
        assert _wait(lambda: got["failed"])
        assert got["failed"][-1] == "The camera didn't take 1/3 (not offered)."
        time.sleep(0.2)
        assert fake.fired == 1
        usb.fire({"iso": "6400"})
        assert _wait(lambda: len(got["failed"]) == 2)
        assert got["failed"][-1].startswith("The camera didn't take ISO 6400")
        # a plain fire() is unchanged
        usb.fire()
        assert _wait(lambda: fake.fired == 2)
        assert len(fake.set_calls) == 3
    finally:
        usb.stop()
        usb.wait(3000)


def test_live_view_can_be_held(tmp_path):
    fake, usb, got = _worker(tmp_path)
    try:
        usb.set_live(True)
        assert _wait(lambda: fake.frames > 2)
        usb.pause_live(True)
        time.sleep(0.15)
        n = fake.frames
        time.sleep(0.3)
        assert fake.frames == n                          # no preview asked while held
        usb.pause_live(False)
        assert _wait(lambda: fake.frames > n)
    finally:
        usb.stop()
        usb.wait(3000)


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #
from PySide6.QtCore import QSettings, QTimer, Signal  # noqa: E402

from core import base_exposure as be  # noqa: E402
from test_capture_routes import FakeUsb, _MW  # noqa: E402
from test_film_base_exposure import SPEEDS, ISOS, CEILING  # noqa: E402

K = {"r": 2.5, "g": 2.1, "b": 0.83}          # base level per ISO-100 second at full LED


class FilmCam(FakeUsb):
    """A USB camera with a linear sensor looking at film base."""
    exposure_choices = Signal(dict)
    exposure_set = Signal(dict)

    def __init__(self, mon_levels):
        super().__init__()
        self.levels_now = mon_levels          # () -> the light's levels at the shot
        self.shots = {}                       # path -> (levels, exposure)
        self.fires, self.dests, self.holds, self.sets = [], [], [], []
        self.n = 0
        self.fail_next = None

    def set_dest(self, d):
        super().set_dest(d)
        self.dests.append(d)

    def pause_live(self, on):
        self.holds.append(bool(on))

    def set_exposure(self, field, value):
        self.sets.append((field, value))

    def fire(self, exposure=None):
        self.fires.append(exposure)
        self.log.append("fire")
        if self.fail_next:
            text, self.fail_next = self.fail_next, None
            QTimer.singleShot(5, lambda: self.fire_failed.emit(text))
            return
        self.n += 1
        path = os.path.join(self.dest or "", f"DSCF{self.n:04d}.RAF")
        with open(path, "wb") as f:
            f.write(b"raf")
        self.shots[path] = (dict(self.levels_now()), dict(exposure or {}))
        QTimer.singleShot(5, lambda: self.file_saved.emit(path))

    def reading(self, path, rect):
        levels, exp = self.shots[path]
        e = be.exposure_of(exp["shutter"], exp["iso"])
        out = {}
        for c, ch in (("r", "R"), ("g", "G"), ("b", "B")):
            out[ch] = min(CEILING, K[c] * e * levels.get(c, 0) / 255.0)
        if levels.get("w"):
            for c, ch in (("r", "R"), ("g", "G"), ("b", "B")):
                out[ch] = min(CEILING, K[c] * e * levels["w"] / 255.0)
        return be.Reading(levels=out, spreads={ch: 0.01 for ch in "RGB"})


@pytest.fixture
def film_mon(tmp_path, monkeypatch):
    from widgets import live_monitor as lm
    from test_live_monitor import FakeFeed, _light
    path = str(tmp_path / "live.ini")
    monkeypatch.setattr(lm, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "scan_type", "trichrome", raising=False)
    feed = FakeFeed()
    light, dev, made = _light()
    holder = {}
    usb = FilmCam(lambda: holder["mon"]._levels)
    hot = tmp_path / "hot"
    hot.mkdir()
    mw = _MW(str(hot))
    mon = lm.LiveCaptureMonitor(main_window=mw, feed=feed, light=light, usb=usb)
    holder["mon"] = mon
    mon._start_hot_folder = lambda from_monitor=False: mw.enter_tethering(from_monitor)
    mon._read_patch = usb.reading
    mon._timing = cs.clean_timing({"focus_ms": 0, "period_ms": 0})
    mon.resize(1300, 820)
    mon.show()
    _app.processEvents()
    light.open_port("/dev/cu.usbmodem1")
    light.link.tick()
    usb.opened("Fujifilm GFX50S II")
    usb.exposure_choices.emit({"shutter": SPEEDS, "iso": ISOS})
    usb.exposure_changed.emit({"shutter": "1/8", "iso": "250"})
    mon._apply_scan_type("trichrome")
    _app.processEvents()
    yield mon, usb, light, lm, path
    mon.close()
    _app.processEvents()


def test_the_film_base_rows_come_with_the_exposure_row(film_mon):
    mon, usb, light, lm, path = film_mon
    assert mon.film_base_box.isVisibleTo(mon)
    assert mon.film_base_combo.currentText() == "No film base yet"
    assert mon.film_base_note.text().startswith("No film base yet")
    assert not mon.film_base_use.isEnabled()
    usb.exposure_choices.emit({})
    assert not mon.film_base_box.isVisibleTo(mon)


def test_measuring_saves_a_film_base_and_puts_everything_back(film_mon, tmp_path):
    mon, usb, light, lm, path = film_mon
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    before = dict(mon._levels)
    mon._ask_film_base_name = lambda default: "Portra 400 · Oct"
    mon.measure_btn.click()
    assert mon._measure is not None and mon.measure_btn.text() == "Stop Measuring"
    assert not mon.shoot_btn.isEnabled()
    temp = mon._measure["temp"]
    assert usb.dests[-1] == temp and usb.holds == [True]
    assert _wait(lambda: mon._measure is None, 20)
    base = mon._film_base()
    assert base["name"] == "Portra 400 · Oct" and base["camera"] == "Fujifilm GFX50S II"
    cols = base["colours"]
    assert (cols["r"]["shutter"], cols["g"]["shutter"], cols["b"]["shutter"]) == \
        ('1/3', '1/2.5', '1s')
    assert all(cols[c]["iso"] == "100" and cols[c]["base"] <= be.TARGET for c in "rgb")
    assert len(usb.fires) == 6 and all(f["iso"] == "100" for f in usb.fires)
    # put back: the light, the camera's exposure, the folder, live view
    assert mon._levels == before
    assert usb.sets[-2:] == [("shutter", "1/8"), ("iso", "250")]
    assert usb.dests[-1] == mon._usb_dest and usb.holds == [True, False]
    assert not os.path.exists(temp)                    # test shots gone
    assert mon.measure_btn.text() == "Measure…" and mon.shoot_btn.isEnabled()
    assert "measured in 6 shots" in mon.capture_caption.text()
    assert mon.film_base_note.text().startswith("R 1/3 · G 1/2.5 · B 1 s, ISO 100")
    assert mon.film_base_use.isEnabled()
    saved = be.normalise(QSettings(path, QSettings.IniFormat).value("live/film_bases"))
    assert saved["bases"][0]["name"] == "Portra 400 · Oct"


def test_shoot_hands_the_exposures_to_each_colour(film_mon):
    mon, usb, light, lm, path = film_mon
    entry = be.entry("Ektar", {c: {"shutter": s, "iso": "100", "base": 0.8,
                                   "levels": dict(cl.solo({"r": 255, "g": 255, "b": 200}, c))}
                               for c, s in (("r", "1/4"), ("g", "1/3"), ("b", "1s"))})
    mon._bases = be.add(mon._bases, entry)
    mon._refresh_film_bases()
    mon._shutter_is_usb = lambda: True
    mon._ensure_hot_folder = lambda: True
    usb.dest = usb.dest or mon._usb_dest
    mon.shoot()
    assert mon.sequence.running and mon.sequence.exposures
    assert usb.holds[-1] is True
    assert "Shooting at “Ektar”" in mon.capture_caption.text()
    assert _wait(lambda: not mon.sequence.running, 20)
    assert usb.fires == [{"shutter": "1/4", "iso": "100"}, {"shutter": "1/3", "iso": "100"},
                         {"shutter": "1s", "iso": "100"}]
    assert usb.holds[-1] is False
    assert usb.sets[-2:] == [("shutter", "1/8"), ("iso", "250")]   # the camera put back
    # unticked: the old Shoot
    mon.film_base_use.setChecked(False)
    usb.fires.clear()
    mon.shoot()
    assert _wait(lambda: not mon.sequence.running, 20)
    assert usb.fires == [None, None, None]


def test_with_no_patch_it_asks_for_a_click_then_measures(film_mon):
    mon, usb, light, lm, path = film_mon
    import numpy as np
    mon._ask_film_base_name = lambda default: "HP5"
    mon.feed.full = None
    mon.measure_btn.click()
    assert mon._measure is None and mon._pending_measure == ("HP5", None)
    assert mon.canvas.mode == "pick" and "Click the film base" in mon.capture_caption.text()
    mon.feed.full = np.zeros((480, 640, 3), np.uint8)
    mon.feed.samples["any"] = (200.0, 150.0, 120.0)
    mon._on_picked(320.0, 24.0)
    assert mon._neg_base_pos == pytest.approx((0.5, 0.05, 640, 480))
    assert _wait(lambda: mon._measure is not None, 5)
    assert mon._measure["rect"] == pytest.approx(be.patch_around(0.5, 0.05, 640, 480))
    assert _wait(lambda: mon._measure is None, 20)
    assert mon._film_base()["name"] == "HP5"


def test_stop_waits_for_the_shot_on_its_way_and_saves_nothing(film_mon):
    mon, usb, light, lm, path = film_mon
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    mon._ask_film_base_name = lambda default: "x"
    mon.measure_btn.click()
    assert _wait(lambda: mon._measure and mon._measure["in_flight"], 5)
    mon._on_escape()
    assert mon._measure is not None and "Stopping after this shot" in mon.capture_caption.text()
    assert _wait(lambda: mon._measure is None, 10)
    assert mon._film_base() is None and len(usb.fires) == 1
    assert usb.holds == [True, False] and usb.dests[-1] == mon._usb_dest
    assert mon.capture_caption.text() == "Measuring stopped; nothing was saved."


def test_a_refused_fire_ends_it_and_puts_things_back(film_mon):
    mon, usb, light, lm, path = film_mon
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    mon._ask_film_base_name = lambda default: "x"
    usb.fail_next = "The camera didn't take 1/8 (Device Busy)."
    mon.measure_btn.click()
    assert _wait(lambda: mon._measure is None, 5)
    assert "Device Busy" in mon.capture_caption.text()
    assert mon._film_base() is None and usb.holds == [True, False]


def test_measuring_one_exposure_uses_the_light_as_it_is(film_mon):
    mon, usb, light, lm, path = film_mon
    mon._apply_scan_type("colour")
    mon.set_levels({"r": 0, "g": 0, "b": 0, "w": 255, "ir": 0})
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    mon._ask_film_base_name = lambda default: "White"
    mon.measure_btn.click()
    assert _wait(lambda: mon._measure is None, 20)
    base = mon._film_base()
    assert set(base["colours"]) == {"w"}
    assert base["colours"]["w"]["shutter"] == "1/3"           # red, the brightest
    assert mon.film_base_note.text() == "1/3, ISO 100 (base 83 %)"
    mon._apply_scan_type("trichrome")
    assert mon.film_base_note.text().startswith("Measured for one exposure")
    assert not mon.film_base_use.isEnabled()


def test_measure_waits_while_shoot_runs(film_mon):
    mon, usb, light, lm, path = film_mon
    mon._shutter_is_usb = lambda: True
    mon._ensure_hot_folder = lambda: True
    mon.shoot()
    assert mon.sequence.running and not mon.measure_btn.isEnabled()
    assert mon.start_measure("x") is False
    assert _wait(lambda: not mon.sequence.running, 20)
    assert mon.measure_btn.isEnabled()


def test_only_a_confident_border_is_read_without_a_click(film_mon, monkeypatch):
    mon, usb, light, lm, path = film_mon
    import numpy as np
    from core import auto_black_point as abp
    mon.feed.full = np.zeros((480, 640, 3), np.uint8)
    found = {"res": abp.RebateResult((200.0, 150.0, 100.0), "medium", "right",
                                     (0.98, 0.2, 1.0, 0.8))}
    monkeypatch.setattr(abp, "find_rebate", lambda frame: found["res"])
    assert mon._film_base_patch() == (None, "")          # one bright edge: maybe bare light
    found["res"] = abp.RebateResult((200.0, 150.0, 100.0), "high", "top",
                                    (0.1, 0.0, 0.9, 0.05))
    rect, where = mon._film_base_patch()
    assert where == "the top border"
    assert rect == pytest.approx(be.patch_inset((0.1, 0.0, 0.9, 0.05)))
    mon._neg_base_pos = (0.3, 0.6, 640, 480)               # a click wins
    assert mon._film_base_patch()[1] == "where you clicked"


def test_trichrome_measures_each_led_from_full_whatever_the_preset(film_mon):
    mon, usb, light, lm, path = film_mon
    mon._presets["presets"][mon._presets["selected"]].update(r=120, g=180, b=255)
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    mon._ask_film_base_name = lambda default: "Full"
    mon.measure_btn.click()
    assert _wait(lambda: mon._measure is None, 20)
    cols = mon._film_base()["colours"]
    assert [cols[c]["levels"][c] for c in "rgb"] == [255, 255, 255]
    assert cols["r"]["shutter"] == '1/3'                      # not slowed by the preset's 120


def test_a_colour_at_a_raised_iso_shows_its_iso():
    e = be.normalise({"bases": [be.entry("x", {
        "r": {"shutter": "1/3", "iso": "100", "base": 0.82, "levels": None},
        "g": {"shutter": "1/2.5", "iso": "100", "base": 0.84, "levels": None},
        "b": {"shutter": "1s", "iso": "200", "base": 0.8, "levels": None}})]})["bases"][0]
    assert be.summary(e, True) == ("R 1/3 ISO 100 · G 1/2.5 ISO 100 · B 1 s ISO 200 "
                                   "(base 82 / 84 / 80 %)")


# --------------------------------------------------------------------------- #
# "busy" from a fire ([-110] I/O in progress, the user's first measures on the
# GFX 50S II): the camera may still have taken the picture, so its file is
# looked for before firing again; and no fire straight after connecting
# --------------------------------------------------------------------------- #
def _busy_backend(tmp_path, busy_times, live=False, shoots_anyway=False, announce=True,
                  card=None):
    from test_capture_routes import fake_gp, GPhoto2Error
    gp = fake_gp(card=card if card is not None else {"/": ["store_10000001"],
                                                      "/store_10000001": ([], [])})
    st = gp.state
    clock = Clock()
    slept = []

    def sleep(s):
        slept.append(s)
        clock.t += s

    b = cb.Gphoto2Backend(gp_module=gp, clock=clock, sleep=sleep)
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    clock.t += 2.0                                  # long after connecting
    slept.clear()
    left = {"n": busy_times}
    calls = {"trigger": 0, "capture": 0}
    for name in ("trigger_capture", "capture"):
        orig = getattr(b.camera, name)

        def wrapped(*a, _orig=orig, _name=name):
            calls["trigger" if _name == "trigger_capture" else "capture"] += 1
            if left["n"] > 0:
                left["n"] -= 1
                if shoots_anyway:                    # the picture IS taken
                    st.fired += 1
                    held = st.card["/store_10000001"][0]
                    held.append(f"DSCF{st.fired:04d}.RAF")
                    if announce:
                        st.events.append((gp.GP_EVENT_FILE_ADDED, types.SimpleNamespace(
                            folder="/store_10000001", name=held[-1])))
                raise GPhoto2Error(gp.GP_ERROR_CAMERA_BUSY)
            return _orig(*a)
        setattr(b.camera, name, wrapped)
    b._live_asked = live
    return b, gp, clock, slept, calls


import types  # noqa: E402


def test_busy_but_shot_is_one_picture_found_by_its_event(tmp_path, capsys):
    b, gp, clock, slept, calls = _busy_backend(tmp_path, 1, shoots_anyway=True)
    b.fire()
    assert calls["trigger"] == 1 and gp.state.fired == 1     # never a second picture
    assert b._pending == [("/store_10000001", "DSCF0001.RAF")]
    out = capsys.readouterr().out
    assert "answered busy" in out and "took the shot" in out
    assert b.shot_pending() and b._errors == 0


def test_busy_but_shot_with_no_event_is_found_in_the_cameras_memory(tmp_path):
    b, gp, clock, slept, calls = _busy_backend(tmp_path, 1, shoots_anyway=True,
                                               announce=False)
    b.fire()
    assert calls["trigger"] == 1 and gp.state.fired == 1
    assert ("/store_10000001", "DSCF0001.RAF") in b._pending
    # the live-view capture call is covered the same way
    b, gp, clock, slept, calls = _busy_backend(tmp_path, 1, live=True, shoots_anyway=True,
                                               announce=False)
    b.fire()
    assert calls["capture"] == 1 and gp.state.fired == 1


def test_busy_and_no_shot_fires_once_more_with_the_capture_call(tmp_path):
    b, gp, clock, slept, calls = _busy_backend(tmp_path, 1)
    t0 = clock.t
    b.fire()
    assert calls == {"trigger": 1, "capture": 1} and gp.state.captures == 1
    assert clock.t - t0 >= cb.FIRE_CONFIRM_S                  # it looked for the shot first
    b.fire()                                                  # and keeps to it
    assert calls == {"trigger": 1, "capture": 2}
    b.open("usb:001,004", str(tmp_path))                      # a new connection starts over
    assert not b._capture_call()


def test_busy_with_no_shot_twice_is_a_failure_not_a_lost_camera(tmp_path):
    b, gp, clock, slept, calls = _busy_backend(tmp_path, 10)
    with pytest.raises(RuntimeError, match="-110"):
        b.fire()
    assert calls["trigger"] + calls["capture"] == 1 + cb.FIRE_BUSY_TRIES
    assert gp.state.fired == 0 and gp.state.captures == 0
    assert b._errors == 0


def test_the_confirm_window_includes_the_exposure(tmp_path):
    b, gp, clock, slept, calls = _busy_backend(tmp_path, 1)
    b._note_shutter("8s")
    t0 = clock.t
    b.fire()
    assert clock.t - t0 >= cb.FIRE_CONFIRM_S + 8.0


def test_no_fire_straight_after_connecting(tmp_path, monkeypatch):
    from test_capture_routes import fake_gp
    monkeypatch.setattr(cb, "RECONNECT_SETTLE_S", 1.0)       # conftest zeroes it
    gp = fake_gp()
    clock = Clock()
    slept = []
    b = cb.Gphoto2Backend(gp_module=gp, clock=clock,
                          sleep=lambda s: (slept.append(s), setattr(clock, "t", clock.t + s)))
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    b.fire()
    assert slept == [pytest.approx(cb.RECONNECT_SETTLE_S)]
    slept.clear()
    clock.t += 5
    b.fire()
    assert slept == []                                        # long since connected
    b._reconnect()                                            # the exposure reconnect
    clock.t += 0.3
    b.fire()
    assert slept == [pytest.approx(cb.RECONNECT_SETTLE_S - 0.3)]


def test_held_shots_come_only_from_the_cameras_own_memory(tmp_path):
    card = {"/": ["store_10000001", "store_00010001"],
            "/store_10000001": (["DSCF0007.RAF", "LIVE0001.JPG", "notes.txt"], []),
            "/store_00010001": (["DSCF0001.RAF"], ["DCIM"])}           # a card: never
    b, gp, clock, slept, calls = _busy_backend(tmp_path, 0, card=card)
    assert b.held_files() == [("/store_10000001", "DSCF0007.RAF")]
    assert b.collect_held() == 1
    assert b._pending == [("/store_10000001", "DSCF0007.RAF")] and b.shot_pending()
    assert b.held_files() == []                               # already queued
    for _ in range(5):
        clock.t += 0.5
        b.poll(0.0)
    assert gp.state.deleted == [("/store_10000001", "DSCF0007.RAF")]
    assert os.path.exists(os.path.join(str(tmp_path), "DSCF0007.RAF"))


def test_a_fujifilm_always_gets_the_capture_call(tmp_path):
    """The user's GFX 50S II log: two capture-call shots in a row, then the
    trigger call refused as busy (InitiateCapture: Device Busy)."""
    from test_capture_routes import fake_gp
    gp = fake_gp(cameras=(("Fuji Fujifilm GFX100", "usb:001,004"),))
    b = cb.Gphoto2Backend(gp_module=gp, clock=Clock(), sleep=lambda s: None)
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    used = []
    b.camera.trigger_capture = lambda: used.append("trigger")
    orig = b.camera.capture
    b.camera.capture = lambda kind: (used.append("capture"), orig(kind))[1]
    for _ in range(3):
        b._live_asked = False                                 # live view not asked
        b.fire()
    assert used == ["capture"] * 3
    assert b._pending                                         # the capture call's file
