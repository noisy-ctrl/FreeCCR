"""The live camera feed of the Live Capture Monitor
(spec/live-capture-monitor.md §5).

`LiveFeed` is a `WorkerThread`: it opens the capture device, loops on ONE
blocking read per frame (the device sets the pace; no timer, no extra
grabs), processes the newest frame and leaves the result in a one-slot
mailbox: the GUI is told once and takes the latest, so frames are dropped
when the GUI is behind, never queued.

Everything else here is plain functions and small classes, Qt-free apart
from building the QImage, so it is tested without a camera:
- `resolve_view` / `Geometry`: zoom and pan, and the mapping between canvas
  device pixels and sensor pixels;
- `Denoiser`: the running average that resets itself when the picture moves;
- `edge_amplify`: an unsharp mask on luma only;
- `sharpness`: Laplacian variance over mean luma squared;
- `Activity`: whether the picture changed (film moved, focus turned).

macOS: OpenCV can't ask for camera permission from a background thread, so
the GUI asks first (`request_camera_permission`) and the capture opens with
OPENCV_AVFOUNDATION_SKIP_AUTH=1.
"""
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from utils.qt_threads import WorkerThread  # noqa: E402

MAX_SCALE = 4.0             # display device pixels per sensor pixel
MAX_INDEX = 5               # cameras probed: 0..5
REQUEST = (1920, 1080, 60)
DENOISE_DEFAULT = {"strength": 35}            # percent; the running average's weight
DENOISE_LIMITS = {"strength": (5, 100)}
EDGE_DEFAULT = {"amount": 150, "radius": 12}  # percent; tenths of a pixel
EDGE_LIMITS = {"amount": (0, 300), "radius": (5, 30)}
MOTION_LEVELS = 1.5         # mean |difference| of a 64x36 thumbnail, 8-bit levels
FOCUS_CHANGE = 0.10         # a 10 % change of the focus measure in a second
HIST_EVERY_S = 0.25
SHARP_EVERY_S = 1 / 30.0    # the meter needs no more
LOST_AFTER_S = 2.0          # no frame for this long: the device is gone
LUMA = np.array([0.0722, 0.7152, 0.2126], np.float32)   # B, G, R (Rec.709)
_LUMA_M = LUMA.reshape(1, 3)
FORMATS = ("auto", "raw", "mjpeg")


def clean_options(opts, default, limits):
    out = {}
    for k, d in default.items():
        lo, hi = limits[k]
        try:
            v = int(round(float((opts or {}).get(k, d))))
        except (TypeError, ValueError):
            v = d
        out[k] = max(lo, min(hi, v))
    return out


# --------------------------------------------------------------------------- #
# view geometry
# --------------------------------------------------------------------------- #
def fit_scale(full_w, full_h, canvas_w, canvas_h):
    if full_w <= 0 or full_h <= 0 or canvas_w <= 0 or canvas_h <= 0:
        return 1.0
    return min(canvas_w / float(full_w), canvas_h / float(full_h))


@dataclass
class Geometry:
    """What one rendered frame shows: the sensor rect [x0, x1) x [y0, y1),
    drawn at (ox, oy) on the canvas with size (out_w, out_h), all canvas
    values in device pixels."""
    full_w: int
    full_h: int
    x0: int
    y0: int
    x1: int
    y1: int
    ox: float
    oy: float
    out_w: int
    out_h: int
    fit: bool = True

    @property
    def sx(self):
        return self.out_w / float(max(1, self.x1 - self.x0))

    @property
    def sy(self):
        return self.out_h / float(max(1, self.y1 - self.y0))

    @property
    def scale(self):
        return 0.5 * (self.sx + self.sy)

    def to_sensor(self, px, py):
        return (self.x0 + (px - self.ox) / self.sx, self.y0 + (py - self.oy) / self.sy)

    def to_canvas(self, sx, sy):
        return (self.ox + (sx - self.x0) * self.sx, self.oy + (sy - self.y0) * self.sy)

    def contains_canvas(self, px, py):
        return (self.ox <= px < self.ox + self.out_w) and (self.oy <= py < self.oy + self.out_h)


