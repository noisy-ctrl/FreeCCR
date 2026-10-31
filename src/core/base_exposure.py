"""Expose for the film base: a shutter speed per colour
(spec/film-base-exposure.md). Qt-free.

A negative's film base is the thinnest film on the roll, so it is the top of
the useful range. Measuring takes test shots, reads the base in the RAW and,
for each colour, picks the exposure that puts the base at or just under
TARGET of the sensor's range. The user's rules: the shutter speed does the
work at ISO 100 up to PREFERRED_S; only then does ISO rise, by at most
ISO_UP_STOPS; only then does the speed go past PREFERRED_S (to LONGEST_S). If
even the shortest allowed speed is too bright, the shutter can't help and the
light is dimmed. The results are kept as named film bases.

Everything here is pure: the camera, the light and the RAW reading are the
caller's, so the state machine is tested against a fake linear camera.
"""
import datetime
import json
import math
from dataclasses import dataclass, field

import numpy as np

TARGET = 0.85             # the base's median, share of the RAW's black-to-white range
ACCEPT_OVER = 1.03        # a check shot this far over the target still counts as on it
CLIP_TRUST = 0.95         # at or above: too near the ceiling to extrapolate from
TOO_DARK = 0.02           # under: too little to measure; go 3 stops longer
SHORT_BY = 2 ** (-1 / 3)  # more than a third of a stop under at the longest speed
BIG_STEP = 8.0            # 3 stops
MAX_SHOTS = 6             # per colour
MAX_SPREAD = 0.08         # IQR / median of the patch: more is not an even base
SPREAD_FROM = 0.10        # ... judged only from this level up (below, shot noise alone
                          # can spread a dim patch that much)
PATCH_SIDE = 0.03         # a clicked point's patch: this share of the short side
INSET = 0.20              # a rebate band is read inside this inset on each side
BASE_ISO = "100"
ISO_UP_STOPS = 2          # ISO may rise this far (100 -> 400), only past PREFERRED_S
PREFERRED_S = 1.0         # the longest speed at the base ISO before ISO rises
LONGEST_S = 30.0          # a safety ceiling: past it the light is off or blocked
SHORTEST_S = 1 / 250
DIM_MARGIN = 0.95         # a dimmed LED goes this bit further than the plan says
COLOURS = ("r", "g", "b")
CHANNEL = {"r": "R", "g": "G", "b": "B"}
NAMES = {"r": "red", "g": "green", "b": "blue", "w": "the base"}
LED_NAMES = {"r": "red", "g": "green", "b": "blue", "w": "white", "ir": "infrared"}
LIGHT_KEYS = ("r", "g", "b", "w", "ir")


# --------------------------------------------------------------------------- #
# the camera's own numbers
# --------------------------------------------------------------------------- #
def seconds(text):
    """'1/250' -> 0.004, '1.3' -> 1.3, '1s' -> 1.0, '60m' -> 3600; None if not
    a time (bulb, time, an empty value). Test 10: the camera's speeds are the
    numbers it shows, so they are used as written."""
    s = str(text if text is not None else "").strip().lower()
    mult = 1.0
    if s.endswith("m") and not s.endswith("ms"):
        mult, s = 60.0, s[:-1]
    s = s.rstrip("s").strip().strip('"')
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            v = mult * float(a) / float(b)
        else:
            v = mult * float(s)
    except (ValueError, ZeroDivisionError):
        return None
    return v if v > 0 and math.isfinite(v) else None


def iso_number(text):
    """100 from "100"; None for the camera's Auto modes (negative numbers)."""
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def exposure_of(shutter, iso):
    """Seconds x ISO / 100: the light a shot gathers, in ISO-100 seconds."""
    t, i = seconds(shutter), iso_number(iso)
    return None if t is None or i is None else t * i / 100.0


def short_text(shutter):
    """A speed as the summary shows it: '1/3', '1 s', '2.5 s'."""
    s = str(shutter).strip()
    if "/" in s:
        return s
    t = seconds(s)
    if t is None:
        return s
    if t >= 60:
        return f"{t / 60:g} min"
    return f"{t:g} s"


