"""Shoot Flat Fields: Capture shoots the flats of a set by itself
(spec/ir-dust-and-flat-sets.md §5, "Capture → Shoot Flat Fields…").

With the film out and the empty carrier in, each chosen light is lit alone
at full and its exposure found from the RAW: the brightest part of the
picture (the carrier opening's hotspot) is put at about TARGET of the RAW's
range by shutter and ISO, never clipped. White and Infrared are one shot
each; RGB is three (red, green, blue), merged like a trichrome scan. The
accepted shots are then built into tagged flats (`flat_field.
build_tagged_profile`) and saved in the field-correction library, one file
per light and format, so shooting them again replaces them.

Qt-free: the monitor is the camera and the light. `FlatShooting` follows
`base_exposure.Calibration`'s interface (`next_shot`, `landed`, `failed`,
`cancel`, `done`, `error`, `result`, `notes`, `shots`), so Capture's Measure
plumbing drives it unchanged."""
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import cv2
import numpy as np

from core import base_exposure as be

TARGET = 0.6               # the brightest part, share of the RAW's black-to-white range
ACCEPT = (0.35, 0.90)      # a shot this bright is kept as it is
ACCEPT_IR = (0.10, 0.90)   # IR is ~15 stops down; the flat is a smoothed grid
FLOOR = 0.05               # at the camera's limit, this bright is still a usable flat
CLIP = 0.95                # the 99.9th percentile of the block means at or above: clipped
PILE_SHARE = 0.0005        # this share of a colour's photosites on one top value: clipped
PILE_FROM = 0.5            # ... when that value is at least this of the range
TOO_DARK = 0.02            # under: too little to scale from; go 3 stops longer
BIG_STEP = 8.0             # 3 stops
MAX_SHOTS = 5              # per shot of a light
BLOCK = 24                 # photosites: a multiple of the 2x2 and 6x6 patterns
LEVEL_PCT = 99.5           # the brightest part: this percentile of the block means
CLIP_PCT = 99.9
IR_ISO_UP_STOPS = 2
IR_LONGEST_S = 30.0

LIGHT_ORDER = ("white", "rgb", "ir")
SHOTS = {"white": ("w",), "rgb": ("r", "g", "b"), "ir": ("ir",)}
SHOT_NAMES = {"w": "white", "r": "red", "g": "green", "b": "blue", "ir": "infrared"}
CHANNEL = {"r": "R", "g": "G", "b": "B"}


def solo_levels(key):
    """The LED levels for a shot: its own LED alone, at full."""
    return {k: (255 if k == key else 0) for k in be.LIGHT_KEYS}


# --------------------------------------------------------------------------- #
# reading a flat shot
# --------------------------------------------------------------------------- #
@dataclass
class FlatReading:
    """Each colour's brightest-part level (share of the RAW's range) and
    whether it clipped. `path` is the shot it was read from."""
    levels: Dict[str, float] = field(default_factory=dict)    # "R" / "G" / "B"
    clipped: Dict[str, bool] = field(default_factory=dict)
    path: str = ""

    def _channels(self, key):
        # a colour's own photosites; white and IR: all of them (every
        # channel of their flat is used)
        if key in CHANNEL:
            return [CHANNEL[key]] if CHANNEL[key] in self.levels else []
        return list(self.levels)

    def level(self, key):
        chs = self._channels(key)
        return max((float(self.levels[c]) for c in chs), default=0.0)

    def is_clipped(self, key):
        return any(self.clipped.get(c, False) for c in self._channels(key))


