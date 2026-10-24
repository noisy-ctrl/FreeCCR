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
"""
import time

from core import capture_light as cl

TIMING_DEFAULT = {"focus_ms": 200, "pulse_ms": 300, "hold_ms": 200, "period_ms": 1000}
TIMING_LIMITS = {"focus_ms": (0, 2000), "pulse_ms": (10, cl.PULSE_MAX_MS),
                 "hold_ms": (0, 5000), "period_ms": (0, 5000)}
LEAD_MIN_MS = 50            # the colour is set at least this long before a pulse
TRICHROME = ("r", "g", "b")


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


def cycle_ms(timing, trichrome=True):
    """How long a Shoot takes, start to finish (the gaps Period enforces
    included)."""
    t = clean_timing(timing)
    one = shot_ms(t)
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


def _qt_schedule(ms, fn):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(max(0, int(round(ms))), fn)


class CaptureSequence:
    """`on_step(channel, duration_ms)` when a shot starts (channel is r/g/b
    in trichrome, None for a single shot); `on_finished(reason)` with
    "done", "stopped" or "disconnected"."""

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
        self._phase = "idle"         # idle | waiting | focus | pulse

    def start(self, timing, preset, trichrome=True):
        if self.running:
            return False
        if not self.light.is_open():
            self._finish("disconnected", restore=False)
            return False
        self.running = True
        self._stop = False
        self._token += 1
        self._timing = clean_timing(timing)
        self._preset = {k: int((preset or {}).get(k, 0)) for k in cl.CHANNELS}
        self._trichrome = bool(trichrome)
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
        elif self._phase == "waiting":
            self._finish("stopped")

    # -- steps ------------------------------------------------------------
    def _later(self, ms, fn):
        token = self._token
        self._schedule(ms, lambda: (fn() if token == self._token else None))

    def _next(self):
        if self._stop:
            self._finish("stopped")
            return
        if not self.light.is_open():
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
        if channel is not None:
            self.light.set_levels(cl.solo(self._preset, channel))
        self._last_shot = self._clock()
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