# --------------------------------------------------------------------------- #
# reading the base in the RAW
# --------------------------------------------------------------------------- #
@dataclass
class Reading:
    """Each colour's median and spread (IQR / median) in the patch, as a share
    of the RAW's black-to-white range."""
    levels: dict = field(default_factory=dict)     # "R" / "G" / "B" -> median
    spreads: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def channel(self, colour):
        if colour in CHANNEL:
            return CHANNEL[colour]
        return max(self.levels, key=self.levels.get) if self.levels else "G"

    def level(self, colour):
        return float(self.levels.get(self.channel(colour), 0.0))

    def spread(self, colour):
        return float(self.spreads.get(self.channel(colour), 0.0))


def _pixel_box(rect, w, h):
    x0, y0, x1, y1 = (float(v) for v in rect)
    x0, x1 = sorted((min(1.0, max(0.0, x0)), min(1.0, max(0.0, x1))))
    y0, y1 = sorted((min(1.0, max(0.0, y0)), min(1.0, max(0.0, y1))))
    c0, c1 = int(math.floor(x0 * w)), int(math.ceil(x1 * w))
    r0, r1 = int(math.floor(y0 * h)), int(math.ceil(y1 * h))
    c1, r1 = max(c1, c0 + 2), max(r1, r0 + 2)     # at least one photosite of each colour
    return max(0, r0), min(h, r1), max(0, c0), min(w, c1)


def reading_from_mosaic(mosaic, colors, desc, black, white, rect=(0.0, 0.0, 1.0, 1.0)):
    """The pure part of `read_patch`: `mosaic` / `colors` the visible area,
    `desc` the colour letters ("RGBG"), `black` per colour index, `white` the
    white level; `rect` is (x0, y0, x1, y1) as shares of the visible area."""
    m = np.asarray(mosaic)
    h, w = m.shape[:2]
    r0, r1, c0, c1 = _pixel_box(rect, w, h)
    m = m[r0:r1, c0:c1].astype(np.float32)
    cols = np.asarray(colors)[r0:r1, c0:c1]
    blk = np.asarray(black, dtype=np.float32).ravel()
    desc = str(desc).upper()
    out = Reading()
    for name in "RGB":
        idx = [i for i, ch in enumerate(desc) if ch == name]
        if not idx:
            continue
        sel = np.isin(cols, idx)
        if not sel.any():
            continue
        b = blk[cols[sel]] if blk.size > 1 else np.float32(blk[0] if blk.size else 0.0)
        f = (m[sel] - b) / np.maximum(float(white) - b, 1.0)
        med = float(np.median(f))
        p25, p75 = (float(v) for v in np.percentile(f, (25, 75)))
        out.levels[name] = med
        out.spreads[name] = (p75 - p25) / med if med > 1e-6 else float("inf")
        out.counts[name] = int(sel.sum())
    return out


def read_patch(path, rect):
    """The base reading of a RAW inside `rect` (shares of its visible area)."""
    import rawpy
    with rawpy.imread(path) as raw:
        vis = raw.raw_image_visible
        h, w = vis.shape
        r0, r1, c0, c1 = _pixel_box(rect, w, h)
        # copies: views into LibRaw's buffer are freed with the file
        m = np.array(vis[r0:r1, c0:c1], copy=True)
        cols = np.array(raw.raw_colors_visible[r0:r1, c0:c1], copy=True)
        desc = bytes(raw.color_desc).decode("ascii", "ignore")
        black = list(raw.black_level_per_channel)
        white = float(raw.white_level)
    return reading_from_mosaic(m, cols, desc, black, white)


def patch_around(fx, fy, frame_w, frame_h, side=PATCH_SIDE):
    """A square of `side` x the short side around a point (frame fractions)."""
    s = side * min(frame_w, frame_h)
    hx, hy = s / 2.0 / max(1, frame_w), s / 2.0 / max(1, frame_h)
    fx, fy = min(1.0 - hx, max(hx, fx)), min(1.0 - hy, max(hy, fy))
    return (fx - hx, fy - hy, fx + hx, fy + hy)


def patch_inset(rect, inset=INSET):
    """A rebate band, `inset` of its size in from each side."""
    x0, y0, x1, y1 = rect
    dx, dy = (x1 - x0) * inset, (y1 - y0) * inset
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    shutter: str
    iso: str
    predicted: float
    dim: float = 1.0          # < 1: the LED must come down by this factor
    short: bool = False       # more than a third of a stop under at the longest
    need_s: float = 0.0       # seconds at this ISO that would reach the target


