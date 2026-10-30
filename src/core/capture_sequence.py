"""Shutter shots and the trichrome sequence (spec/live-capture-monitor.md §8).

A small state machine driven by one-shot timers. The light is anything with
`set_levels(dict)`, `focus(bool)`, `pulse(ms)` and `is_open()`; the timer
(`schedule(ms, fn)`) and the clock are injectable, so it is tested with a
fake clock and a fake light.

One shot: focus on, wait Focus (never less than LEAD_MIN_MS after the colour
was set), pulse, wait Pulse + Hold, focus off. Shots start at least Period
apart: a shot waits, it is never dropped. Trichrome: R, G, then B, each lit
alone at the preset's value, then White at the preset's value for
positioning the next frame. Stop finishes a pulse already fired (never cuts
one) and skips the rest; a light that disconnects stops it.

With a film base (spec/film-base-exposure.md) a camera-shutter run takes
`exposures`: each colour's LED levels and shutter speed / ISO, set before its
shot, the exposure handed to the shutter with the fire so the camera takes it
first.
"""
import time

from core import capture_light as cl

TIMING_DEFAULT = {"focus_ms": 200, "pulse_ms": 300, "hold_ms": 200, "period_ms": 1000}
TIMING_LIMITS = {"focus_ms": (0, 2000), "pulse_ms": (10, cl.PULSE_MAX_MS),
                 "hold_ms": (0, 5000), "period_ms": (0, 5000)}
LEAD_MIN_MS = 50            # the colour is set at least this long before a pulse
TRICHROME = ("r", "g", "b")
# A camera shutter (spec/capture-routes.md §5.4): fire, then wait for the file.
FILE_TIMEOUT_S = 30.0
USB_SHOT_EST_MS = 2000      # firing to the file landing, for the progress ring


def clean_timing(t):
    out = {}
    for k, dflt in TIMING_DEFAULT.items():
        lo, hi = TIMING_LIMITS[k]
        try:
            v = int(round(float((t or {}).get(k, dflt))))
        except (TypeError, ValueError):
            v = dflt
        out[k] = max(lo, min(hi, v))
    return out


def shot_ms(timing):
    t = clean_timing(timing)
    return max(t["focus_ms"], LEAD_MIN_MS) + t["pulse_ms"] + t["hold_ms"]


def settle_ms(timing):
    """With a camera shutter: the wait between setting a colour and firing
    (the Focus field)."""
    return max(clean_timing(timing)["focus_ms"], LEAD_MIN_MS)


def cycle_ms(timing, trichrome=True, camera=False):
    """How long a Shoot takes, start to finish (the gaps Period enforces
    included). With a camera shutter it is an estimate: the file decides."""
    t = clean_timing(timing)
    one = settle_ms(t) + USB_SHOT_EST_MS if camera else shot_ms(t)
    n = 3 if trichrome else 1
    return one + (n - 1) * max(one, t["period_ms"])


def hold_covers(timing, exposure_s):
    """False when the camera's exposure is longer than Hold: the next colour
    would light while the shutter is still open. True if unknown."""
    if not exposure_s or exposure_s <= 0:
        return True
    return clean_timing(timing)["hold_ms"] >= exposure_s * 1000.0


def exposure_seconds(path):
    """The shutter time in a capture's EXIF, or None."""
    try:
        from core.it8_profile import read_shot_exposure
        sec, _iso = read_shot_exposure(path)
        return sec
    except Exception:
        return None


def _exposure_s(exp):
    from core.base_exposure import seconds
    return (seconds((exp or {}).get("shutter")) or 0.0) if exp else 0.0


def _qt_schedule(ms, fn):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(max(0, int(round(ms))), fn)


