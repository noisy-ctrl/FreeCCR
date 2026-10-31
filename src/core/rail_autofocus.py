"""Autofocus on the focus rail (spec/focus-rail.md §6). Qt-free.

A coarse sweep, then a fine one around the coarse best, reading the
sharpness meter's fresh readings (`FrameResult.reading`) at each position,
then a fitted peak. Every measured position and the final one is reached
moving UP (increasing steps): going down to a target means going below it by
the backlash first, so the lead screw's play is always taken up the same way.

The sequence is a generator (`_plan`) that yields what it waits for:
`("goto", pos)` -> the rail's DONE for that move; `("measure",)` -> the
median of the next `readings` readings after `settle_ms`. The monitor feeds
`moved()`, `reading()` and `tick()`; `goto` is a callable that sends the move
and returns its id.
"""
import math
import time

import numpy as np

from core import orp

DEFAULTS = {"range_um": 1000, "coarse_um": 100, "fine_um": 20,
            "backlash_um": 100, "settle_ms": 300, "readings": 3}
LIMITS = {"range_um": (200, 5000), "coarse_um": (20, 500), "fine_um": (5, 100),
          "backlash_um": (0, 500), "settle_ms": (50, 2000), "readings": (1, 9)}
ASSUMED_STEPS_PER_MM = 400.0
PAST_PEAK = 0.8          # both of the last two readings under this x the best
FLAT_RATIO = 1.15        # best / worst under this over the coarse sweep: nothing to focus on
VERIFY_RATIO = 0.9       # the final reading under this x the fine best: warn
MOVE_TIMEOUT_S = 20.0
READ_TIMEOUT_S = 3.0


def clean_options(opts):
    out = {}
    for k, d in DEFAULTS.items():
        lo, hi = LIMITS[k]
        try:
            v = int(round(float((opts or {}).get(k, d))))
        except (TypeError, ValueError):
            v = d
        out[k] = max(lo, min(hi, v))
    return out


def fit_peak(points):
    """The focus peak's position from (pos, score) readings: a least-squares
    parabola on log(score) through the best reading and up to two either
    side (a Gaussian peak). Its vertex, kept between the best reading's
    neighbours; the best reading itself when the fit doesn't open down."""
    pts = sorted((int(p), float(s)) for p, s in points)
    if not pts:
        return None
    pos = np.array([p for p, _ in pts], dtype=np.float64)
    sc = np.array([max(s, 1e-12) for _, s in pts], dtype=np.float64)
    m = int(np.argmax(sc))
    lo, hi = max(0, m - 2), min(len(pts) - 1, m + 2)
    if hi - lo + 1 < 3:
        return int(pos[m])
    x = pos[lo:hi + 1] - pos[m]
    y = np.log(sc[lo:hi + 1])
    try:
        a, b, _c = np.polyfit(x, y, 2)
    except Exception:
        return int(pos[m])
    if not (a < 0) or not np.isfinite(a) or not np.isfinite(b):
        return int(pos[m])
    v = pos[m] - b / (2.0 * a)
    left = pos[m - 1] if m > 0 else pos[m]
    right = pos[m + 1] if m < len(pts) - 1 else pos[m]
    return int(round(min(max(v, left), right)))


def past_peak(points, ratio=PAST_PEAK):
    """At least 3 readings, the best not among the last two, and both of the
    last two under `ratio` x the best."""
    if len(points) < 3:
        return False
    scores = [s for _p, s in points]
    best = max(scores)
    m = scores.index(best)
    return (m <= len(scores) - 3 and scores[-1] < ratio * best
            and scores[-2] < ratio * best)


class Result:
    def __init__(self, ok, message, peak=None, score=None, points=None, warning=""):
        self.ok = bool(ok)
        self.message = message
        self.peak = peak
        self.score = score
        self.points = list(points or [])
        self.warning = warning

    def __repr__(self):
        return f"Result(ok={self.ok}, peak={self.peak}, message={self.message!r})"


class _Fail(Exception):
    def __init__(self, message, go_back=True):
        super().__init__(message)
        self.message = message
        self.go_back = go_back


