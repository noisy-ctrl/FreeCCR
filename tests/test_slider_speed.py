#!/usr/bin/env python3
"""Live slider rendering (spec/slider-speed.md): render-then-show, off the GUI
thread, latest wins, drafts only when slow, and no change to the pixels."""

import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])
_LIVE = []


def _scene(h=480, w=640, seed=3):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    g = 0.15 + 0.6 * (x / w) * (0.6 + 0.4 * y / h)
    g = g + rng.normal(0, 0.02, size=g.shape).astype(np.float32)
    img = np.stack([g * 1.05, g, g * 0.9], axis=-1)
    return (np.clip(img, 0, 1) * 65535).astype(np.uint16)


def _make_image(tmp_path, name="scan.png", shape=(480, 640)):
    import cv2
    from core.ccr_image import CCRImage
    path = str(tmp_path / name)
    cv2.imwrite(path, np.full((40, 60, 3), 20000, np.uint16))
    img = CCRImage(path)
    img.converted = True
    img.adjustment_settings = {"contrast": 10, "saturation": 5}
    img.resized_raw = _scene(*shape)
    img.original_full_size = (shape[1] * 4, shape[0] * 4)
    return img


def _pix(pm):
    im = pm.toImage()
    return bytes(im.constBits())


def _wait(lr, timeout=20.0):
    t0 = time.monotonic()
    while lr.busy() and time.monotonic() - t0 < timeout:
        _app.processEvents()
        time.sleep(0.002)
    for _ in range(5):
        _app.processEvents()
    assert not lr.busy(), "live render did not finish"


@pytest.fixture
def backend_images():
    from core.ccr_backend import ccr_backend
    saved = (ccr_backend.images, ccr_backend.file_paths)
    yield ccr_backend
    ccr_backend.images, ccr_backend.file_paths = saved


# --- Render split -----------------------------------------------------------

def test_snapshot_render_equals_live_render(tmp_path, backend_images):
    img = _make_image(tmp_path)
    backend_images.images = [img]
    img.area_layers = [{"id": "a", "enabled": True, "kind": "circle",
                        "geometry": {"cx": 0.4, "cy": 0.4, "rx": 0.3, "ry": 0.2},
                        "feather": 0.2, "settings": {"brightness": 20}}]
    img.adjustment_settings = {"contrast": 15, "gamma": 10,
                               "curves": {"rgb": [[0, 0], [120, 100], [255, 255]]}}
    snap = img.preview_render_snapshot()
    a = img.render_preview_pixels()
    b = img.render_preview_pixels(snap)
    assert np.array_equal(a["preview8"], b["preview8"])
    assert np.array_equal(a["thumb8"], b["thumb8"])
    assert np.array_equal(a["hist"], b["hist"])
    e0 = img._render_epoch if hasattr(img, "_render_epoch") else 0
    img.update_thumbnail_and_preview()
    assert img._render_epoch == e0 + 1
    assert np.array_equal(img.histogram_data, a["hist"])


def test_snapshot_is_isolated_from_later_edits(tmp_path, backend_images):
    img = _make_image(tmp_path)
    backend_images.images = [img]
    snap = img.preview_render_snapshot()
    before = img.render_preview_pixels(snap)["preview8"]
    img.adjustment_settings = {"contrast": 80}
    after = img.render_preview_pixels(snap)["preview8"]
    assert np.array_equal(before, after)


# --- Auto Gain cache --------------------------------------------------------

def test_auto_gain_cache_is_exact_and_invalidates(tmp_path):
    from core.ccr_processor import compute_auto_gain_offset
    img = _make_image(tmp_path)
    base = img.resized_raw
    v = img._auto_gain_cached(base, False, None)
    assert v == compute_auto_gain_offset(base, False)
    calls = []
    import core.ccr_image as ci
    orig = ci.compute_auto_gain_offset
    ci.compute_auto_gain_offset = lambda b, ws: calls.append(1) or orig(b, ws)
    try:
        assert img._auto_gain_cached(base, False, None) == v and not calls
        img._auto_gain_cached(base, True, None)               # window flag
        assert len(calls) == 1
        other = base.copy()
        img._auto_gain_cached(other, True, None)              # another array
        assert len(calls) == 2
        other[::53, ::59] //= 2                               # in-place edit
        img._auto_gain_cached(other, True, None)
        assert len(calls) == 3
        img._auto_gain_cached(other, True, {"xt_rg": 20})     # crosstalk
        assert len(calls) == 4
    finally:
        ci.compute_auto_gain_offset = orig


