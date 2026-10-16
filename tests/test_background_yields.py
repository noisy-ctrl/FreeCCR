#!/usr/bin/env python3
"""The background detail render gives way to you (spec/background-yields.md).

While you scroll the thumbnail list or move between frames, the sharp-view
render waits to start, pauses at its safe points, holds its result back, and
gives up on frames you have left."""

import os
import sys
import threading
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import load_progress, ui_activity  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from widgets import image_preview as ip_mod  # noqa: E402
from widgets.image_preview import HiResDetailWorker  # noqa: E402

from test_full_res_zoom import (_setup, _viewport_ok, _FakeWorker,  # noqa: E402
                                _StubImage)


def _idle():
    ui_activity._last = -1e9


def _busy_for(seconds):
    """The UI counts as busy for about `seconds` more."""
    ui_activity._last = time.monotonic() - ui_activity.QUIET_S + seconds


def _pump_until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# --- the clock and the stop signal ------------------------------------------------

def test_the_clock():
    _idle()
    assert not ui_activity.busy()
    ui_activity.touch()
    assert ui_activity.busy() and ui_activity.idle_for() < 0.1
    _busy_for(-0.01)
    assert not ui_activity.busy()


def test_step_lets_the_stop_through_and_nothing_else():
    def stop(_f):
        raise load_progress.Interrupted()

    def broken(_f):
        raise ValueError("a progress bar bug")

    with load_progress.bound(stop):
        with pytest.raises(load_progress.Interrupted):
            load_progress.step(0.3)
    with load_progress.bound(broken):
        load_progress.step(0.3)                   # swallowed, as before
    load_progress.step(0.3)                       # nothing bound: a no-op
    # The decode's own `except Exception` handlers must not catch it.
    assert not issubclass(load_progress.Interrupted, Exception)


# --- the worker's safe points -------------------------------------------------------

class _DecodeStub(_StubImage):
    """A frame whose 'decode' reports two milestones like the real one."""

    def __init__(self, before_step=None):
        super().__init__(preview_w=64, preview_h=48)
        self.converted = True
        self.log = []
        self.before_step = before_step

    def render_hires_base(self, max_long_side=None, conversion_inputs=None,
                          preview=True):
        self.log.append(("decode", time.monotonic()))
        if self.before_step:
            self.before_step()
        load_progress.step(0.3)
        self.log.append(("read", time.monotonic()))
        load_progress.step(0.7)
        return np.full((48, 64, 3), 30000, np.uint16), None

    def apply_adjustments(self, base, **kw):
        self.log.append(("adjust", time.monotonic()))
        return base


def _run(img):
    w = HiResDetailWorker(img, ("sig",), ("adj",), None, 1000, preview=True,
                          res=1000)
    got = []
    w.finished_hires.connect(lambda *a: got.append(a), Qt.DirectConnection)
    return w, got


def _events(img):
    return [e for e, _t in img.log]


def test_an_idle_render_runs_straight_through():
    _idle()
    img = _DecodeStub()
    w, got = _run(img)
    w.start()
    assert w.wait(5000)
    assert _events(img) == ["decode", "read", "adjust"] and len(got) == 1


def test_it_waits_to_start_while_you_scroll():
    _busy_for(0.3)
    img = _DecodeStub()
    w, got = _run(img)
    t0 = time.monotonic()
    w.start()
    assert w.wait(5000)
    assert img.log[0][1] - t0 > 0.2               # held at the first safe point
    assert len(got) == 1                          # and then finished


def test_it_pauses_mid_decode_when_you_start_scrolling():
    _idle()
    img = _DecodeStub(before_step=ui_activity.touch)   # you scroll mid-decode
    w, got = _run(img)
    w.start()
    assert w.wait(5000)
    t = dict(img.log)
    assert t["read"] - t["decode"] > 0.3          # paused at the milestone
    assert len(got) == 1


def test_a_frame_you_left_stops_at_the_next_safe_point(capsys):
    _idle()
    in_decode, go = threading.Event(), threading.Event()

    def hold():
        in_decode.set()
        go.wait(5)

    img = _DecodeStub(before_step=hold)
    w, got = _run(img)
    w.start()
    assert in_decode.wait(5)
    w.requestInterruption()
    go.set()
    assert w.wait(5000)
    assert _events(img) == ["decode"]             # nothing after the milestone
    assert got == []
    assert "failed" not in capsys.readouterr().out