class CaptureSequence:
    """`on_step(channel, duration_ms)` when a shot starts (channel is r/g/b
    in trichrome, None for a single shot); `on_finished(reason)` with
    "done", "stopped", "disconnected", "timeout" or "failed".

    With a `shutter` (an object with `fire()`: a USB camera) a shot is
    colour -> settle -> fire -> wait for `frame_done()` (its file landed) or
    FILE_TIMEOUT_S; the light is needed only to set trichrome colours.
    Without one, the Scanlight's focus/pulse timers as before.

    `exposures` (camera shutter only): {"r"/"g"/"b" or "w": {"shutter",
    "iso", "levels"}}: a colour lights at its own levels and the shutter gets
    `fire(exposure)`; the file timeout and the step's duration include the
    exposure time."""

    def __init__(self, light, schedule=None, clock=time.monotonic,
                 on_step=None, on_finished=None):
        self.light = light
        self._schedule = schedule or _qt_schedule
        self._clock = clock
        self.on_step = on_step
        self.on_finished = on_finished
        self.running = False
        self._stop = False
        self._queue = []
        self._timing = dict(TIMING_DEFAULT)
        self._preset = {}
        self._trichrome = True
        self._last_shot = None       # clock() when the last shot started
        self._token = 0              # voids timers of a finished run
        self._phase = "idle"         # idle | waiting | focus | pulse | settle | exposing
        self._shutter = None
        self._shot = 0               # voids a shot's file timeout once it is done
        self.exposures = None
        self._channel = None         # the colour of the shot in flight

    def start(self, timing, preset, trichrome=True, shutter=None, exposures=None):
        if self.running:
            return False
        self._shutter = shutter
        self.exposures = ({k: dict(v) for k, v in exposures.items() if isinstance(v, dict)}
                          if (exposures and shutter is not None) else None)
        self._trichrome = bool(trichrome)
        if not self._light_ok():
            self._finish("disconnected", restore=False)
            return False
        self.running = True
        self._stop = False
        self._token += 1
        self._timing = clean_timing(timing)
        self._preset = {k: int((preset or {}).get(k, 0)) for k in cl.CHANNELS}
        self._queue = list(TRICHROME) if trichrome else [None]
        self._next()
        return True

    def stop(self):
        """Stop now, or, with a pulse already fired, as soon as it and its
        Hold are over (a pulse is never cut). Nothing more is fired."""
        if not self.running:
            return
        self._stop = True
        if self._phase == "focus":
            self.light.focus(False)
            self._finish("stopped")
        elif self._phase in ("waiting", "settle"):
            self._finish("stopped")
        # "exposing": the shot's file is awaited, then it stops

    def frame_done(self, path=None):
        """The camera's file for the shot in flight has landed."""
        if not self.running or self._phase != "exposing":
            return
        self._shot += 1
        self._phase = "idle"
        self._next()

    @property
    def camera_shutter(self):
        """True while running (or last run) with a camera shutter."""
        return self._shutter is not None

    def abort(self, reason):
        """End now (the camera or light is gone): nothing more can land."""
        if self.running:
            self._finish(reason)

    def fire_failed(self, reason=""):
        if self.running and self._phase == "exposing":
            self._finish("failed")

    def _light_ok(self):
        if self._shutter is not None and not self._trichrome:
            return True              # a single shot uses the light as it is
        return self.light.is_open()

    # -- steps ------------------------------------------------------------
    def _later(self, ms, fn):
        token = self._token
        self._schedule(ms, lambda: (fn() if token == self._token else None))

    def _next(self):
        if self._stop:
            self._finish("stopped")
            return
        if not self._light_ok():
            self._finish("disconnected")
            return
        if not self._queue:
            self._finish("done")
            return
        now = self._clock()
        if self._last_shot is not None:
            wait = self._timing["period_ms"] - (now - self._last_shot) * 1000.0
            if wait > 0:
                self._phase = "waiting"
                self._later(wait, self._next)
                return
        channel = self._queue.pop(0)
        self._channel = channel
        exp = self._exposure_for(channel)
        if channel is not None:
            lv = (exp or {}).get("levels")
            self.light.set_levels(dict(lv) if lv else cl.solo(self._preset, channel))
        elif exp and exp.get("levels") and self.light.is_open():
            self.light.set_levels(dict(exp["levels"]))
        self._last_shot = self._clock()
        if self._shutter is not None:
            if self.on_step:
                self.on_step(channel, settle_ms(self._timing) + USB_SHOT_EST_MS
                             + 1000.0 * _exposure_s(exp))
            self._phase = "settle"
            self._later(settle_ms(self._timing), self._fire_camera)
            return
        if self.on_step:
            self.on_step(channel, shot_ms(self._timing))
        self.light.focus(True)
        self._phase = "focus"
        self._later(max(self._timing["focus_ms"], LEAD_MIN_MS), self._fire)

    def _fire(self):
        if self._stop or not self.light.is_open():
            self.light.focus(False)
            self._finish("stopped" if self._stop else "disconnected")
            return
        self.light.pulse(self._timing["pulse_ms"])
        self._phase = "pulse"
        self._later(self._timing["pulse_ms"] + self._timing["hold_ms"], self._release)

    def _release(self):
        self.light.focus(False)
        self._phase = "idle"
        self._next()

    def _exposure_for(self, channel):
        if not self.exposures:
            return None
        return self.exposures.get(channel if channel is not None else "w")

    def _fire_camera(self):
        if self._stop or not self._light_ok():
            self._finish("stopped" if self._stop else "disconnected")
            return
        self._phase = "exposing"
        self._shot += 1
        shot = self._shot
        exp = self._exposure_for(self._channel)
        self._later((FILE_TIMEOUT_S + _exposure_s(exp)) * 1000.0,
                    lambda: self._finish("timeout")
                    if (self._phase == "exposing" and self._shot == shot) else None)
        if exp:
            self._shutter.fire({k: exp[k] for k in ("shutter", "iso") if exp.get(k)})
        else:
            self._shutter.fire()

    def _finish(self, reason, restore=True):
        was = self.running
        self.running = False
        self._phase = "idle"
        self._stop = False
        self._queue = []
        self._token += 1
        if was and restore and self._trichrome and self.light.is_open():
            white = dict(cl.OFF)
            white["w"] = int(self._preset.get("w", 255) or 0)
            self.light.set_levels(white)
        if self.on_finished:
            self.on_finished(reason)