def test_draft_keeps_geometry_and_exposure(tmp_path, backend_images):
    img = _make_image(tmp_path)
    backend_images.images = [img]
    full = img.render_preview_pixels()
    draft = img.render_preview_pixels(draft_long=320)
    assert draft["draft"] and draft["thumb8"] is None
    assert draft["preview8"].shape == full["preview8"].shape
    # same Auto Gain / tone: means agree closely, only spatial detail differs
    assert abs(float(draft["preview8"].mean()) - float(full["preview8"].mean())) < 1.0


# --- Panel wiring -----------------------------------------------------------

class _PreviewStub:
    """Mirrors ImagePreview.update_preview's contract: read the cached preview,
    THEN hand over to the panel's set_current_idx."""
    def __init__(self, img, log):
        self.current_idx = 0
        self._img = img
        self.log = log
        self.panel = None

    def update_preview(self, idx):
        self.log.append(("show", _pix(self._img.resized_preview)))
        if self.panel is not None:
            self.panel.set_current_idx(idx)


@pytest.fixture
def live_panel(tmp_path, backend_images):
    from widgets.sliders_panel import SlidersPanel
    img = _make_image(tmp_path)
    log = []

    class _Host(QWidget):
        def __init__(self):
            super().__init__()
            self.image_preview = _PreviewStub(img, log)
            self.mid = QWidget(self)

    host = _Host()
    QVBoxLayout(host.mid).addWidget(SlidersPanel(host.mid))
    panel = host.mid.layout().itemAt(0).widget()
    host.image_preview.panel = panel
    _LIVE.append(host)
    backend_images.images, backend_images.file_paths = [img], [img.file_path]
    img.update_thumbnail_and_preview()
    panel.current_idx = 0
    for sl in panel.sliders:
        sl.setEnabled(True)
    log.clear()
    yield panel, img, log
    panel.shutdown_live_render()


def _slider(panel, key):
    return panel.sliders[panel.adjustment_keys.index(key)]


def test_tick_shows_a_render_of_the_latest_settings(live_panel):
    """The old path showed the PREVIOUS tick's render; now what lands on the
    canvas is a render of the current settings."""
    panel, img, log = live_panel
    _slider(panel, "contrast").setValue(40)
    lr = panel._live
    _wait(lr)
    assert img.adjustment_settings["contrast"] == 40
    shown = log[-1][1]
    img.update_thumbnail_and_preview()                # fresh synchronous render
    assert shown == _pix(img.resized_preview)


def test_ticks_coalesce_latest_wins(live_panel, monkeypatch):
    panel, img, log = live_panel
    from core.ccr_image import CCRImage
    n = [0]
    orig = CCRImage.render_preview_pixels

    def counting(self, *a, **k):
        n[0] += 1
        return orig(self, *a, **k)
    monkeypatch.setattr(CCRImage, "render_preview_pixels", counting)
    sl = _slider(panel, "contrast")
    for v in range(1, 31):                       # 30 ticks, no event processing
        sl.setValue(v)
    _wait(panel._live)
    assert n[0] <= 3                             # one in flight + one pending
    img.update_thumbnail_and_preview()
    assert log[-1][1] == _pix(img.resized_preview)


def test_synchronous_render_voids_a_late_live_result(live_panel):
    panel, img, log = live_panel
    _slider(panel, "contrast").setValue(60)      # live job in flight
    lr = panel._live
    img.adjustment_settings = dict(img.adjustment_settings, contrast=-50)
    img.update_thumbnail_and_preview()           # waits for the job, then wins
    sync = _pix(img.resized_preview)
    _wait(lr)
    assert _pix(img.resized_preview) == sync
    assert not lr.needs_full(img)


def test_refresh_guard_neither_renders_nor_cancels_settle(live_panel, monkeypatch):
    panel, img, _log = live_panel
    from core.ccr_backend import ccr_backend
    calls = []
    monkeypatch.setattr(ccr_backend, "apply_adjustment_by_index",
                        lambda idx: calls.append(idx))
    panel._debounce_timer.start(5000)
    with panel.live_refresh():
        panel.set_current_idx(0)
    assert not calls and panel._debounce_timer.isActive()
    panel.set_current_idx(0)                     # unguarded: as before
    assert calls == [0] and not panel._debounce_timer.isActive()