def default_rules(isos):
    numeric = sorted((v for v in isos or [] if iso_number(v)), key=iso_number)
    base = BASE_ISO if BASE_ISO in (isos or []) else (numeric[0] if numeric else BASE_ISO)
    cap = (iso_number(base) or 100.0) * 2 ** ISO_UP_STOPS * 1.01
    top = [v for v in numeric if iso_number(v) <= cap]
    return {"base_iso": base, "max_iso": top[-1] if top else base,
            "preferred": PREFERRED_S, "longest": LONGEST_S,
            "shortest": SHORTEST_S, "target": TARGET}


def allowed_shutters(shutters, rules):
    """[(seconds, text)] of the camera's speeds within the rules, shortest
    first."""
    out = []
    for s in shutters or []:
        t = seconds(s)
        if t is not None and rules["shortest"] * 0.999 <= t <= rules["longest"] * 1.001:
            out.append((t, str(s)))
    out.sort(key=lambda x: x[0])
    return out


def iso_steps(isos, rules):
    """The camera's ISO values from the base to the highest allowed."""
    lo, hi = iso_number(rules["base_iso"]), iso_number(rules["max_iso"])
    steps = sorted((v for v in isos or [] if iso_number(v) and lo <= iso_number(v) <= hi),
                   key=iso_number)
    return steps or [rules["base_iso"]]


def plan_exposure(k, shutters, isos, rules):
    """Where the predicted base level `k x t x ISO/100` lands at or just under
    the target, in the user's order:
    1. the base ISO, the longest speed up to `preferred` that fits;
    2. still more than 1/3 stop short at `preferred`: ISO up (at most to
       `max_iso`) at that speed;
    3. still short at `max_iso`: the speed goes past `preferred`, up to
       `longest` (`short` if even that leaves it more than 1/3 stop under).
    Too bright even at the shortest speed at the base ISO: `dim`."""
    sh = allowed_shutters(shutters, rules)
    if not sh:
        raise ValueError("no shutter speed within the allowed range")
    steps = iso_steps(isos, rules)
    target = rules["target"]
    pref = min(float(rules.get("preferred", rules["longest"])), rules["longest"])

    def pred(t, iso):
        return k * t * iso_number(iso) / 100.0

    def need(iso):
        gain = k * iso_number(iso) / 100.0
        return target / gain if gain > 0 else float("inf")

    base = steps[0]
    fits = [(t, s) for t, s in sh if pred(t, base) <= target]
    if not fits:
        t, s = sh[0]
        p = pred(t, base)
        return Plan(s, base, p, dim=target / p if p > 0 else 1.0, need_s=need(base))
    tp, sp = ([x for x in sh if x[0] <= pref * 1.001] or sh[:1])[-1]
    t, s = [x for x in fits if x[0] <= tp * 1.001][-1]
    if (t, s) != (tp, sp) or pred(tp, base) >= target * SHORT_BY:
        return Plan(s, base, pred(t, base), need_s=need(base))       # 1.
    iso = base
    for cand in steps[1:]:
        if pred(tp, cand) <= target:
            iso = cand
    if iso != steps[-1] or pred(tp, iso) >= target * SHORT_BY:
        return Plan(sp, iso, pred(tp, iso), need_s=need(iso))        # 2.
    t, s = [x for x in sh if pred(x[0], iso) <= target][-1]          # 3.
    p = pred(t, iso)
    return Plan(s, iso, p, short=(t == sh[-1][0] and p < target * SHORT_BY),
                need_s=need(iso))


