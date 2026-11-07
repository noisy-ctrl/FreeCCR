#!/usr/bin/env python3
"""Live view after a Measure on a Fujifilm (spec/capture-routes.md §22): the
body refuses to start live view while its PriorityMode is Camera, and
libgphoto2's event check sets Camera on every call, so the backend sets USB
right before asking for live view whenever live view isn't running."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import camera_backends as cb  # noqa: E402
from test_capture_routes import GPhoto2Error, _File, _List  # noqa: E402
from test_live_capture_light import Clock  # noqa: E402
from test_usb_exposure_control import exposure_gp  # noqa: E402

FUJI = ("Fuji Fujifilm GFX100", "usb:001,004")
CANON = ("Canon EOS 5D Mark III", "usb:001,004")


def priority_gp(model=FUJI, has_priority=True):
    """A GFX 50S II as libgphoto2 2.5.34 drives it: connecting sets USB
    priority, every event check sets Camera (refused while live view runs),
    the capture call ends live view and sets USB, and live view won't start
    with Camera priority."""
    choices = {"shutterspeed": ["1/40", "1/20", "1/10"], "iso": ["100", "200"]}
    if has_priority:
        choices["prioritymode"] = ["Camera", "USB"]
    gp = exposure_gp(choices=choices)
    gp.Camera.autodetect = staticmethod(lambda: _List([model]))
    st = gp.state
    st.vals["prioritymode"] = "USB"
    st.priority_reads = 0
    st.refusals = 0
    st.starts = 0
    st.frame = 0
    real_init, real_get = gp.Camera.init, gp.Camera.get_single_config

    def init(self):
        real_init(self)
        if has_priority:
            st.vals["prioritymode"] = "USB"         # camera_prepare_capture

    def wait_for_event(self, ms):
        st.event_checks += 1
        if has_priority and not st.in_live:
            st.vals["prioritymode"] = "Camera"      # "reenable event wait mode"
        if st.events:
            return st.events.pop(0)
        return gp.GP_EVENT_TIMEOUT, None

    def capture_preview(self):
        if not st.in_live:
            if st.vals.get("prioritymode") == "Camera":
                st.refusals += 1
                raise GPhoto2Error(gp.GP_ERROR_CAMERA_BUSY)
            st.in_live = True                       # Initiate Open Capture
            st.starts += 1
        st.frame += 1
        return _File(b"\xff\xd8 frame %d \xff\xd9" % st.frame)

    def capture(self, kind):
        st.in_live = False                          # camera_fuji_capture ends it
        if has_priority:
            st.vals["prioritymode"] = "USB"
        st.captures += 1
        return type("P", (), {"folder": "/store_10000001", "name": "DSCF%04d.RAF" % st.captures})()

    def get_single_config(self, key):
        if key == "prioritymode":
            st.priority_reads += 1
        return real_get(self, key)

    gp.Camera.init = init
    gp.Camera.wait_for_event = wait_for_event
    gp.Camera.capture_preview = capture_preview
    gp.Camera.capture = capture
    gp.Camera.get_single_config = get_single_config
    return gp


def _opened(tmp_path, **kw):
    gp = priority_gp(**kw)
    clock = Clock()
    b = cb.Gphoto2Backend(gp_module=gp, clock=clock)
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    return b, gp, clock


def _priority_sets(gp):
    return [s for s in gp.state.sets if s[0] == "prioritymode"]


def _frame(b, clock):
    clock.t += 1.0                                  # past any busy back-off
    return b.preview()


def test_the_fake_refuses_live_view_with_camera_priority(tmp_path):
    """The camera as the log has it: after an event check with live view off,
    starting live view is refused however often it is asked."""
    gp = priority_gp()
    cam = gp.Camera()
    cam.init()
    cam.wait_for_event(10)
    assert gp.state.vals["prioritymode"] == "Camera"
    for _ in range(3):
        try:
            cam.capture_preview()
            raise AssertionError("started")
        except GPhoto2Error:
            pass
    assert gp.state.refusals == 3 and not gp.state.in_live


def test_live_view_starts_after_an_event_check_set_camera_priority(tmp_path):
    b, gp, clock = _opened(tmp_path)
    b.poll(0.01)                                     # the event check: Camera
    assert gp.state.vals["prioritymode"] == "Camera"
    assert _frame(b, clock) is not None              # USB first, then live view
    assert gp.state.refusals == 0 and gp.state.starts == 1
    assert _priority_sets(gp) == [("prioritymode", "USB", False)]


def test_no_priority_setting_while_live_view_runs(tmp_path):
    b, gp, clock = _opened(tmp_path)
    assert _frame(b, clock) is not None
    for _ in range(5):
        clock.t += 5.0                               # an event check is due each time
        b.poll(0.01)                                 # refused by the running live view
        assert _frame(b, clock) is not None
    assert gp.state.vals["prioritymode"] == "USB"
    assert len(_priority_sets(gp)) == 1 and gp.state.starts == 1


def test_set_again_after_a_shot_and_after_a_reconnect(tmp_path):
    """The Measure: a reconnect for the exposure, shots, event checks (each
    sets Camera with live view off), then live view again."""
    b, gp, clock = _opened(tmp_path)
    assert _frame(b, clock) is not None
    assert b.set_exposure({"shutter": "1/10"})["shutter"] == (True, "")   # reconnects
    for _ in range(3):
        b.fire()
        clock.t += 5.0
        b.poll(0.01)
        assert gp.state.vals["prioritymode"] == "Camera"
    assert _frame(b, clock) is not None
    assert gp.state.refusals == 0 and gp.state.starts == 2
    b.restart_live()                                 # the old fallback still works too
    b.poll(0.01)
    assert _frame(b, clock) is not None and gp.state.starts == 3
    assert len(_priority_sets(gp)) == 3


def test_not_on_a_camera_that_isnt_a_fujifilm(tmp_path):
    b, gp, clock = _opened(tmp_path, model=CANON)
    gp.state.vals["prioritymode"] = "USB"
    assert _frame(b, clock) is not None
    assert _priority_sets(gp) == [] and gp.state.priority_reads == 0


def test_a_body_without_the_setting_is_asked_once_per_connection(tmp_path):
    b, gp, clock = _opened(tmp_path, has_priority=False)
    assert _frame(b, clock) is not None
    b.fire()
    assert _frame(b, clock) is not None
    assert gp.state.priority_reads == 1 and _priority_sets(gp) == []
    b.open("usb:001,004", str(tmp_path))
    _frame(b, clock)
    assert gp.state.priority_reads == 2


def test_a_refused_setting_still_asks_for_live_view(tmp_path):
    b, gp, clock = _opened(tmp_path)
    gp.state.in_live = True                          # e.g. still running on the camera
    assert _frame(b, clock) is not None              # the refusal is not fatal
    assert _priority_sets(gp) == [("prioritymode", "USB", True)]


def test_the_debug_log_stays_out_of_the_terminal(tmp_path, monkeypatch):
    import logging
    monkeypatch.setenv("FREECCR_GPHOTO2_LOG", str(tmp_path / "gp.log"))
    monkeypatch.setattr(cb, "_GP_LOG", [])
    log = logging.getLogger("gphoto2")
    saved = (log.handlers[:], log.propagate, log.level)
    try:
        gp = priority_gp()
        gp.use_python_logging = lambda mapping=None: object()
        gp.check_result = lambda r: r
        gp.GP_LOG_ERROR, gp.GP_LOG_VERBOSE, gp.GP_LOG_DEBUG = 0, 1, 2
        cb._gphoto2_debug_log(gp)
        assert log.propagate is False
        assert any(isinstance(h, logging.FileHandler) for h in log.handlers)
    finally:
        for h in log.handlers[:]:
            if h not in saved[0]:
                log.removeHandler(h)
                h.close()
        log.propagate, log.level = saved[1], saved[2]