# --- starting a render ---------------------------------------------------------------

@pytest.fixture
def fit_view(monkeypatch):
    """A fitted view on a 2x screen, where the sharp-fit render applies."""
    monkeypatch.setattr(ip_mod, "HiResDetailWorker", _FakeWorker)
    monkeypatch.setattr(ccr_backend, "sharp_fit_preview", True, raising=False)
    _FakeWorker.instances = []
    _idle()
    ip = _setup()
    if not _viewport_ok(ip):
        pytest.skip("offscreen viewport not sized")
    img = ccr_backend.images[0]
    img.converted = True                          # sharp fit is for converted frames
    img.conversion_inputs = {"mode": "bw"}        # ... with a replayable recipe
    monkeypatch.setattr(ip, "_dpr", lambda: 2.0)
    ip.zoom_to_fit()
    _FakeWorker.instances = []
    ip._hires_workers.clear()
    yield ip
    _FakeWorker.instances = []


def test_the_fitted_view_render_waits_for_a_quiet_moment(fit_view):
    ip = fit_view
    assert ip._zoomed_in_enough() and ip._fit_only_request()
    ui_activity.touch()
    ip._maybe_request_hires()
    assert _FakeWorker.instances == []            # not while you are moving
    assert ip._hires_defer_timer.isActive()
    assert _pump_until(lambda: len(_FakeWorker.instances) == 1)
    assert ui_activity.idle_for() >= ip.HIRES_QUIET_S - 0.05


def test_a_quiet_ui_starts_it_at_once(fit_view):
    fit_view._maybe_request_hires()
    assert len(_FakeWorker.instances) == 1


def test_zooming_in_starts_at_once(fit_view):
    ip = fit_view
    ip.zoom_to_percent(1.0)
    _FakeWorker.instances = []
    ip._hires_workers.clear()
    ui_activity.touch()
    ip._maybe_request_hires()
    assert len(_FakeWorker.instances) == 1        # you asked for the detail


# --- landing the result --------------------------------------------------------------

def test_the_result_waits_until_you_stop(fit_view):
    ip = fit_view
    img = ccr_backend.images[0]
    sig, adj = ip._hires_signature(img), ip._current_adj_sig()
    display8 = np.zeros((1200, 1800, 3), np.uint8)
    base = np.zeros((1200, 1800, 3), np.uint16)
    ip._hires = None
    _busy_for(0.25)
    ip._on_hires_ready(img, sig, adj, base, None, display8, 1800)
    assert ip._hires is None                      # held back
    assert ip._hires_land_timer.isActive()
    assert _pump_until(lambda: ip._hires is not None)
    assert ip._hires["img"] is img and ip._hires["full_pm"] is not None
    assert ip._hires_pending is None


# --- switching frames ----------------------------------------------------------------

class _Running:
    def __init__(self, img):
        self.req_img = img
        self.stopped = False

    def isRunning(self):
        return True

    def requestInterruption(self):
        self.stopped = True


def test_switching_frames_stops_the_others(tmp_path):
    from test_dust_image_switch import _two_image_preview
    ip, _host, imgs = _two_image_preview(tmp_path)
    old, new = _Running(imgs[0]), _Running(imgs[1])
    ip._hires_workers.update({old, new})
    _idle()
    ip.update_preview(1)
    assert old.stopped and not new.stopped
    assert ui_activity.busy()                     # a switch counts as activity
    _idle()
    ip.update_preview(1)                          # a same-frame refresh does not
    assert not ui_activity.busy()
    ip._hires_workers.clear()


def test_scrolling_the_list_counts_as_activity():
    from widgets.thumbnail_list import ThumbnailList
    tl = ThumbnailList(lambda *_a: None)
    bar = tl.thumbnail_list.verticalScrollBar()
    bar.setRange(0, 100)
    _idle()
    bar.setValue(40)
    assert ui_activity.busy()