def reading_from_mosaic(mosaic, colors, desc, black, white, block=BLOCK):
    """The pure part of `read_flat`: the whole visible area, each colour's
    photosites averaged in `block`-square blocks; its level is the 99.5th
    percentile of the block means (the hotspot, not one hot photosite), and
    it is clipped when the 99.9th reaches CLIP or a pile sits on its top
    value."""
    m = np.asarray(mosaic)
    cols = np.asarray(colors)
    h, w = m.shape[:2]
    pat = _two_by_two(cols) if block % 2 == 0 else None
    if pat is not None and h >= block and w >= block:
        return _reading_2x2(m, pat, desc, black, white, block)
    block = max(1, min(int(block), h, w))
    hb, wb = h // block, w // block
    blk = np.asarray(black, dtype=np.float32).ravel()
    desc = str(desc).upper()
    names = [n for n in "RGB" if any(ch == n for ch in desc)]
    idx = {n: [i for i, ch in enumerate(desc) if ch == n] for n in names}
    sums = {n: np.zeros((hb, wb), np.float64) for n in names}
    counts = {n: np.zeros((hb, wb), np.float64) for n in names}
    top = {n: -1.0 for n in names}
    at_top = {n: 0 for n in names}
    n_all = {n: 0 for n in names}
    rows = max(1, 384 // block) * block
    for r0 in range(0, hb * block, rows):
        r1 = min(hb * block, r0 + rows)
        nb = (r1 - r0) // block
        a = m[r0:r1, :wb * block].astype(np.float32)
        c = cols[r0:r1, :wb * block]
        if blk.size > 1:
            v = a - blk[np.clip(c, 0, len(blk) - 1)]
        else:
            v = a - np.float32(blk[0] if blk.size else 0.0)
        for n in names:
            sel = np.isin(c, idx[n])
            cnt = int(sel.sum())
            if not cnt:
                continue
            vv = np.where(sel, v, np.float32(0))
            sums[n][r0 // block:r0 // block + nb] += \
                vv.reshape(nb, block, wb, block).sum(axis=(1, 3), dtype=np.float64)
            counts[n][r0 // block:r0 // block + nb] += \
                sel.reshape(nb, block, wb, block).sum(axis=(1, 3))
            t = float(np.max(np.where(sel, a, np.float32(-1))))
            here = int(np.count_nonzero(sel & (a >= t)))
            if t > top[n]:
                top[n], at_top[n] = t, here
            elif t == top[n]:
                at_top[n] += here
            n_all[n] += cnt
    out = FlatReading()
    for n in names:
        if n_all[n] == 0:
            continue
        b0 = float(np.mean(blk[np.clip(np.asarray(idx[n]), 0, len(blk) - 1)])) \
            if blk.size else 0.0
        rng = max(float(white) - b0, 1.0)
        cnt = counts[n].ravel()
        means = (sums[n].ravel()[cnt > 0] / cnt[cnt > 0]) / rng
        out.levels[n] = float(np.percentile(means, LEVEL_PCT))
        pile = (top[n] - b0) / rng >= PILE_FROM and at_top[n] / n_all[n] >= PILE_SHARE
        out.clipped[n] = bool(float(np.percentile(means, CLIP_PCT)) >= CLIP or pile)
    return out


def _reading_2x2(m, pat, desc, black, white, block):
    """`reading_from_mosaic` for a 2x2 colour filter: each of the four
    sub-planes is one colour, so it is averaged as a plain picture (the
    same numbers, ~5x faster on a 50 MP RAW)."""
    blk = np.asarray(black, dtype=np.float32).ravel()
    desc = str(desc).upper()
    half = block // 2
    h, w = m.shape[:2]
    hb, wb = h // block, w // block
    planes = {}
    for dy in range(2):
        for dx in range(2):
            ci = int(pat[dy, dx])
            name = desc[ci] if ci < len(desc) else "?"
            if name not in "RGB":
                continue
            p = m[dy:hb * block:2, dx:wb * block:2]
            b = float(blk[min(ci, len(blk) - 1)]) if blk.size else 0.0
            planes.setdefault(name, []).append((p, b))
    out = FlatReading()
    for name, ps in planes.items():
        acc = np.zeros((hb, wb), np.float64)
        top, at_top, n_all = -1.0, 0, 0
        b0 = float(np.mean([b for _p, b in ps]))
        for p, b in ps:
            acc += (cv2.resize(p.astype(np.float32), (wb, hb), interpolation=cv2.INTER_AREA)
                    - b)
            t = float(p.max())
            here = int(np.count_nonzero(p >= t))
            if t > top:
                top, at_top = t, here
            elif t == top:
                at_top += here
            n_all += p.size
        rng = max(float(white) - b0, 1.0)
        means = (acc / len(ps)).ravel() / rng
        out.levels[name] = float(np.percentile(means, LEVEL_PCT))
        pile = (top - b0) / rng >= PILE_FROM and at_top / n_all >= PILE_SHARE
        out.clipped[name] = bool(float(np.percentile(means, CLIP_PCT)) >= CLIP or pile)
    return out


def read_flat(path, _rect=None):
    """A flat shot's reading (`_rect` is ignored: the whole picture is read,
    its brightest part is the opening)."""
    import rawpy
    with rawpy.imread(path) as raw:
        m = np.array(raw.raw_image_visible, copy=True)
        cols = np.array(raw.raw_colors_visible, copy=True)
        desc = bytes(raw.color_desc).decode("ascii", "ignore")
        black = list(raw.black_level_per_channel)
        white = float(raw.white_level)
    out = reading_from_mosaic(m, cols, desc, black, white)
    out.path = path
    return out


def _two_by_two(cols):
    """The 2x2 pattern when the colour filter repeats every 2 photosites
    (Bayer), else None."""
    h, w = cols.shape[:2]
    if h < 2 or w < 2:
        return None
    pat = cols[:2, :2]
    for dy in range(2):
        for dx in range(2):
            plane = cols[dy::2, dx::2]
            if plane.min() != pat[dy, dx] or plane.max() != pat[dy, dx]:
                return None
    return pat


# --------------------------------------------------------------------------- #
# finding each shot's exposure
# --------------------------------------------------------------------------- #
def ir_rules(isos, start_iso):
    """IR: as much light as possible first (the longest speed up to 30 s at
    the IR shot's ISO), then ISO up at most two stops."""
    numeric = sorted((str(v) for v in isos or [] if be.iso_number(v)), key=be.iso_number)
    want = be.iso_number(start_iso) or 3200.0
    base = (min(numeric, key=lambda v: abs(math.log(be.iso_number(v) / want)))
            if numeric else be.BASE_ISO)
    cap = (be.iso_number(base) or 100.0) * 2 ** IR_ISO_UP_STOPS * 1.01
    top = [v for v in numeric if be.iso_number(base) <= be.iso_number(v) <= cap]
    return {"base_iso": str(base), "max_iso": str(top[-1]) if top else str(base),
            "preferred": IR_LONGEST_S, "longest": IR_LONGEST_S,
            "shortest": be.SHORTEST_S, "target": TARGET}


def light_rules(isos):
    rules = be.default_rules(isos)
    rules["target"] = TARGET
    return rules


class FlatShooting:
    """Find and take each chosen light's flat shot(s).

    `next_shot()` gives `(key, levels, {"shutter", "iso"})` (None once
    finished); `landed(reading)` takes that shot's `FlatReading` (its `path`
    is kept). When `done`: `result` {light: {key: {"path", "shutter", "iso",
    "level", "levels", "dimmed"}}} and `notes`, or `error`."""

    def __init__(self, lights, shutters, isos, first_shutter, ir_start=None,
                 can_dim=True):
        self.lights = [x for x in LIGHT_ORDER if x in (lights or [])]
        self.isos = list(isos or [])
        self.can_dim = bool(can_dim)
        self.shutter_texts = list(shutters or [])
        self.result, self.notes = {}, []
        self.error = None
        self.done = False
        self.shots = 0
        self._pending = False
        self._queue = [(light, key) for light in self.lights for key in SHOTS[light]]
        self._i = 0
        ir_start = dict(ir_start or {})
        self._first = first_shutter
        self._ir_start = ir_start
        if not self._queue:
            self._end(error="No light chosen.")
            return
        if not be.allowed_shutters(self.shutter_texts, light_rules(self.isos)):
            self._end(error="The camera offers no shutter speed FreeCCR can use for "
                            "the flats.")
            return
        self._start_key(None)

    # -- what the caller sees ---------------------------------------------
    @property
    def light(self):
        return None if self.done else self._queue[self._i][0]

    @property
    def key(self):
        return None if self.done else self._queue[self._i][1]

    def what(self):
        """What is being shot, for the caption."""
        if self.done:
            return ""
        light, key = self._queue[self._i]
        if light == "rgb":
            return f"the RGB flat's {SHOT_NAMES[key]}"
        return f"the {SHOT_NAMES[key]} flat"

    def next_shot(self):
        if self.done:
            return None
        self._pending = True
        return self.key, dict(self._levels), {"shutter": self._shutter, "iso": self._iso}

    def landed(self, reading):
        if self.done or not self._pending:
            return
        self._pending = False
        self.shots += 1
        self._shots += 1
        key = self.key
        level = reading.level(key)
        clipped = reading.is_clipped(key)
        path = getattr(reading, "path", "")
        t = be.seconds(self._shutter)
        if not clipped:
            self._tried[self._state_key()] = (level, path)
        if clipped:
            shorter = self._at_or_below(t / BIG_STEP)
            if be.seconds(shorter) < t:
                self._shutter = shorter
                return self._limit()
            if self._iso != self.rules["base_iso"]:
                self._iso = self.rules["base_iso"]
                return self._limit()
            return self._dim(0.5)
        lo, hi = self._accept
        if lo <= level <= hi:
            return self._take(level, path)
        if level < TOO_DARK:
            longer = self._at_or_above(t * BIG_STEP)
            if be.seconds(longer) > t:
                self._shutter = longer
                return self._limit()
            if level <= 0:
                return self._end(error=self._dark_text())
        k = level / (t * be.iso_number(self._iso) / 100.0)
        plan = be.plan_exposure(k, [s for _, s in self._shutters], self.isos, self.rules)
        if plan.dim < 1.0:
            return self._dim(plan.dim)
        if (plan.shutter, plan.iso) == (self._shutter, self._iso) \
                or (plan.shutter, plan.iso, self._led_key()) in self._tried:
            return self._take_best()
        self._shutter, self._iso = plan.shutter, plan.iso
        return self._limit()

    def failed(self, text):
        if not self.done:
            self._end(error=str(text) or "The camera didn't take the shot.")

    def cancel(self):
        if not self.done:
            self._end(error="cancelled")

    # -- steps ----------------------------------------------------------------
    def _start_key(self, prev):
        light, key = self._queue[self._i]
        self._tried = {}
        self._shots = 0
        self._levels = solo_levels(key)
        self._start_levels = dict(self._levels)
        if key == "ir":
            self.rules = ir_rules(self.isos, self._ir_start.get("iso"))
            self._accept = ACCEPT_IR
        else:
            self.rules = light_rules(self.isos)
            self._accept = ACCEPT
        self._shutters = be.allowed_shutters(self.shutter_texts, self.rules)
        if prev is not None and key != "ir" and prev.get("key") != "ir":
            # the next colour starts where the last one settled
            self._shutter, self._iso = self._nearest(be.seconds(prev["shutter"])), prev["iso"]
        elif key == "ir":
            self._shutter = self._nearest(be.seconds(self._ir_start.get("shutter")) or 8.0)
            self._iso = self.rules["base_iso"]
        else:
            self._shutter = self._nearest(be.seconds(self._first) or 1 / 60)
            self._iso = self.rules["base_iso"]

    def _limit(self):
        if self._shots >= MAX_SHOTS:
            return self._take_best()
        return None

    def _state_key(self):
        return (self._shutter, self._iso, self._led_key())

    def _led_key(self):
        return tuple(self._levels.get(k, 0) for k in be.LIGHT_KEYS)

    def _dim(self, factor):
        lit = [k for k in be.LIGHT_KEYS if self._levels.get(k, 0) > 0]
        name = SHOT_NAMES[self.key]
        if not self.can_dim or not lit:
            return self._end(error=f"Even {be.short_text(self._shutters[0][1])} is too "
                                   f"bright for the {name} flat.")
        new = {k: (max(1, int(self._levels[k] * factor * be.DIM_MARGIN)) if k in lit
                   else self._levels.get(k, 0)) for k in be.LIGHT_KEYS}
        if all(new[k] >= self._levels[k] for k in lit):
            return self._end(error=f"Even {be.short_text(self._shutters[0][1])} with the "
                                   f"LED at its lowest is too bright for the {name} flat.")
        self._levels = new
        self._shutter = self._shutters[0][1]
        return self._limit()

    def _take(self, level, path):
        light, key = self._queue[self._i]
        dimmed = self._levels != self._start_levels
        entry = {"key": key, "path": path, "shutter": self._shutter, "iso": self._iso,
                 "level": round(float(level), 4), "levels": dict(self._levels),
                 "dimmed": bool(dimmed)}
        self.result.setdefault(light, {})[key] = entry
        lo = self._accept[0]
        if level < lo:
            self.notes.append(f"The {SHOT_NAMES[key]} flat is darker than ideal "
                              f"({level * 100:.0f} % at {be.short_text(self._shutter)}, "
                              f"ISO {self._iso}): it is still usable.")
        if dimmed:
            self.notes.append(f"The {SHOT_NAMES[key]} LED was dimmed to "
                              + ", ".join(str(v) for v in self._levels.values() if v)
                              + f" (even {be.short_text(self._shutters[0][1])} was too bright).")
        self._i += 1
        if self._i >= len(self._queue):
            self._end()
        else:
            self._start_key(entry)

    def _take_best(self):
        """The camera can't do better (or the shots ran out): the brightest
        unclipped shot tried, if it is at least FLOOR."""
        hi = self._accept[1]
        ok = [(lv, st, p) for st, (lv, p) in self._tried.items() if FLOOR <= lv <= hi]
        if not ok:
            return self._end(error=self._dark_text())
        lv, (sh, iso, led), path = max(ok, key=lambda x: x[0])
        self._shutter, self._iso = sh, iso
        self._levels = dict(zip(be.LIGHT_KEYS, led))
        return self._take(lv, path)

    def _dark_text(self):
        name = SHOT_NAMES[self.key]
        led = "the infrared LED" if self.key == "ir" else f"the {name} LED"
        return (f"The {name} flat is still too dark at {be.short_text(self._shutter)}, ISO "
                f"{self._iso}: is {led} on, and the carrier's opening clear?")

    def _end(self, error=None):
        self.done = True
        self._pending = False
        self.error = error

    def _nearest(self, t):
        return min(self._shutters, key=lambda x: abs(math.log(x[0] / t)))[1]

    def _at_or_below(self, t):
        below = [s for tt, s in self._shutters if tt <= t * 1.001]
        return below[-1] if below else self._shutters[0][1]

    def _at_or_above(self, t):
        above = [s for tt, s in self._shutters if tt >= t * 0.999]
        return above[0] if above else self._shutters[-1][1]


# --------------------------------------------------------------------------- #
# building the flats
# --------------------------------------------------------------------------- #
def file_name(light, fmt):
    """One file per light and format: shooting it again replaces it."""
    safe = re.sub(r"[^A-Za-z0-9]+", "-", str(fmt or "any")).strip("-") or "any"
    return f"Flat {light} {safe}"


def profile_name(light, fmt):
    from core import film_formats
    from core import flat_field as ff
    head = ff.LIGHT_LABELS.get(light, light).split(" (")[0]
    tail = film_formats.short_label(fmt) if fmt else "any format"
    return f"{head} flat · {tail}"


def build_flats(result, fmt, out_dir, demosaic=True, decode=None, merge=None):
    """Build and save each light's flat from its accepted shot(s): a list of
    {"light", "path", "profile"} in light order. `decode` / `merge` stand in
    for the RAW decode and the trichrome merge (tests)."""
    from core import flat_field as ff
    if decode is None:
        decode = ff.decode_calibration
    if merge is None:
        from core import it8_profile

        def merge(paths):
            return it8_profile.decode_target_merged(paths, demosaic=demosaic,
                                                    sample_max=1024)
    saved = []
    for light in LIGHT_ORDER:
        shots = (result or {}).get(light)
        if not shots:
            continue
        if light == "rgb":
            paths = [shots[k]["path"] for k in ("r", "g", "b")]
            arr = merge(paths)
        else:
            paths = [shots[SHOTS[light][0]]["path"]]
            arr = decode(paths[0])
        if arr is None or getattr(arr, "ndim", 0) != 3:
            raise ff.FieldProfileError(f"Couldn't decode the {light} flat's shot.")
        meta = ff.source_metadata(paths[0])
        meta["shots"] = {k: {f: v for f, v in e.items() if f != "path"}
                         for k, e in shots.items()}
        meta["shot_by"] = "Capture"
        prof = ff.build_tagged_profile(arr, profile_name(light, fmt), light=light,
                                       fmt=fmt, meta=meta)
        path = os.path.join(out_dir, file_name(light, fmt) + ff.EXTENSION)
        ff.save_profile(prof, path)
        saved.append({"light": light, "path": path, "profile": prof})
    return saved


def summary(result):
    """'White 1/125 s ISO 100 (61 %) · RGB R 1/60 · G 1/80 · B 1/30, ISO 100 · …'"""
    parts = []
    for light in LIGHT_ORDER:
        shots = (result or {}).get(light)
        if not shots:
            continue
        if light == "rgb":
            isos = {shots[k]["iso"] for k in ("r", "g", "b") if k in shots}
            sp = " · ".join(f"{CHANNEL[k]} {be.short_text(shots[k]['shutter'])}"
                            for k in ("r", "g", "b") if k in shots)
            iso = f", ISO {isos.pop()}" if len(isos) == 1 else ""
            parts.append(f"RGB {sp}{iso}")
        else:
            e = shots[SHOTS[light][0]]
            word = "White" if light == "white" else "IR"
            parts.append(f"{word} {be.short_text(e['shutter'])}, ISO {e['iso']} "
                         f"({e['level'] * 100:.0f} %)")
    return "; ".join(parts)
