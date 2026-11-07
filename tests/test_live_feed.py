#!/usr/bin/env python3
"""The live feed (spec/live-capture-monitor.md §5): view geometry, the view
filters, the sharpness meter, and the capture thread with a fake device."""

import os
import sys
import time

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import live_feed as lf  # noqa: E402


def _scene(w=640, h=360, seed=0):
    """Textured, mid-grey-ish BGR picture with edges."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    base = 110 + 40 * np.sin(x / 13.0) * np.cos(y / 17.0)
    base += 30 * ((x // 40 + y // 40) % 2)
    img = np.stack([base * 0.8, base, base * 1.1], -1)
    img += rng.normal(0, 2.0, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def test_fit_is_centred_and_whole():
    g = lf.resolve_view(1920, 1080, 1000, 800)
    assert (g.x0, g.y0, g.x1, g.y1) == (0, 0, 1920, 1080) and g.fit
    assert g.out_w == 1000 and abs(g.out_h - 562.5) <= 0.5
    assert g.ox == pytest.approx(0, abs=1e-9) and g.oy == pytest.approx((800 - 562.5) / 2)


def test_zoom_clamps_to_the_frame_and_maps_both_ways():
    for scale, cx, cy in ((1.0, 960, 540), (2.0, 10, 10), (1.5, 1900, 1070), (4.0, 700, 300),
                          (9.0, 700, 300), (0.01, 0, 0)):
        g = lf.resolve_view(1920, 1080, 1200, 800, scale, cx, cy)
        assert 0 <= g.x0 < g.x1 <= 1920 and 0 <= g.y0 < g.y1 <= 1080
        fs = lf.fit_scale(1920, 1080, 1200, 800)
        assert g.scale <= lf.max_scale(fs) + 1e-6
        for px, py in ((g.ox + 3, g.oy + 5), (g.ox + g.out_w / 2, g.oy + g.out_h / 3)):
            sx, sy = g.to_sensor(px, py)
            bx, by = g.to_canvas(sx, sy)
            assert abs(bx - px) < 1e-6 and abs(by - py) < 1e-6
    g = lf.resolve_view(1920, 1080, 1200, 800, 2.0, 10, 10)     # pinned to the corner
    assert (g.x0, g.y0) == (0, 0)
    g = lf.resolve_view(1920, 1080, 1200, 800, 0.01)            # never below fit
    assert g.fit


def test_a_sensor_point_stays_under_the_same_canvas_point_when_zoomed_about_it():
    g1 = lf.resolve_view(1920, 1080, 1200, 800, 1.0, 800, 500)
    sx, sy = g1.to_sensor(600, 400)                               # the canvas middle
    g2 = lf.resolve_view(1920, 1080, 1200, 800, 2.0, sx, sy)
    px, py = g2.to_canvas(sx, sy)
    assert abs(px - 600) < 1.5 and abs(py - 400) < 1.5


# --------------------------------------------------------------------------- #
# filters and measures
# --------------------------------------------------------------------------- #
def test_denoise_converges_on_a_still_picture_and_restarts_when_it_moves():
    rng = np.random.default_rng(1)
    clean = _scene().astype(np.float32)
    d = lf.Denoiser()
    out = None
    for _ in range(20):
        noisy = np.clip(clean + rng.normal(0, 6, clean.shape), 0, 255).astype(np.uint8)
        out, moved = d.apply(noisy, 0.35, key="k")
        assert not moved
    assert np.std(out.astype(np.float32) - clean) < 6 * 0.6      # quieter than one frame
    shifted = np.roll(noisy, 25, axis=1)
    out, moved = d.apply(shifted, 0.35, key="k")
    assert moved and np.array_equal(out, shifted)                # no ghost of the old position
    out2, _ = d.apply(shifted, 0.35, key="other view")
    assert np.array_equal(out2, shifted)                         # a new view starts over


def test_edge_amplify_moves_luma_only():
    img = _scene()
    assert lf.edge_amplify(img, 0, 1.2) is img
    out = lf.edge_amplify(img, 150, 1.2)
    a, b = img.astype(np.float32), out.astype(np.float32)
    ok = (out > 0).all(-1) & (out < 255).all(-1)                 # where nothing clipped
    ya, yb = lf.luma(a), lf.luma(b)
    for c in (0, 2):                                             # B - Y and R - Y
        diff = np.abs((b[..., c] - yb) - (a[..., c] - ya))[ok]
        assert diff.max() <= 1.0 + 1e-3
    assert np.abs(yb - ya).max() > 10                            # luma detail did grow


def test_sharpness_ignores_brightness_and_follows_focus():
    img = cv2.cvtColor(_scene(), cv2.COLOR_BGR2GRAY).astype(np.float32)
    s = lf.sharpness(img)
    assert lf.sharpness(img * 0.5) == pytest.approx(s, rel=0.02)
    assert lf.sharpness(np.clip(img * 0.5 + 0.5, 0, 255).astype(np.uint8)) == pytest.approx(s, rel=0.02)
    assert lf.sharpness(cv2.GaussianBlur(img, (0, 0), 1.0)) < 0.5 * s


def test_sharpness_best_is_per_area_and_zoom():
    b = lf.SharpnessBest()
    assert b.update("a", 5.0) == 5.0
    assert b.update("a", 3.0) == 5.0
    assert b.update("b", 2.0) == 2.0           # another area/zoom starts over
    b.reset()
    assert b.update("b", 1.0) == 1.0


def test_activity_sees_movement_and_focus_but_not_noise():
    class C:
        t = 0.0

        def __call__(self):
            return self.t
    clock = C()
    act = lf.Activity(clock)
    rng = np.random.default_rng(2)
    clean = _scene().astype(np.float32)

    def noisy(img):
        return np.clip(img + rng.normal(0, 2, img.shape), 0, 255).astype(np.uint8)
    assert not act.update(noisy(clean), False)
    for _ in range(5):
        clock.t += 1.1
        assert not act.update(noisy(clean), False)
    clock.t += 1.1
    assert act.update(noisy(cv2.GaussianBlur(clean, (0, 0), 1.5)), False)   # focus turned
    assert act.update(noisy(clean), True)                                   # moved


def test_histogram_is_rgb_counts():
    img = np.zeros((10, 10, 3), np.uint8)
    img[..., 2] = 200                                   # red, in BGR
    h = lf.histogram_rgb(img)
    assert h.shape == (3, 256) and h[0, 200] == 100 and h[1, 0] == 100 and h[2, 0] == 100


# --------------------------------------------------------------------------- #
# the processor
# --------------------------------------------------------------------------- #
def test_processor_renders_at_the_canvas_device_size():
    p = lf.Processor()
    img = _scene(1920, 1080)
    r = p.process(img, lf.ViewParams(canvas_w=1600, canvas_h=1000))
    assert (r.image.width(), r.image.height()) == (r.geometry.out_w, r.geometry.out_h) == (1600, 900)
    r = p.process(img, lf.ViewParams(canvas_w=1600, canvas_h=1000, scale=1.0, cx=900, cy=500))
    assert (r.image.width(), r.image.height()) == (1600, 1000)       # 1:1, a crop
    assert r.geometry.x1 - r.geometry.x0 == 1600


def test_processor_negative_false_colour_clip_and_sample():
    p = lf.Processor()
    img = np.full((360, 640, 3), (60, 120, 210), np.uint8)
    img[:20] = 255
    lut = np.zeros((256, 1, 3), np.uint8)
    lut[:, 0, 0] = 255 - np.arange(256)
    lut[:, 0, 1] = 255 - np.arange(256)
    lut[:, 0, 2] = 255 - np.arange(256)
    r = p.process(img, lf.ViewParams(canvas_w=640, canvas_h=360, denoise=False, negative=lut,
                                     clip="white"))
    q = r.image.convertToFormat(r.image.Format.Format_RGB888)
    px = q.pixelColor(300, 200)
    assert (px.red(), px.green(), px.blue()) == (255 - 210, 255 - 120, 255 - 60)
    assert r.clip is not None and r.clip.pixelColor(10, 5).alpha() > 200
    assert r.clip.pixelColor(10, 200).alpha() == 0
    assert p.sample(300, 200) == pytest.approx((60.0, 120.0, 210.0))
    assert p.sample(5000, 200) is None
    r = p.process(img, lf.ViewParams(canvas_w=640, canvas_h=360, false_colour=True))
    q = r.image.convertToFormat(r.image.Format.Format_RGB888)
    c = q.pixelColor(300, 5)
    assert (c.red(), c.green(), c.blue()) == (240, 30, 30)          # white clipping band


def test_processor_sharpness_reads_the_fresh_frame_in_the_area():
    t = [0.0]
    p = lf.Processor(clock=lambda: t[0])
    img = _scene(640, 360)
    r = p.process(img, lf.ViewParams(canvas_w=640, canvas_h=360, sharp=True,
                                     sharp_area=(0.25, 0.25, 0.75, 0.75)))
    score, best = r.sharp
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    want = lf.sharpness(grey[90:270, 160:480])
    assert score == pytest.approx(want, rel=1e-4) and best == score
    t[0] += 0.01
    again = p.process(img, lf.ViewParams(canvas_w=640, canvas_h=360, sharp=True,
                                         sharp_area=(0.25, 0.25, 0.75, 0.75)))
    assert again.sharp == r.sharp                                  # at most 30 times a second
    blurred = cv2.GaussianBlur(img, (0, 0), 1.5)
    for _ in range(lf.FocusMeter.MEDIAN // 2 + 1):                  # steadied: a median
        t[0] += 0.1
        r = p.process(blurred, lf.ViewParams(canvas_w=640, canvas_h=360, sharp=True,
                                             sharp_area=(0.25, 0.25, 0.75, 0.75)))
    assert r.sharp[0] < 0.5 * r.sharp[1]                           # % of best fell


# --------------------------------------------------------------------------- #
# the capture thread, with a fake device
# --------------------------------------------------------------------------- #
class FakeCap:
    def __init__(self, index, fail_after=None):
        self.index = index
        self.reads = 0
        self.released = False
        self.fail_after = fail_after

    def read(self):
        time.sleep(0.003)
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            return False, None
        f = np.zeros((90, 160, 3), np.uint8)
        f[..., 1] = self.reads % 256
        return True, f

    def get(self, prop):
        return {cv2.CAP_PROP_FRAME_WIDTH: 160, cv2.CAP_PROP_FRAME_HEIGHT: 90}.get(prop, 0)

    def release(self):
        self.released = True


def _wait(cond, timeout=3.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        _app.processEvents()
        time.sleep(0.005)
    return cond()


def test_feed_reads_once_per_frame_and_the_latest_wins():
    caps = []

    def opener(i, fmt):
        caps.append(FakeCap(i))
        return caps[-1]
    feed = lf.LiveFeed(opener=opener)
    notices = []
    feed.frame_ready.connect(lambda: notices.append(1))
    feed.set_params(lf.ViewParams(canvas_w=160, canvas_h=90, denoise=False))
    feed.start()
    try:
        feed.open_camera(1)
        assert _wait(lambda: caps and caps[-1].reads > 40)
        _wait(lambda: False, 0.05)
        assert len(notices) == 1                 # told once while nothing was taken
        r = feed.take()
        n = caps[-1].reads
        got = r.image.pixelColor(5, 5).green()
        assert abs(got - n % 256) <= 3           # the newest frame, not the first
        assert feed.take() is None
        assert _wait(lambda: len(notices) == 2)  # told again once taken
    finally:
        feed.stop()
        feed.wait(3000)
    assert caps[-1].released


def test_feed_probes_each_camera_once_remembered_first():
    log = []

    def opener(i, fmt):
        log.append(i)
        return FakeCap(i) if i in (2, 4) else None
    feed = lf.LiveFeed(opener=opener)
    found = []
    feed.cameras_found.connect(found.append)
    feed.start()
    try:
        feed.find_cameras(preferred=4)
        assert _wait(lambda: found)
        assert found[0] == [(2, 160, 90), (4, 160, 90)]
        assert log[:7] == [4, 0, 1, 2, 3, 5, 4]   # probe order, then open the remembered one
        assert feed.index == 4
    finally:
        feed.stop()
        feed.wait(3000)


def test_feed_notices_a_device_that_stops_delivering(monkeypatch):
    monkeypatch.setattr(lf, "LOST_AFTER_S", 0.1)
    caps = []

    def opener(i, fmt):
        caps.append(FakeCap(i, fail_after=5))
        return caps[-1]
    feed = lf.LiveFeed(opener=opener)
    st = []
    feed.status.connect(lambda k, t: st.append(k))
    feed.start()
    try:
        feed.open_camera(0)
        assert _wait(lambda: "lost" in st)
        assert caps[-1].released
    finally:
        feed.stop()
        feed.wait(3000)


def test_permission_is_asked_once_and_reported():
    seen = []
    lf.request_camera_permission(_app, seen.append)
    assert _wait(lambda: seen)
    assert seen[0] in (True, False)


# --------------------------------------------------------------------------- #
# the meter, revision 2 (spec/sharpness-meter-v2.md)
# --------------------------------------------------------------------------- #
def _film(h=400, w=600, seed=1):
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 120, np.float32)
    for _ in range(160):
        cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))),
                   int(rng.integers(6, 40)), float(rng.integers(40, 220)), -1)
    img += rng.normal(0, 4, img.shape).astype(np.float32)          # grain
    return np.clip(img, 0, 255)


def test_focus_closeness_spreads_the_top():
    vals = [round(lf.focus_closeness(r), 2) for r in (1.0, 0.99, 0.95, 0.9, 0.5, 0.0)]
    assert vals == [1.0, 0.96, 0.81, 0.66, 0.06, 0.0]
    # a finite slope at best focus: half a per cent of noise moves it ~2 points
    assert (lf.focus_closeness(1.0) - lf.focus_closeness(0.995)) * 100 < 2.5


def test_the_meter_is_steadied_and_its_best_too():
    m = lf.FocusMeter()
    for v in [10] * 10 + [100, 100] + [10] * 3:     # wild readings
        steady, best = m.update(v)
    assert steady == 10 and best == 10


def test_the_area_follows_a_small_drift_and_is_capped():
    img = _film(seed=2)
    tr = lf.AreaTracker()
    box = (200, 120, 360, 280)
    assert tr.follow(img, box) == box
    moved = np.roll(img, (3, 5), axis=(0, 1))                      # down 3, right 5
    got = tr.follow(moved, box)
    assert got == (205, 123, 365, 283)
    other = _film(seed=9)                                          # a different picture
    assert tr.follow(other, box) == got
    tr.dx = 10_000
    tr.prev = None
    assert tr.follow(img, box)[0] <= 200 + 80                      # half the area at most


def test_a_new_frame_is_said_once_after_it_settles():
    t = [0.0]
    d = lf.NewFrameDetector(clock=lambda: t[0])
    a = cv2.resize(_film(seed=1), (64, 36), interpolation=cv2.INTER_AREA)
    b = cv2.resize(_film(seed=7), (64, 36), interpolation=cv2.INTER_AREA)
    assert not d.update(a)
    assert not d.update(cv2.GaussianBlur(a, (0, 0), 1.0))          # focusing isn't new
    said = []
    for _ in range(15):
        t[0] += 0.1
        said.append(d.update(b))
    assert said.count(True) == 1 and said.index(True) >= 8         # after it settled
    for _ in range(20):
        t[0] += 0.1
        assert not d.update(b)                                     # a still picture: no more


# --------------------------------------------------------------------------- #
# a new frame on 35 mm: look inside the frame (spec/sharpness-meter-v2.md
# revision 3)
# --------------------------------------------------------------------------- #
def _strip_kit():
    sys.path.insert(0, os.path.dirname(__file__))
    from test_guide_tracker import strip, HAND, WIND
    return strip, HAND, WIND


def _feed(d, frames, box, t, dt=0.1):
    said = []
    for f in frames:
        t[0] += dt
        b = box(f) if callable(box) else box
        said.append(d.update(lf.new_frame_view(f), b))
    return said


class _SnapAfter:
    """The guide's box, then (after `n` pictures) the box moved: the guide
    snapping onto the new frame while it settles."""

    def __init__(self, before, after, n):
        self.before, self.after, self.n, self.k = before, after, n, 0

    def __call__(self, _frame):
        self.k += 1
        return self.before if self.k <= self.n else self.after


def test_the_new_frame_check_looks_inside_the_guide_or_the_middle():
    g = {"cx": 0.5, "cy": 0.5, "w": 0.5, "h": 0.4, "angle": 2.0}
    assert lf.new_frame_box(320, 240, g) == (96, 82, 224, 158)       # its inner 80 %
    middle = (64, 48, 256, 192)                                        # the middle 60 %
    assert lf.new_frame_box(320, 240) == middle
    for bad in ({"cx": 0.5}, {"cx": 3.0, "cy": 0.5, "w": 0.3, "h": 0.3},
                {"cx": "x", "cy": 0.5, "w": 0.3, "h": 0.3}, {"cx": 0.5, "cy": 0.5, "w": 0.01, "h": 0.5}):
        assert lf.new_frame_box(320, 240, bad) == middle
    v = lf.new_frame_view(np.zeros((768, 1024, 3), np.uint8))
    assert v.shape == (240, 320) and v.dtype == np.float32
    small = lf.new_frame_view(np.zeros((36, 64), np.uint8))
    assert small.shape == (36, 64)                                     # never enlarged


def test_winding_on_a_35mm_strip_is_seen_inside_the_guide():
    """The sprocket rows, rebate and carrier fill the whole view and are the
    same in every frame: across the whole view (the old way) two frames
    correlate ~0.92 and nothing is said; inside the guide, once."""
    strip, HAND, WIND = _strip_kit()
    still, nxt = strip(), strip(offset=-WIND)
    moving = [strip(offset=-(WIND * k) // 6) for k in range(1, 6)]
    w, h = lf.new_frame_view(still).shape[::-1]
    guide = lf.new_frame_box(w, h, HAND)
    snapped = lf.new_frame_box(w, h, dict(HAND, cx=HAND["cx"] + 5.0 / 1024))
    for box, want in ((None, 0), (guide, 1),
                      (_SnapAfter(guide, snapped, 10), 1)):   # a snap while it settles
        t = [0.0]
        d = lf.NewFrameDetector(clock=lambda: t[0])
        said = _feed(d, [still] * 3 + moving + [nxt] * 15, box, t)
        assert said.count(True) == want, box
        if want:
            assert said.index(True) >= 3 + len(moving) + 8                # after it settled


def test_focusing_and_moving_the_guide_are_not_a_new_frame():
    strip, HAND, _WIND = _strip_kit()
    still = strip()
    hh, ww = still.shape[:2]
    m = np.float32([[1.03, 0, -0.015 * ww], [0, 1.03, -0.015 * hh]])  # 3 % larger, blurred
    focus = cv2.GaussianBlur(cv2.warpAffine(still, m, (ww, hh)), (0, 0), 2.0)
    w, h = lf.new_frame_view(still).shape[::-1]
    t = [0.0]
    d = lf.NewFrameDetector(clock=lambda: t[0])
    assert not any(_feed(d, [still] * 3 + [focus] * 12, lf.new_frame_box(w, h, HAND), t))
    # the guide dragged a fifth of the view on a still picture: the same box
    # is cut from the picture kept, so it is the same picture
    dragged = lf.new_frame_box(w, h, dict(HAND, cx=HAND["cx"] - 0.2, w=HAND["w"] * 0.6))
    assert not any(_feed(d, [focus] * 12, dragged, t))


def test_the_feed_looks_for_a_new_frame_inside_the_guide_it_is_given():
    strip, HAND, WIND = _strip_kit()
    still, nxt = strip(), strip(offset=-WIND)
    t = [0.0]
    p = lf.Processor(clock=lambda: t[0])
    said = []
    for f in [still] * 3 + [strip(offset=-WIND // 2)] + [nxt] * 14:
        t[0] += 0.1
        r = p.process(f, lf.ViewParams(canvas_w=1024, canvas_h=768, sharp=True, denoise=False,
                                       frame_area=dict(HAND)))
        said.append(r.new_frame)
    assert said.count(True) == 1


def test_the_view_can_zoom_well_past_fit_on_a_small_live_view():
    # a 1024 px USB live view on a Retina-sized canvas fits at ~2.15
    fs = lf.fit_scale(1024, 768, 2200, 1878)
    g = lf.resolve_view(1024, 768, 2200, 1878, fs * 8, 512, 384)
    assert g.scale == pytest.approx(fs * 8, rel=1e-3)
    assert lf.max_scale(0.5) == lf.MAX_SCALE                        # big frames: as before
