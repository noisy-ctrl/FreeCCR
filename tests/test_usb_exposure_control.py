#!/usr/bin/env python3
"""Shutter speed and ISO from the Live Capture Monitor
(spec/usb-exposure-control.md): the camera's lists, a direct setting with
live view off, one reconnect when the camera refuses while live view runs
(GFX 50S II, tests 8-9), latest wins, never mid-shot, and the monitor's
Exposure row."""

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
from core.usb_camera import UsbCamera  # noqa: E402
from test_capture_routes import GPhoto2Error, fake_gp, _wait  # noqa: E402
from test_live_capture_light import Clock  # noqa: E402


# --------------------------------------------------------------------------- #
# a fake libgphoto2 that sets single settings, refusing while live view runs
# --------------------------------------------------------------------------- #
def exposure_gp(refuse_in_live=True, choices=None):
    gp = fake_gp(cameras=(("Fuji Fujifilm GFX100", "usb:001,004"),))
    st = gp.state
    st.exp = choices or {"shutterspeed": ["1/40", "1/20", "1/10"],
                         "iso": ["100", "200", "400", "-1", "-2"]}
    st.vals = {"shutterspeed": "1/20", "iso": "200"}
    st.refuse_in_live = refuse_in_live
    st.sets = []
    st.exits = 0
    st.inits = 0

    class W:
        def __init__(self, key):
            self.key = key
            self.value = st.vals.get(key)

        def count_choices(self):
            return len(st.exp[self.key])

        def get_choice(self, i):
            return st.exp[self.key][i]

        def get_value(self):
            return self.value

        def set_value(self, v):
            self.value = v

    def get_single_config(self, key):
        if key not in st.exp:
            raise GPhoto2Error(-2)
        return W(key)

    def set_single_config(self, key, w):
        st.sets.append((key, w.value, st.in_live))
        if st.refuse_in_live and st.in_live:
            raise GPhoto2Error(-1)                 # Device Busy, as libgphoto2 reports it
        if w.value not in st.exp[key]:
            raise GPhoto2Error(-2)
        st.vals[key] = w.value

    real_exit, real_init = gp.Camera.exit, gp.Camera.init

    def exit_(self):
        real_exit(self)
        st.exits += 1
        st.in_live = False                         # disconnecting ends live view

    def init_(self):
        real_init(self)
        st.inits += 1

    gp.Camera.get_single_config = get_single_config
    gp.Camera.set_single_config = set_single_config
    gp.Camera.exit = exit_
    gp.Camera.init = init_
    return gp


def _opened(tmp_path, **kw):
    gp = exposure_gp(**kw)
    clock = Clock()
    b = cb.Gphoto2Backend(gp_module=gp, clock=clock)
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    return b, gp, clock


def test_the_cameras_own_lists_from_the_first_setting_that_has_them(tmp_path):
    b, gp, _ = _opened(tmp_path)
    assert b.exposure_choices() == {"shutter": ["1/40", "1/20", "1/10"],
                                    "iso": ["100", "200", "400", "-1", "-2"]}
    # a body with only the second name for the shutter, and no ISO list
    b2, gp2, _ = _opened(tmp_path, choices={"shutterspeed2": ["1/8", "1/4"],
                                            "iso": ["100"]})
    gp2.state.vals["shutterspeed2"] = "1/8"
    assert b2.exposure_choices() == {"shutter": ["1/8", "1/4"]}
    assert b2.set_exposure({"shutter": "1/4"})["shutter"] == (True, "")
    assert gp2.state.vals["shutterspeed2"] == "1/4"


def test_live_view_off_sets_directly(tmp_path):
    b, gp, _ = _opened(tmp_path)
    inits = gp.state.inits
    res = b.set_exposure({"shutter": "1/10", "iso": "400"})
    assert res == {"shutter": (True, ""), "iso": (True, "")}
    assert gp.state.vals == {"shutterspeed": "1/10", "iso": "400"}
    assert b.reconnects == 0 and gp.state.inits == inits