def resolve_view(full_w, full_h, canvas_w, canvas_h, scale=None, cx=None, cy=None):
    """The Geometry for a zoom (`scale` device px per sensor px, None = fit)
    centred on sensor (cx, cy) (None = the middle), clamped so the view
    stays on the frame."""
    canvas_w, canvas_h = max(1, int(canvas_w)), max(1, int(canvas_h))
    fs = fit_scale(full_w, full_h, canvas_w, canvas_h)
    s = fs if scale is None else max(fs, min(MAX_SCALE, float(scale)))
    fit = s <= fs * 1.0001
    cx = full_w / 2.0 if cx is None else float(cx)
    cy = full_h / 2.0 if cy is None else float(cy)

    def axis(full, canvas, c):
        half = canvas / (2.0 * s)
        if 2 * half >= full:
            lo, hi = 0, full
            off = (canvas - full * s) / 2.0
            return lo, hi, off, full * s
        c = max(half, min(full - half, c))
        lo = int(math.floor(c - half))
        hi = int(math.ceil(c + half))
        lo, hi = max(0, lo), min(full, hi)
        off = canvas / 2.0 - (c - lo) * s
        return lo, hi, off, (hi - lo) * s

    x0, x1, ox, w = axis(full_w, canvas_w, cx)
    y0, y1, oy, h = axis(full_h, canvas_h, cy)
    return Geometry(full_w, full_h, x0, y0, x1, y1, ox, oy,
                    max(1, int(round(w))), max(1, int(round(h))), fit)