def test_drafts_only_mid_burst_when_slow_and_never_with_dust(live_panel, monkeypatch):
    panel, img, _log = live_panel
    from core.ccr_image import CCRImage
    seen = []
    orig = CCRImage.render_preview_pixels

    def spy(self, snap=None, **k):
        seen.append(k.get("draft_long"))
        return orig(self, snap, **k)
    monkeypatch.setattr(CCRImage, "render_preview_pixels", spy)
    lr = panel._live_renderer()
    sl = _slider(panel, "contrast")
    for v in (5, 6):                              # fast machine: never a draft
        lr.last_full_ms = 10.0
        panel._last_live_req = time.monotonic()   # mid-burst
        sl.setValue(v); _wait(lr)
    assert all(d is None for d in seen)
    seen.clear()
    lr.last_full_ms = 500.0                       # slow machine
    panel._last_live_req = 0.0
    sl.setValue(7); _wait(lr)                     # first of a burst: full
    lr.last_full_ms = 500.0                       # (the real render reset it)
    panel._last_live_req = time.monotonic()       # still mid-burst
    sl.setValue(8); _wait(lr)                     # mid-burst: draft
    assert seen[0] is None and seen[-1] == 320
    assert lr.needs_full(img)
    panel._process_pending_adjustment()           # idle settle
    _wait(lr)
    assert seen[-1] is None and not lr.needs_full(img)
    seen.clear()
    img.dust_spots = [{"x": 0.5, "y": 0.5, "r": 0.01}]
    monkeypatch.setattr(CCRImage, "_apply_dust_removal",
                        lambda self, image, ws_windowed=False: image)
    lr.last_full_ms = 500.0
    sl.setValue(9); _wait(lr)
    lr.last_full_ms = 500.0
    panel._last_live_req = time.monotonic()
    sl.setValue(10); _wait(lr)
    assert all(d is None for d in seen)


def test_settle_after_draft_equals_synchronous_render(live_panel):
    panel, img, log = live_panel
    lr = panel._live_renderer()
    lr.last_full_ms = 500.0
    sl = _slider(panel, "saturation")
    panel._last_live_req = time.monotonic()
    sl.setValue(30)                               # a draft
    _wait(lr)
    panel._process_pending_adjustment()
    _wait(lr)
    shown = log[-1][1]
    img.update_thumbnail_and_preview()
    assert shown == _pix(img.resized_preview)


def test_discrete_edit_renders_once(live_panel, monkeypatch):
    panel, img, log = live_panel
    from core.ccr_image import CCRImage
    n = [0]
    orig = CCRImage.render_preview_pixels

    def counting(self, *a, **k):
        n[0] += 1
        return orig(self, *a, **k)
    monkeypatch.setattr(CCRImage, "render_preview_pixels", counting)
    panel._on_cineon_toggled(True)
    assert n[0] == 1 and log and log[-1][0] == "show"


# --- OpenCL device choice ---------------------------------------------------

class _Dev:
    def __init__(self, name, kind):
        self.name = name
        self.type = kind


class _Plat:
    def __init__(self, name, devs):
        self.name = name
        self._devs = devs

    def get_devices(self):
        return self._devs


@pytest.mark.skipif("not __import__('core.ccr_processor', fromlist=['x']).OPENCL_AVAILABLE")
def test_device_choice_default_unchanged_and_overrides():
    from core import ccr_processor as cp
    CPU, GPU = int(cp.cl.device_type.CPU), int(cp.cl.device_type.GPU)
    p1 = _Plat("Apple", [_Dev("Intel CPU", CPU), _Dev("Iris", GPU)])
    p2 = _Plat("Other", [_Dev("Radeon", GPU)])
    assert cp._pick_opencl_device([p1, p2], "")[1].name == "Intel CPU"
    assert cp._pick_opencl_device([p1, p2], "gpu")[1].name == "Iris"
    assert cp._pick_opencl_device([p1, p2], "cpu")[1].name == "Intel CPU"
    assert cp._pick_opencl_device([p1, p2], "2")[1].name == "Radeon"
    assert cp._pick_opencl_device([p1, p2], "9")[1].name == "Intel CPU"
    assert cp._pick_opencl_device([_Plat("x", [])], "") == (None, None)


def test_opencl_can_be_switched_off(monkeypatch):
    from core import ccr_processor as cp
    monkeypatch.setenv("FREECCR_OPENCL", "0")
    assert cp._opencl_wanted() is False
    monkeypatch.setenv("FREECCR_OPENCL", "1")
    assert cp._opencl_wanted() is True


def test_late_result_does_not_leave_crop_mode(live_panel):
    """Any canvas refresh exits crop/slice mode, so a render landing after the
    user entered one must store its pixels without redrawing."""
    panel, img, log = live_panel
    ip = panel.parent().parent().image_preview
    ip.crop_mode = True
    _slider(panel, "contrast").setValue(33)
    _wait(panel._live)
    assert not log                                # no refresh while cropping
    shown_later = img.render_preview_pixels()["preview8"]
    from core.ccr_image import CCRImage  # noqa: F401
    img_pix = img.resized_preview.toImage()
    assert img_pix.width() == shown_later.shape[1]   # result was stored
    ip.crop_mode = False