def test_a_camera_that_refuses_in_live_view_gets_one_reconnect(tmp_path):
    b, gp, clock = _opened(tmp_path)
    assert b.preview() is not None                 # live view running
    assert b._live_asked and gp.state.in_live
    frames, dest = b._preview_frames, b.dest
    inits = gp.state.inits
    res = b.set_exposure({"shutter": "1/10", "iso": "100"})
    assert res == {"shutter": (True, ""), "iso": (True, "")}
    assert b.reconnects == 1 and gp.state.inits == inits + 1   # ONE pause for both
    assert gp.state.vals == {"shutterspeed": "1/10", "iso": "100"}
    assert not b._live_asked and b._last_preview is None
    assert b._preview_frames == frames and b.dest == dest
    assert b.preview() is not None                 # live view starts again
    # the refused tries were made while live, the good ones after
    assert [s[2] for s in gp.state.sets] == [True, True, False, False]


def test_a_camera_that_takes_settings_in_live_view_never_pauses(tmp_path):
    b, gp, _ = _opened(tmp_path, refuse_in_live=False)
    b.preview()
    assert b.set_exposure({"iso": "400"}) == {"iso": (True, "")}
    assert b.reconnects == 0


def test_a_value_it_still_refuses_is_reported(tmp_path):
    b, gp, _ = _opened(tmp_path)
    b.preview()
    res = b.set_exposure({"shutter": "1/10", "iso": "3200"})   # 3200 not offered
    assert res["shutter"] == (True, "")
    assert res["iso"][0] is False and res["iso"][1]
    assert b.reconnects == 1                        # only one pause, even then
    assert gp.state.vals["iso"] == "200"


def test_shot_pending_covers_the_shot_on_its_way(tmp_path):
    b, gp, clock = _opened(tmp_path)
    assert not b.shot_pending()
    b.fire()
    assert b.shot_pending()
    for _ in range(20):
        clock.t += 0.2
        b.poll(0.0)
    assert not b.shot_pending()


def test_a_failed_reconnect_is_a_lost_camera(tmp_path):
    b, gp, _ = _opened(tmp_path)
    b.preview()
    gp.state.init_error = -52                      # unplugged meanwhile
    with pytest.raises(cb.CameraLost):
        b.set_exposure({"shutter": "1/10"})


# --------------------------------------------------------------------------- #
# the worker
# --------------------------------------------------------------------------- #
def test_the_worker_sends_the_last_picks_once_and_never_mid_shot(tmp_path):
    fake = cb.FakeBackend()
    usb = UsbCamera(backends=[fake])
    choices, results, readings, states = [], [], [], []
    usb.exposure_choices.connect(choices.append)
    usb.exposure_set.connect(results.append)
    usb.exposure_changed.connect(readings.append)
    usb.state.connect(lambda k, t: states.append(k))
    usb.start()
    try:
        usb.set_dest(str(tmp_path))
        usb.open("usb:fake:0")
        assert _wait(lambda: choices)
        assert choices[-1] == {"shutter": fake.shutters, "iso": fake.isos}
        fake.pending_shot = True                    # a shot on its way: hold
        usb.set_exposure("shutter", "1/125")
        usb.set_exposure("shutter", "1/30")
        usb.set_exposure("iso", "200")
        time.sleep(0.3)
        assert fake.set_calls == []
        assert usb.exposure_wanted() == {"shutter": "1/30", "iso": "200"}
        readings.clear()
        fake.pending_shot = False
        assert _wait(lambda: results)
        assert fake.set_calls == [{"shutter": "1/30", "iso": "200"}]
        assert results[-1] == {"shutter": (True, "", "1/30"), "iso": (True, "", "200")}
        assert _wait(lambda: readings)
        assert readings[-1]["shutter"] == "1/30" and readings[-1]["iso"] == "200"
        # with live view running the fake pauses once (like the GFX)
        usb.set_live(True)
        assert _wait(lambda: fake.frames > 0)
        usb.set_exposure("shutter", "1/250")
        assert _wait(lambda: len(fake.set_calls) == 2)
        assert fake.reconnects == 1
        # closing forgets anything still wanted
        fake.pending_shot = True
        usb.set_exposure("iso", "400")
        usb.close()
        assert _wait(lambda: "closed" in states)
        assert usb.exposure_wanted() == {}
    finally:
        usb.stop()
        usb.wait(3000)


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #
@pytest.fixture
def monitor(iso_settings):
    from test_live_monitor import _monitor, _close
    mon, feed, light, dev = _monitor()
    mon.usb.status = "open"                          # the worker isn't running here
    mon.usb.key = "usb:gphoto2:usb:001,004"
    yield mon
    mon.usb.status = "closed"
    _close(mon)