# --------------------------------------------------------------------------- #
# measuring
# --------------------------------------------------------------------------- #
class Calibration:
    """Measure the exposure for each colour in turn.

    The caller is the camera and the light: `next_shot()` gives
    `(colour, levels, {"shutter", "iso"})` to shoot (None once finished),
    `landed(reading)` takes that shot's patch reading. `failed(text)` and
    `cancel()` end it. When `done`: `result` {colour: {"shutter", "iso",
    "base", "levels", "dimmed"}} and `notes`, or `error`."""

    def __init__(self, colours, start_levels, shutters, isos, first_shutter,
                 rules=None, can_dim=True):
        self.colours = list(colours)
        self.rules = dict(rules or default_rules(isos))
        self.isos = list(isos or [])
        self.can_dim = bool(can_dim)
        self.levels = {}
        for c in self.colours:
            lv = (start_levels or {}).get(c)
            self.levels[c] = ({k: int(lv.get(k, 0)) for k in LIGHT_KEYS}
                              if isinstance(lv, dict) else None)
        self._start_levels = {c: (dict(v) if v else None) for c, v in self.levels.items()}
        self.shutters = allowed_shutters(shutters, self.rules)
        self.result, self.notes = {}, []
        self.error = None
        self.done = False
        self.shots = 0                       # all colours
        self._i = 0
        self._pending = False
        if not self.colours:
            self._end(error="Nothing to measure.")
            return
        if not self.shutters:
            self._end(error="The camera offers no shutter speed between "
                            f"{short_text('1/' + str(round(1 / self.rules['shortest'])))} "
                            f"and {short_text(self.rules['longest'])}.")
            return
        self._first = self._nearest(seconds(first_shutter) or 0.25)
        self._start_colour()

    # -- what the caller sees ---------------------------------------------
    @property
    def colour(self):
        return None if self.done else self.colours[self._i]

    def next_shot(self):
        if self.done:
            return None
        c = self.colours[self._i]
        self._pending = True
        lv = self.levels[c]
        return c, (dict(lv) if lv is not None else None), {"shutter": self._shutter,
                                                            "iso": self._iso}

    def landed(self, reading):
        if self.done or not self._pending:
            return
        self._pending = False
        self.shots += 1
        self._shots += 1
        c = self.colours[self._i]
        level = reading.level(c)
        t = seconds(self._shutter)
        if level >= SPREAD_FROM and reading.spread(c) > MAX_SPREAD:
            return self._end(error="The film base patch isn't even (dust, edge print or "
                                   "the picture's edge?): click the film base in Negative "
                                   "Preview, then Measure again.")
        self._tried[self._key()] = level
        if level >= CLIP_TRUST:
            shorter = self._at_or_below(t / BIG_STEP)
            if seconds(shorter) < t:
                self._shutter = shorter
                return self._limit()
            return self._dim(0.5)
        if level < TOO_DARK:
            longer = self._at_or_above(t * BIG_STEP)
            if seconds(longer) > t:
                self._shutter = longer
                return self._limit()
            return self._end(error=f"{self._name(c).capitalize()} is still dark at "
                                   f"{short_text(self._shutter)}: is {self._led(c)} on, "
                                   "and the film base under the lens?")
        k = level / (t * iso_number(self._iso) / 100.0)
        plan = plan_exposure(k, [s for _, s in self.shutters], self.isos, self.rules)
        if plan.dim < 1.0:
            return self._dim(plan.dim)
        if plan.short:
            return self._end(error=f"{self._name(c).capitalize()} would need about "
                                   f"{_about(plan.need_s)} at ISO {plan.iso}: is "
                                   f"{self._led(c)} on, and the film base under the lens?")
        if (plan.shutter, plan.iso) == (self._shutter, self._iso):
            return self._accept(level)
        if (plan.shutter, plan.iso, self._led_key()) in self._tried:
            return self._accept_best()
        self._shutter, self._iso = plan.shutter, plan.iso
        return self._limit()

    def failed(self, text):
        if not self.done:
            self._end(error=str(text) or "The camera didn't take the shot.")

    def cancel(self):
        if not self.done:
            self._end(error="cancelled")

    # -- steps --------------------------------------------------------------
    def _start_colour(self):
        self._tried = {}
        self._shots = 0
        self._shutter = self._first
        self._iso = self.rules["base_iso"]

    def _limit(self):
        if self._shots >= MAX_SHOTS:
            return self._accept_best()
        return None

    def _key(self):
        return (self._shutter, self._iso, self._led_key())

    def _led_key(self):
        lv = self.levels[self.colours[self._i]]
        return None if lv is None else tuple(lv.get(k, 0) for k in LIGHT_KEYS)

    def _dim(self, factor):
        c = self.colours[self._i]
        lv = self.levels[c]
        lit = [k for k in LIGHT_KEYS if lv and lv.get(k, 0) > 0]
        if not self.can_dim or not lit:
            return self._end(error=f"Even {short_text(self.shutters[0][1])} is too bright for "
                                   f"{self._name(c)}: dim the light, then Measure again.")
        new = {k: (max(1, int(lv[k] * factor * DIM_MARGIN)) if k in lit else lv.get(k, 0))
               for k in LIGHT_KEYS}
        if all(new[k] >= lv[k] for k in lit):
            return self._end(error=f"Even {short_text(self.shutters[0][1])} with the LED at "
                                   f"its lowest is too bright for {self._name(c)}.")
        self.levels[c] = new
        self._shutter = self.shutters[0][1]
        return self._limit()

    def _accept(self, level):
        c = self.colours[self._i]
        lv = self.levels[c]
        dimmed = lv is not None and lv != self._start_levels.get(c)
        self.result[c] = {"shutter": self._shutter, "iso": self._iso,
                          "base": round(float(level), 4),
                          "levels": dict(lv) if lv is not None else None,
                          "dimmed": bool(dimmed)}
        if dimmed:
            lit = [k for k in LIGHT_KEYS if lv.get(k, 0) > 0]
            self.notes.append(", ".join(f"{LED_NAMES[k]} LED dimmed to {lv[k]}" for k in lit)
                              + f" (even {short_text(self.shutters[0][1])} was too bright)")
        self._i += 1
        if self._i >= len(self.colours):
            self._end()
        else:
            self._start_colour()

    def _accept_best(self):
        led = self._led_key()
        ok = [(exposure_of(s, i) or 0.0, s, i, lv) for (s, i, l), lv in self._tried.items()
              if l == led and lv <= self.rules["target"] * ACCEPT_OVER]
        if not ok:
            return self._end(error=f"Couldn't settle {self._name(self.colours[self._i])}'s "
                                   "exposure: is the light steady?")
        _e, s, i, lv = max(ok)
        self._shutter, self._iso = s, i
        return self._accept(lv)

    def _end(self, error=None):
        self.done = True
        self._pending = False
        self.error = error

    # -- helpers ------------------------------------------------------------
    def _nearest(self, t):
        return min(self.shutters, key=lambda x: abs(math.log(x[0] / t)))[1]

    def _at_or_below(self, t):
        below = [s for tt, s in self.shutters if tt <= t * 1.001]
        return below[-1] if below else self.shutters[0][1]

    def _at_or_above(self, t):
        above = [s for tt, s in self.shutters if tt >= t * 0.999]
        return above[0] if above else self.shutters[-1][1]

    def _name(self, c):
        return NAMES.get(c, c)

    def _led(self, c):
        return f"the {LED_NAMES[c]} LED" if c in COLOURS else "the light"