# --------------------------------------------------------------------------- #
# processing
# --------------------------------------------------------------------------- #
def _thumb(frame):
    """A 64x36 grey thumbnail: every thumbnail pixel averages hundreds of
    frame pixels, so sensor noise can't look like movement."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return cv2.resize(g, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


class Denoiser:
    """`acc += s * (frame - acc)`. Restarts when the picture moves (a 64x36
    thumbnail changes by more than MOTION_LEVELS on average), when the view
    changes (`key`), and on any change of size, so moving the film never
    smears the old position across the view."""

    def __init__(self):
        self.acc = None
        self.thumb = None
        self.key = None

    def reset(self):
        self.acc = None
        self.thumb = None

    def apply(self, frame, strength, key=None):
        thumb = _thumb(frame)
        moved = (self.thumb is not None and self.thumb.shape == thumb.shape
                 and float(np.mean(np.abs(thumb - self.thumb))) > MOTION_LEVELS)
        self.thumb = thumb
        if (self.acc is None or self.acc.shape != frame.shape or key != self.key
                or moved or strength >= 1.0):
            self.key = key
            self.acc = frame.astype(np.float32)
            return frame, moved
        cv2.accumulateWeighted(frame, self.acc, float(strength))
        return cv2.convertScaleAbs(self.acc), moved     # rounds and saturates


def luma(bgr):
    return np.asarray(bgr, np.float32) @ LUMA


def luma8(bgr):
    """Rec.709 luma of an 8-bit BGR picture, rounded to uint8 (fast)."""
    return cv2.transform(bgr, _LUMA_M)


def edge_amplify(bgr, amount, radius):
    """Unsharp mask on luma only: the luma detail (Y - blur(Y)) times
    amount/100 is added equally to B, G and R, so the colour difference
    channels are untouched (only clipping at 0 / 255 can change them) and
    colour noise is not amplified. (Any luma weights summing to 1 keep that
    property, so OpenCV's fast grey conversion is used.) 8-bit / 16-bit
    OpenCV arithmetic, so it keeps up with the feed."""
    if amount <= 0:
        return bgr
    y = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(y, (0, 0), max(0.1, float(radius)))
    detail = cv2.subtract(y, blur, dtype=cv2.CV_16S)
    detail = cv2.multiply(detail, amount / 100.0, dtype=cv2.CV_16S)
    return cv2.add(bgr, cv2.merge([detail, detail, detail]), dtype=cv2.CV_8U)


def sharpness(grey):
    """Focus measure: variance of the Laplacian of the lightly blurred luma,
    divided by the mean luma squared. A brighter or dimmer light scales the
    picture, which scales the raw variance by the square; dividing it out
    keeps the measure on focus."""
    g = np.asarray(grey)
    if g.ndim == 3:
        g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY) if g.dtype == np.uint8 else luma(g)
    # blur and Laplacian in float: rounding the blurred picture to 8 bits
    # would add a "detail" of its own that doesn't scale with brightness
    g = g.astype(np.float32)
    if g.size < 9:
        return 0.0
    b = cv2.GaussianBlur(g, (3, 3), 0)
    lap = cv2.Laplacian(b, cv2.CV_32F)
    _m, sd = cv2.meanStdDev(lap)
    m = float(cv2.mean(b)[0])
    return float(sd[0, 0]) ** 2 / (m * m + 1.0)


class SharpnessBest:
    """The best score for one area and zoom (`key`); a new key starts over."""

    def __init__(self):
        self.key = None
        self.best = 0.0

    def update(self, key, score):
        if key != self.key:
            self.key, self.best = key, 0.0
        self.best = max(self.best, score)
        return self.best

    def reset(self):
        self.key, self.best = None, 0.0


class Activity:
    """Is someone using the picture? True when it moved (the Denoiser's test)
    or the focus measure of the middle of the frame changed by more than
    FOCUS_CHANGE since a second ago (the focus ring turned)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.ref = None
        self.ref_t = None

    def update(self, frame, moved):
        now = self.clock()
        if moved:
            self.ref, self.ref_t = None, None
            return True
        if self.ref_t is not None and now - self.ref_t < 1.0:
            return False
        h, w = frame.shape[:2]
        mid = frame[h // 4: h - h // 4, w // 4: w - w // 4]
        small = cv2.resize(mid, (max(1, mid.shape[1] // 2), max(1, mid.shape[0] // 2)),
                           interpolation=cv2.INTER_AREA)
        s = sharpness(small)
        changed = self.ref is not None and abs(s - self.ref) > FOCUS_CHANGE * max(self.ref, 1e-9)
        self.ref, self.ref_t = s, now
        return changed


def histogram_rgb(bgr):
    """(3, 256) counts, R, G, B, from a frame reduced to <= 480 px."""
    h, w = bgr.shape[:2]
    k = max(1, int(math.ceil(max(h, w) / 480.0)))
    small = cv2.resize(bgr, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA) if k > 1 else bgr
    out = np.zeros((3, 256), np.float32)
    for i, c in enumerate((2, 1, 0)):
        out[i] = cv2.calcHist([small], [c], None, [256], [0, 256]).ravel()
    return out


_FC_LUT = None


def false_colour_bgr(bgr):
    """FreeCCR's false-colour bands (core.false_colour) on an 8-bit BGR
    picture, as one table lookup on the rounded luma, so it keeps up with
    the feed. Same bands; a pixel within half a level of a band edge may
    fall either side."""
    global _FC_LUT
    if _FC_LUT is None:
        from core.false_colour import BANDS
        lut = np.zeros((256, 1, 3), np.uint8)
        for v in range(256):
            y = v / 255.0
            col = (v, v, v)
            for lo, hi, rgb, _l, _m in BANDS:
                if lo <= y < hi:
                    col = rgb
            lut[v, 0] = (col[2], col[1], col[0])
        _FC_LUT = lut
    return cv2.applyColorMap(luma8(bgr), _FC_LUT)


def to_qimage(bgr):
    bgr = np.ascontiguousarray(bgr)
    h, w = bgr.shape[:2]
    return QImage(bgr.data, w, h, 3 * w, QImage.Format_BGR888).copy()


def rgba_qimage(rgba):
    rgba = np.ascontiguousarray(rgba)
    h, w = rgba.shape[:2]
    return QImage(rgba.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()


@dataclass
class ViewParams:
    """What the GUI wants rendered. Read by the worker once per frame."""
    canvas_w: int = 960
    canvas_h: int = 540
    scale: Optional[float] = None     # None = fit
    cx: Optional[float] = None
    cy: Optional[float] = None
    denoise: bool = True
    denoise_opts: dict = field(default_factory=lambda: dict(DENOISE_DEFAULT))
    edge: bool = False
    edge_opts: dict = field(default_factory=lambda: dict(EDGE_DEFAULT))
    sharp: bool = False
    sharp_area: Optional[tuple] = None   # sensor fractions (x0, y0, x1, y1)
    false_colour: bool = False
    negative: Optional[np.ndarray] = None  # (256, 1, 3) uint8 BGR tables
    bw: bool = False
    clip: str = ""                      # "black" / "white" while hovered
    reset_best: int = 0                 # bump to reset the sharpness best


@dataclass
class FrameResult:
    image: QImage
    geometry: Geometry
    fps: float = 0.0
    sharp: Optional[tuple] = None      # (score, best)
    hist: Optional[np.ndarray] = None
    clip: Optional[QImage] = None
    activity: bool = False
    elapsed_ms: float = 0.0


class Processor:
    """One frame in, one FrameResult out (spec §5.5). Keeps the state that
    spans frames: the running average, the sharpness best, the activity
    test, the histogram timer."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.denoiser = Denoiser()
        self.best = SharpnessBest()
        self.activity = Activity(clock)
        self._hist_t = -1e9
        self._sharp_t = -1e9
        self._sharp_last = None
        self._reset_best = 0
        # (steady crop, its sensor rect (x0, y0, x1, y1), the full frame),
        # replaced as one tuple so a reader on another thread never sees a
        # crop with the wrong rect.
        self.last = (None, None, None)

    @property
    def last_full(self):
        return self.last[2]

    def process(self, frame, p: ViewParams, fps=0.0):
        t0 = time.perf_counter()
        full_h, full_w = frame.shape[:2]
        g = resolve_view(full_w, full_h, p.canvas_w, p.canvas_h, p.scale, p.cx, p.cy)
        fresh = frame[g.y0:g.y1, g.x0:g.x1]
        rect = (g.x0, g.y0, g.x1, g.y1)
        if p.denoise:
            s = clean_options(p.denoise_opts, DENOISE_DEFAULT, DENOISE_LIMITS)["strength"] / 100.0
            steady, moved = self.denoiser.apply(fresh, s, key=rect)
        else:
            self.denoiser.reset()
            steady = fresh
            th = _thumb(fresh)
            prev = getattr(self, "_thumb", None)
            moved = (prev is not None and prev.shape == th.shape
                     and float(np.mean(np.abs(th - prev))) > MOTION_LEVELS)
            self._thumb = th
        self.last = (steady, rect, frame)
        activity = self.activity.update(fresh, moved)

        # measurements
        sharp = None
        if p.sharp:
            area = fresh
            key = (rect, p.sharp_area)
            if p.sharp_area:
                fx0, fy0, fx1, fy1 = p.sharp_area
                ax0 = int(max(g.x0, min(g.x1 - 1, fx0 * full_w)))
                ax1 = int(max(ax0 + 1, min(g.x1, fx1 * full_w)))
                ay0 = int(max(g.y0, min(g.y1 - 1, fy0 * full_h)))
                ay1 = int(max(ay0 + 1, min(g.y1, fy1 * full_h)))
                area = frame[ay0:ay1, ax0:ax1]
            if p.reset_best != self._reset_best:
                self._reset_best = p.reset_best
                self.best.reset()
                self._sharp_last = None
            now_s = self.clock()
            if (self._sharp_last is None or self._sharp_last[0] != key
                    or now_s - self._sharp_t >= SHARP_EVERY_S):
                self._sharp_t = now_s
                score = sharpness(area)
                self._sharp_last = (key, (score, self.best.update(key, score)))
            sharp = self._sharp_last[1]
        hist = None
        now = self.clock()
        if now - self._hist_t >= HIST_EVERY_S:
            self._hist_t = now
            hist = histogram_rgb(steady)

        # the picture
        shown = steady
        if p.negative is not None:
            shown = cv2.LUT(shown, p.negative)
            if p.bw:
                y = cv2.cvtColor(shown, cv2.COLOR_BGR2GRAY)
                shown = cv2.cvtColor(y, cv2.COLOR_GRAY2BGR)
        if p.edge:
            e = clean_options(p.edge_opts, EDGE_DEFAULT, EDGE_LIMITS)
            shown = edge_amplify(shown, e["amount"], e["radius"] / 10.0)
        size = (g.out_w, g.out_h)
        interp = cv2.INTER_AREA if g.out_w < shown.shape[1] else cv2.INTER_LINEAR
        shown = cv2.resize(shown, size, interpolation=interp) if size != (shown.shape[1], shown.shape[0]) else shown
        if p.false_colour:
            shown = false_colour_bgr(shown)
        clip = None
        if p.clip in ("black", "white"):
            from core.clip_overlay import clip_rgba
            st = steady
            if size != (st.shape[1], st.shape[0]):
                st = cv2.resize(st, size, interpolation=cv2.INTER_NEAREST)
            clip = rgba_qimage(clip_rgba(cv2.cvtColor(st, cv2.COLOR_BGR2RGB), p.clip))
        return FrameResult(to_qimage(shown), g, fps, sharp, hist, clip, activity,
                           (time.perf_counter() - t0) * 1000.0)

    def sample(self, sx, sy, half=6):
        """Mean B, G, R of the steady frame around sensor (sx, sy), or None
        when that point isn't in view."""
        st, rect, _full = self.last
        if st is None or rect is None:
            return None
        x0, y0, x1, y1 = rect
        if not (x0 <= sx < x1 and y0 <= sy < y1):
            return None
        cx, cy = int(sx - x0), int(sy - y0)
        a = st[max(0, cy - half):cy + half + 1, max(0, cx - half):cx + half + 1]
        if a.size == 0:
            return None
        return tuple(float(v) for v in a.reshape(-1, 3).mean(axis=0))


# --------------------------------------------------------------------------- #
# the device
# --------------------------------------------------------------------------- #
def _cv_open(index, fmt="auto"):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    try:
        if fmt == "mjpeg":
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        elif fmt == "raw":
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQUEST[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQUEST[1])
        cap.set(cv2.CAP_PROP_FPS, REQUEST[2])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap


def fourcc_text(cap):
    try:
        v = int(cap.get(cv2.CAP_PROP_FOURCC))
    except Exception:
        return ""
    if v <= 0:
        return ""
    s = "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))
    return s if s.isprintable() and s.strip() else ""


class LiveFeed(WorkerThread):
    """The capture thread. Commands (`open_camera`, `find_cameras`, `close`,
    `stop`) are queued; parameters (`set_params`) are read once per frame.
    `frame_ready` fires once per batch of new frames; `take()` returns the
    latest FrameResult and clears the notice."""

    frame_ready = Signal()
    cameras_found = Signal(list)       # [(index, width, height), ...]  (OpenCV devices)
    status = Signal(str, str)          # kind, text: searching/open/none/lost/closed

    def __init__(self, opener=None, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self._opener = opener or _cv_open
        # Other sources by key prefix: "usb" -> UsbCamera.frame_source
        # (spec/capture-routes.md §5.3). A source's key is a str "prefix:…".
        self.sources = {}
        self._clock = clock
        self._lock = threading.Lock()
        self._cmds = []
        self._wake = threading.Event()
        self._params = ViewParams()
        self._latest = None
        self._notified = False
        self._stop = False
        self.processor = Processor(clock)
        self.index = None
        self.size = None
        self.fourcc = ""

    # -- GUI side ---------------------------------------------------------
    def set_params(self, params: ViewParams):
        with self._lock:
            self._params = params

    def params(self):
        with self._lock:
            return self._params

    def open_camera(self, key, fmt="auto"):
        """An OpenCV device index (int) or a source key ("usb:…")."""
        self._command(("open", key if isinstance(key, str) else int(key), fmt))

    def find_cameras(self, preferred=None, fmt="auto", open_found=True):
        """Probe the OpenCV devices; open the preferred (or first) one unless
        `open_found` is False (another source is wanted)."""
        self._command(("find", preferred, fmt, open_found))

    def close_camera(self):
        self._command(("close",))

    def stop(self):
        self._stop = True
        self._command(("stop",))

    def take(self):
        with self._lock:
            r, self._latest = self._latest, None
            self._notified = False
        return r

    def sample(self, sx, sy):
        with self._lock:
            return self.processor.sample(sx, sy)

    def last_full_frame(self):
        with self._lock:
            f = self.processor.last_full
            return None if f is None else f.copy()

    def _command(self, cmd):
        with self._lock:
            self._cmds.append(cmd)
        self._wake.set()

    # -- worker side ------------------------------------------------------
    def run(self):
        cap = None
        fails_since = None
        frames, window_t, fps = 0, self._clock(), 0.0
        while not self._stop:
            with self._lock:
                cmds, self._cmds = self._cmds, []
            for cmd in cmds:
                if cmd[0] == "stop":
                    self._stop = True
                elif cmd[0] == "close":
                    cap = self._release(cap)
                    self.status.emit("closed", "")
                elif cmd[0] == "open":
                    cap = self._release(cap)
                    cap = self._open(cmd[1], cmd[2])
                    fails_since = None
                elif cmd[0] == "find":
                    cap = self._release(cap)
                    found = self._probe(cmd[1], cmd[2])
                    self.cameras_found.emit(found)
                    if found and cmd[3]:
                        idx = cmd[1] if cmd[1] in [f[0] for f in found] else found[0][0]
                        cap = self._open(idx, cmd[2])
                    elif not found:
                        self.status.emit("none", "")
            if self._stop:
                break
            if cap is None:
                self._wake.wait(0.1)
                self._wake.clear()
                continue
            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None
            now = self._clock()
            if not ok or frame is None or frame.ndim != 3:
                alive = getattr(cap, "alive", None)
                if alive is not None and alive():
                    fails_since = None        # a source pausing (opening, capturing)
                    continue
                fails_since = fails_since or now
                if now - fails_since >= LOST_AFTER_S:
                    cap = self._release(cap)
                    self.status.emit("lost", "")
                else:
                    time.sleep(0.005)
                continue
            fails_since = None
            frames += 1
            if now - window_t >= 1.0:
                fps = frames / (now - window_t)
                frames, window_t = 0, now
            params = self.params()
            try:
                result = self.processor.process(frame, params, fps)
            except Exception as e:  # never let one bad frame kill the feed
                print(f"live feed: frame skipped ({e})")
                continue
            if os.environ.get("FREECCR_LIVE_TIMING") == "1":
                print(f"live feed: {result.elapsed_ms:.1f} ms")
            with self._lock:
                self._latest = result
                notify = not self._notified
                self._notified = True
            if notify:
                self.frame_ready.emit()
        self._release(cap)

    def _open(self, index, fmt):
        source = None
        if isinstance(index, str):
            source = self.sources.get(index.split(":", 1)[0])
        if isinstance(index, str):
            self.status.emit("searching", "Opening the camera…")
        else:
            self.status.emit("searching", f"Opening camera {index}…")
        try:
            if isinstance(index, str):
                cap = source(index, fmt) if source is not None else None
            else:
                cap = self._opener(index, fmt)
        except Exception:
            cap = None
        if cap is None:
            self.status.emit("none", f"Camera {index} did not open")
            return None
        self.index = index
        try:
            self.size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        except Exception:
            self.size = None
        if self.size and min(self.size) <= 0:
            self.size = None                  # not known until the first frame
        self.fourcc = fourcc_text(cap)
        with self._lock:
            self.processor.denoiser.reset()
            self.processor.best.reset()
        self.status.emit("open", f"{index}")
        return cap

    def _probe(self, preferred, fmt):
        """Each index once, released before the next; the remembered one
        first. A device counts only if it delivers a frame."""
        self.status.emit("searching", "Looking for cameras…")
        order = list(range(MAX_INDEX + 1))
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        found = []
        for idx in order:
            if self._stop:
                break
            try:
                cap = self._opener(idx, fmt)
            except Exception:
                cap = None
            if cap is None:
                continue
            try:
                ok, frame = cap.read()
                if ok and frame is not None:
                    found.append((idx, int(frame.shape[1]), int(frame.shape[0])))
            except Exception:
                pass
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
        found.sort(key=lambda f: f[0])
        return found

    @staticmethod
    def _release(cap):
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None


# --------------------------------------------------------------------------- #
# camera permission (macOS): asked on the main thread
# --------------------------------------------------------------------------- #
def request_camera_permission(app, done):
    """Calls done(True / False / None) on the main thread: True granted (or
    not applicable), False refused before (System Settings), None asked but
    not confirmed. Qt's macOS permission request needs the camera usage
    text in the app's Info.plist; FreeCCR run from source has none, so its
    answer can't be trusted and the cameras are simply tried (None)."""
    try:
        from PySide6.QtCore import QCameraPermission, Qt as _Qt
        perm = QCameraPermission()
        status = app.checkPermission(perm)
        if status == _Qt.PermissionStatus.Granted:
            done(True)
            return
        if status == _Qt.PermissionStatus.Denied:
            done(False)
            return

        def answered(*_a):
            try:
                ok = app.checkPermission(QCameraPermission()) == _Qt.PermissionStatus.Granted
            except Exception:
                ok = False
            done(True if ok else None)
        app.requestPermission(perm, app, answered)
    except Exception:
        done(True)