class Autofocus:
    def __init__(self, goto, options=None, steps_per_mm=0.0, limits=None,
                 clock=time.monotonic, on_progress=None, on_finished=None):
        self._goto = goto
        self.opts = clean_options(options)
        self.spm_known = bool(steps_per_mm and steps_per_mm > 0)
        self.spm = float(steps_per_mm) if self.spm_known else ASSUMED_STEPS_PER_MM
        self.limits = limits                 # (min, max) when the rail is homed
        self._clock = clock
        self.on_progress = on_progress
        self.on_finished = on_finished
        self.running = False
        self.result = None
        self.pos = None
        self.start_pos = None
        self.points = []                     # every (pos, score, phase) read
        self.moves = []                      # every (from, to) sent
        self._dir = 0                        # +1 / -1: the last move's direction
        self._gen = None
        self._wait = None

    # ------------------------------------------------------------------ #
    # units
    # ------------------------------------------------------------------ #
    def steps(self, um):
        return float(um) * self.spm / 1000.0

    def text(self, pos):
        return orp.mm_text(pos, self.spm if self.spm_known else 0)

    def _clamp(self, pos):
        pos = int(round(pos))
        if self.limits:
            lo, hi = self.limits
            if lo is not None:
                pos = max(int(lo), pos)
            if hi is not None:
                pos = min(int(hi), pos)
        return pos

    # ------------------------------------------------------------------ #
    # driving
    # ------------------------------------------------------------------ #
    def start(self, pos):
        self.running = True
        self.result = None
        self.pos = int(pos)
        self.start_pos = int(pos)
        self.points = []
        self.moves = []
        self._dir = 0
        self._gen = self._run(self.pos)
        self._advance(None)

    def moved(self, move_id, pos, flags=None):
        w = self._wait
        if not self.running or w is None or w[0] != "move" or int(move_id) != w[1]:
            return
        flags = flags or {}
        self.pos = int(pos)
        if flags.get("stopped") or flags.get("fail"):
            why = flags.get("fail") or "it was stopped"
            self._end(Result(False, f"The rail didn't finish its move ({why})."))
            return
        self._wait = None
        self._advance(None)

    def reading(self, score):
        w = self._wait
        if not self.running or w is None or w[0] != "read" or score is None:
            return
        if self._clock() < w[1]:
            return                                  # still settling
        w[3].append(float(score))
        if len(w[3]) >= self.opts["readings"]:
            self._wait = None
            self._advance(float(np.median(w[3])))

    def tick(self):
        w = self._wait
        if not self.running or w is None:
            return
        now = self._clock()
        if w[0] == "move" and now > w[2]:
            self._end(Result(False, "The rail didn't finish its move in time."))
        elif w[0] == "read" and now > w[2]:
            self._wait = None
            self._throw(_Fail("No sharpness readings: is the live view running?"))

    def stop(self, message="Autofocus stopped."):
        if self.running:
            self._end(Result(False, message, points=self.points))

    def _advance(self, value):
        try:
            action = self._gen.send(value)
        except StopIteration:
            return
        self._do(action)

    def _throw(self, exc):
        try:
            action = self._gen.throw(exc)
        except StopIteration:
            return
        self._do(action)

    def _do(self, action):
        if not self.running:
            return
        if action[0] == "goto":
            target = int(action[1])
            self.moves.append((self.pos, target))
            if target != self.pos:
                self._dir = 1 if target > self.pos else -1
            mid = self._goto(target)
            self._wait = ("move", int(mid), self._clock() + MOVE_TIMEOUT_S)
        elif action[0] == "measure":
            now = self._clock()
            settle = now + self.opts["settle_ms"] / 1000.0
            self._wait = ("read", settle, settle + READ_TIMEOUT_S, [])

    def _end(self, result):
        self.running = False
        self._wait = None
        self.result = result
        gen, self._gen = self._gen, None
        if gen is not None:
            try:
                gen.close()
            except Exception:
                pass
        if self.on_finished:
            self.on_finished(result)

    def _progress(self, text):
        if self.on_progress:
            self.on_progress(text)

    # ------------------------------------------------------------------ #
    # the sequence
    # ------------------------------------------------------------------ #
    def _run(self, p0):
        try:
            result = yield from self._plan(p0)
        except _Fail as f:
            if f.go_back and self.pos != p0:
                self._progress("Going back to where it started…")
                yield from self._approach(p0)
            self._end(Result(False, f.message, points=self.points))
            return
        self._end(result)

    def _approach(self, target):
        """Arrive at `target` moving up."""
        target = self._clamp(target)
        b = int(round(self.steps(self.opts["backlash_um"])))
        if target > self.pos:
            yield ("goto", target)
            return
        if target == self.pos and (self._dir > 0 or b == 0):
            return
        if b > 0:
            below = self._clamp(target - b)
            if below != self.pos:
                yield ("goto", below)
        if target != self.pos:
            yield ("goto", target)

    def _scan(self, a, b, step, phase):
        """Readings from `a` up to `b` (we're at `a`, arrived moving up);
        stops once past the peak. Returns (points, stopped_early)."""
        pts = []
        n = max(1, int(math.floor((b - a) / step + 1e-6)) + 1)
        for i in range(n):
            pos = self._clamp(a + i * step)
            if pos != self.pos:
                yield ("goto", pos)
            s = yield ("measure",)
            pts.append((self.pos, s))
            self.points.append((self.pos, s, phase))
            self._progress(f"Autofocus: {phase} {i + 1}/{n} at {self.text(self.pos)}")
            if past_peak(pts):
                return pts, True
        return pts, False

    def _plan(self, p0):
        R = self.steps(self.opts["range_um"])
        C = max(1.0, self.steps(self.opts["coarse_um"]))
        F = max(1.0, self.steps(self.opts["fine_um"]))
        lo, hi = self._clamp(p0 - R), self._clamp(p0 + R)
        yield from self._approach(lo)
        pts, early = yield from self._scan(lo, hi, C, "coarse")
        scores = [s for _p, s in pts]
        if max(scores) <= 0 or max(scores) < FLAT_RATIO * max(min(scores), 1e-12):
            raise _Fail("Focus barely changed over the sweep: nothing sharp in the "
                        "meter's area, or focus is further away (widen the Range).")
        m = scores.index(max(scores))
        # the peak at an end: look once more that way
        if m == 0:
            new_lo = self._clamp(lo - R)
            if new_lo < lo:
                yield from self._approach(new_lo)
                more, _e = yield from self._scan(new_lo, lo - C, C, "coarse")
                pts = sorted(more + pts)
        elif m == len(pts) - 1 and not early:
            new_hi = self._clamp(hi + R)
            if new_hi > pts[-1][0]:
                more, early = yield from self._scan(pts[-1][0] + C, new_hi, C, "coarse")
                pts = pts + more
        scores = [s for _p, s in pts]
        m = scores.index(max(scores))
        if m == 0 or (m == len(pts) - 1 and not early):
            span = self.opts["range_um"] * 2 / 1000.0
            raise _Fail(f"No focus peak within ±{span:g} mm of where it started: "
                        "move the rail nearer focus by hand (or widen the Range).")
        pc = pts[m][0]
        flo, fhi = self._clamp(pc - C), self._clamp(pc + C)
        yield from self._approach(flo)
        fpts, _e = yield from self._scan(flo, fhi, F, "fine")
        peak = self._clamp(fit_peak(fpts))
        self._progress(f"Autofocus: to the peak at {self.text(peak)}")
        yield from self._approach(peak)
        final = yield ("measure",)
        self.points.append((self.pos, final, "final"))
        best = max(s for _p, s in fpts)
        if final < VERIFY_RATIO * best:
            return Result(True, f"Focused at {self.text(self.pos)}, but the last reading "
                          f"was {100 * final / best:.0f} % of the sweep's best: the rail "
                          "may have more play than the Backlash setting.",
                          peak=self.pos, score=final, points=self.points,
                          warning="play")
        return Result(True, f"Focused at {self.text(self.pos)}.", peak=self.pos,
                      score=final, points=self.points)