def _about(sec):
    if not math.isfinite(sec):
        return "forever"
    if sec >= 120:
        return f"{sec / 60:.0f} min"
    return f"{sec:.0f} s" if sec >= 10 else f"{sec:.1f} s"


# --------------------------------------------------------------------------- #
# the film bases (QSettings `live/film_bases`)
# --------------------------------------------------------------------------- #
def _clean_colour(e):
    if not isinstance(e, dict):
        return None
    shutter, iso = str(e.get("shutter", "")).strip(), str(e.get("iso", "")).strip()
    if seconds(shutter) is None or iso_number(iso) is None:
        return None
    try:
        base = float(e.get("base", 0.0))
    except (TypeError, ValueError):
        base = 0.0
    lv = e.get("levels")
    levels = None
    if isinstance(lv, dict):
        levels = {}
        for k in LIGHT_KEYS:
            try:
                levels[k] = max(0, min(255, int(round(float(lv.get(k, 0))))))
            except (TypeError, ValueError):
                levels[k] = 0
    out = {"shutter": shutter, "iso": iso, "base": max(0.0, min(2.0, base)),
           "levels": levels, "dimmed": bool(e.get("dimmed", False))}
    if e.get("edited"):
        out["edited"] = True     # set by hand: `base` is an estimate (revision 4)
    return out