@pytest.fixture
def iso_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from widgets import live_monitor as lm
    path = str(tmp_path / "live.ini")
    monkeypatch.setattr(lm, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "scan_type", "colour", raising=False)
    return path


CHOICES = {"shutter": ["1/40", "1/20", "1/10"], "iso": ["100", "200", "-1", "-3"]}


def test_the_row_appears_with_the_cameras_values(monitor):
    mon = monitor
    assert not mon.exposure_row.isVisibleTo(mon)
    mon.usb.exposure_choices.emit(CHOICES)
    assert mon.exposure_row.isVisibleTo(mon)
    iso = [mon.iso_combo.itemText(i) for i in range(mon.iso_combo.count())]
    assert iso == ["ISO 100", "ISO 200", "Auto 1", "Auto 3"]
    assert mon.iso_combo.itemData(2) == "-1"
    assert [mon.shutter_speed_combo.itemText(i) for i in range(3)] == CHOICES["shutter"]
    mon.usb.exposure_changed.emit({"shutter": "1/20", "iso": "200"})
    assert mon.shutter_speed_combo.currentData() == "1/20"
    assert mon.iso_combo.currentData() == "200"
    # only one field offered: the other picker hides
    mon.usb.exposure_choices.emit({"shutter": CHOICES["shutter"]})
    assert mon.shutter_speed_combo.isVisibleTo(mon) and not mon.iso_combo.isVisibleTo(mon)
    # the camera goes: the row goes
    mon._on_usb_state("lost", "unplugged")
    assert not mon.exposure_row.isVisibleTo(mon)


def test_a_pick_is_sent_and_the_readout_waits_for_it(monitor):
    mon = monitor
    mon.usb.exposure_choices.emit(CHOICES)
    mon.usb.exposure_changed.emit({"shutter": "1/20", "iso": "200"})
    i = mon.shutter_speed_combo.findData("1/10")
    mon.shutter_speed_combo.setCurrentIndex(i)
    mon.shutter_speed_combo.activated.emit(i)
    assert mon.usb.exposure_wanted() == {"shutter": "1/10"}
    assert mon.capture_caption.text() == "Setting 1/10…"
    # the camera still reports the old value: the picker keeps the pick
    mon.usb.exposure_changed.emit({"shutter": "1/20", "iso": "200"})
    assert mon.shutter_speed_combo.currentData() == "1/10"
    mon.usb.exposure_set.emit({"shutter": (True, "", "1/10")})
    mon.usb.exposure_changed.emit({"shutter": "1/10", "iso": "200"})
    assert mon.shutter_speed_combo.currentData() == "1/10"
    assert mon.capture_caption.text() == ""
    # picking what is already set sends nothing
    mon.usb._want.clear()
    mon.shutter_speed_combo.activated.emit(i)
    assert mon.usb.exposure_wanted() == {}


def test_a_refused_value_is_said_and_the_picker_goes_back(monitor):
    mon = monitor
    mon.usb.exposure_choices.emit(CHOICES)
    mon.usb.exposure_changed.emit({"shutter": "1/20", "iso": "200"})
    i = mon.iso_combo.findData("100")
    mon.iso_combo.setCurrentIndex(i)
    mon.iso_combo.activated.emit(i)
    mon.usb.exposure_set.emit({"iso": (False, "Device Busy", "100")})
    assert "didn't take ISO 100 (Device Busy)" in mon.capture_caption.text()
    mon.usb.exposure_changed.emit({"shutter": "1/20", "iso": "200"})
    assert mon.iso_combo.currentData() == "200"


def test_a_pick_during_a_sequence_waits_for_its_end(monitor):
    mon = monitor
    mon.usb.exposure_choices.emit(CHOICES)
    mon.usb.exposure_changed.emit({"shutter": "1/20", "iso": "200"})
    mon.sequence.running = True
    i = mon.shutter_speed_combo.findData("1/40")
    mon.shutter_speed_combo.setCurrentIndex(i)
    mon.shutter_speed_combo.activated.emit(i)
    assert mon.usb.exposure_wanted() == {}
    assert "is set when this shot finishes" in mon.capture_caption.text()
    mon.usb.exposure_changed.emit({"shutter": "1/20", "iso": "200"})
    assert mon.shutter_speed_combo.currentData() == "1/40"      # held, not reverted
    mon.sequence.running = False
    mon._on_sequence_finished("done")
    assert mon.usb.exposure_wanted() == {"shutter": "1/40"}