def _clean_base(b):
    if not isinstance(b, dict):
        return None
    name = str(b.get("name", "")).strip()
    if not name:
        return None
    colours = {}
    for k in COLOURS + ("w",):
        c = _clean_colour((b.get("colours") or {}).get(k))
        if c:
            colours[k] = c
    if not colours:
        return None
    return {"name": name, "when": str(b.get("when", "") or ""),
            "camera": str(b.get("camera", "") or ""), "colours": colours,
            "notes": [str(n) for n in (b.get("notes") or []) if str(n).strip()][:6]}


def normalise(data):
    """{"bases": [clean entries, unique names], "selected": index or -1}."""
    if isinstance(data, str):
        try:
            data = json.loads(data) if data.strip() else None
        except (TypeError, ValueError):
            data = None
    bases, seen = [], set()
    if isinstance(data, dict):
        for b in data.get("bases", []) or []:
            c = _clean_base(b)
            if c and c["name"].lower() not in seen:
                seen.add(c["name"].lower())
                bases.append(c)
    sel = -1
    if isinstance(data, dict):
        try:
            sel = int(data.get("selected", -1))
        except (TypeError, ValueError):
            sel = -1
    if bases:
        sel = max(0, min(len(bases) - 1, sel)) if sel >= 0 else 0
    else:
        sel = -1
    return {"bases": bases, "selected": sel}


def dumps(data):
    return json.dumps(normalise(data))


def selected(data):
    d = normalise(data)
    return d["bases"][d["selected"]] if d["selected"] >= 0 else None


def unique_name(data, name):
    taken = {b["name"].lower() for b in normalise(data)["bases"]}
    base = (name or "Film base").strip() or "Film base"
    if base.lower() not in taken:
        return base
    i = 2
    while f"{base} {i}".lower() in taken:
        i += 1
    return f"{base} {i}"


def default_name(today=None):
    d = today or datetime.date.today()
    return f"Film base · {d.day} {d.strftime('%b')}"


def entry(name, colours, camera="", notes=(), when=None):
    return {"name": name, "when": when or datetime.date.today().isoformat(),
            "camera": camera or "", "colours": dict(colours), "notes": list(notes)}


def add(data, new):
    d = normalise(data)
    c = _clean_base(dict(new, name=unique_name(d, new.get("name"))))
    if c:
        d["bases"].append(c)
        d["selected"] = len(d["bases"]) - 1
    return d


def replace(data, index, colours, camera="", notes=(), when=None):
    """Measure Again: new exposures under the same name."""
    d = normalise(data)
    if 0 <= index < len(d["bases"]):
        old = d["bases"][index]
        c = _clean_base(entry(old["name"], colours, camera or old["camera"], notes, when))
        if c:
            d["bases"][index] = c
            d["selected"] = index
    return d


def rename(data, index, name):
    d = normalise(data)
    name = (name or "").strip()
    if not name or not (0 <= index < len(d["bases"])):
        return d
    if name.lower() in {b["name"].lower() for i, b in enumerate(d["bases"]) if i != index}:
        return d
    d["bases"][index]["name"] = name
    return d


def delete(data, index):
    d = normalise(data)
    if 0 <= index < len(d["bases"]):
        del d["bases"][index]
        if not d["bases"]:
            d["selected"] = -1
        elif d["selected"] >= index:
            d["selected"] = max(0, d["selected"] - 1)
    return d


# --------------------------------------------------------------------------- #
# editing by hand (spec revision 4)
# --------------------------------------------------------------------------- #
def led_level(colour):
    """A colour's LED level, 1-255: the brightest lit channel of its levels;
    None when it has none (one exposure with the light as it was)."""
    lv = (colour or {}).get("levels")
    if not lv:
        return None
    top = max(lv.values())
    return int(top) if top > 0 else None


def scale_levels(levels, level):
    """`levels` with every lit channel scaled so the brightest is `level`;
    a preset's mix keeps its balance."""
    top = max(levels.values()) if levels else 0
    if top <= 0:
        return dict(levels or {})
    level = max(1, min(255, int(level)))
    if level == top:
        return dict(levels)
    f = level / float(top)
    return {k: (max(1, min(255, int(round(v * f)))) if v > 0 else 0)
            for k, v in levels.items()}


def estimate_base(colour, shutter, iso, level=None):
    """The base reading at a hand-set exposure, from the measured one: twice
    the time, ISO or LED level, twice the level (test 10: linear). None when
    a value has no number."""
    e_old = exposure_of(colour["shutter"], colour["iso"])
    e_new = exposure_of(shutter, iso)
    if not e_old or not e_new:
        return None
    est = float(colour["base"]) * e_new / e_old
    old = led_level(colour)
    if level is not None and old:
        est *= max(1, min(255, int(level))) / float(old)
    return est


def edited_colours(old, values):
    """The colours after an edit. `values` = {k: {"shutter", "iso", "level"}}
    for the colours shown; a colour whose values didn't change is copied as
    it was, a changed one is marked `edited` with an estimated base."""
    out = {}
    for k, col in old.items():
        v = values.get(k)
        if not v:
            out[k] = dict(col)
            continue
        shutter = str(v.get("shutter") or col["shutter"])
        iso = str(v.get("iso") or col["iso"])
        lvl = v.get("level")
        old_lvl = led_level(col)
        levels = col.get("levels")
        led_changed = lvl is not None and old_lvl is not None and int(lvl) != old_lvl
        if shutter == col["shutter"] and iso == col["iso"] and not led_changed:
            out[k] = dict(col)
            continue
        if led_changed:
            levels = scale_levels(levels, lvl)
        est = estimate_base(col, shutter, iso, lvl if led_changed else None)
        new = dict(col, shutter=shutter, iso=iso, levels=levels, edited=True,
                   base=est if est is not None else col["base"])
        if led_changed:
            new["dimmed"] = True
        out[k] = new
    return out


def edit(data, index, name, colours):
    """Save: the film base at `index` takes `colours` (from `edited_colours`)
    and `name` (made unique against the others); when/camera/notes stay."""
    d = normalise(data)
    if not 0 <= index < len(d["bases"]):
        return d
    old = d["bases"][index]
    others = dict(d, bases=[b for i, b in enumerate(d["bases"]) if i != index])
    name = (name or "").strip() or old["name"]
    if name.lower() != old["name"].lower():
        name = unique_name(others, name)
    c = _clean_base(dict(old, name=name, colours=colours))
    if c:
        d["bases"][index] = c
        d["selected"] = index
    return d


def exposures_for(base, trichrome):
    """The sequence's exposures for this scan type, or None if the film base
    wasn't measured for it."""
    if not base:
        return None
    cols = base.get("colours") or {}
    keys = COLOURS if trichrome else ("w",)
    if not all(k in cols for k in keys):
        return None
    return {k: {"shutter": cols[k]["shutter"], "iso": cols[k]["iso"],
                "levels": dict(cols[k]["levels"]) if cols[k].get("levels") else None}
            for k in keys}


def summary(base, trichrome):
    """One line: each colour's speed, the ISO, the base reading."""
    if not base:
        return "No film base yet: Measure… reads the base and picks each colour's exposure."
    cols = base.get("colours") or {}
    keys = COLOURS if trichrome else ("w",)
    if not all(k in cols for k in keys):
        other = "one exposure" if trichrome else "trichrome"
        return f"Measured for {other}: Measure Again for this scan type."
    isos = {cols[k]["iso"] for k in keys}
    iso = f", ISO {isos.pop()}" if len(isos) == 1 else ""
    if trichrome:
        speeds = " · ".join(f"{k.upper()} {short_text(cols[k]['shutter'])}"
                            + ("" if iso else f" ISO {cols[k]['iso']}") for k in keys)
    else:
        speeds = short_text(cols["w"]["shutter"]) + ("" if iso else f" ISO {cols['w']['iso']}")
    bases = " / ".join(("≈" if cols[k].get("edited") else "") + f"{100 * cols[k]['base']:.0f}"
                       for k in keys)
    line = f"{speeds}{iso} (base {bases} %)"
    dimmed = [(f"{NAMES[k]} LED" if k in COLOURS else "light") + f" {led_level(cols[k])}"
              for k in keys if cols[k].get("dimmed") and led_level(cols[k])]
    if dimmed:
        line += "; " + ", ".join(dimmed)
    return line
