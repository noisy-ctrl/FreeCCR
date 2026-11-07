"""Grouping trichrome files into red / green / blue sets by the light that lit
them (spec/smart-triplets.md).

Opening files with 3-way merge used to take them in name order, three at a
time, and refuse a count that wasn't a multiple of 3. Here each RAW's light
colour is read off its mosaic (`scan_type.light_shares`), the files are walked
in name order and every complete set is merged; a colour shot twice in one set
keeps the sharper, unclipped frame (the other becomes an "unused retake");
whatever can't make a set is reported, nothing guessed. A set never spans two
shots: each RAW's picture is compared too (`scan_type.read_light`), and a
frame showing another picture starts a new shot (revision 2026-11-02).
"""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from core import scan_type as st

COLOURS = ("R", "G", "B")
NAMES = {"R": "red", "G": "green", "B": "blue"}
CLIP_LEVEL = 0.985        # a photosite this close to the ceiling counts as clipped
CLIP_SHARE_MAX = 0.01     # more than 1 % of the lit channel clipped: a poor frame
RETAKE_TIE = 0.03         # sharpness within 3 %: the later frame (the retake) wins


@dataclass
class Plan:
    sets: list = field(default_factory=list)       # [(r, g, b) paths]
    notes: list = field(default_factory=list)      # attention entries (dicts)
    chosen: list = field(default_factory=list)     # [(colour, used, [spares])]


def colour_of(shares):
    """"R" / "G" / "B" for a frame lit by one colour (its share the largest and
    at least LIGHT_SHARE_MIN), None for white or mixed light; "?" when it
    can't be told (a monochrome sensor, an unreadable file)."""
    if shares is None:
        return "?"
    k = int(np.argmax(shares))
    if shares[k] < st.LIGHT_SHARE_MIN:
        return None
    return COLOURS[k]


def frame_quality(path, colour):
    """(sharpness, clipped share) of the lit channel's photosites in the
    middle half of the frame. Sharpness is the variance of the Laplacian of
    that plane divided by its mean, squared, so a brighter exposure doesn't
    read as sharper. (0, 1) when it can't be read."""
    try:
        import cv2
        import rawpy
        with rawpy.imread(path) as raw:
            m = raw.raw_image_visible
            pat = np.array(raw.raw_pattern)
            desc = bytes(raw.color_desc).decode("ascii", "ignore").upper()
            s = raw.sizes
            black = float(np.mean(raw.black_level_per_channel))
            white = float(raw.white_level)
            want = desc.find(colour)
            ph, pw = pat.shape
            spot = next(((i, j) for i in range(ph) for j in range(pw)
                         if pat[i, j] == want), None)
            if spot is None:
                return 0.0, 1.0
            r0 = (spot[0] - s.top_margin) % ph
            c0 = (spot[1] - s.left_margin) % pw
            plane = np.array(m[r0::ph, c0::pw], dtype=np.float32, copy=True)
        h, w = plane.shape
        plane = plane[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        clipped = float(np.mean(plane >= black + CLIP_LEVEL * (white - black)))
        v = np.clip(plane - black, 0, None)
        mean = float(v.mean())
        if mean <= 1e-6:
            return 0.0, clipped
        lap = cv2.Laplacian(v / mean, cv2.CV_32F, ksize=3)
        return float(lap.var()), clipped
    except Exception:
        return 0.0, 1.0


def pick_best(paths, colour, quality=frame_quality):
    """The frame to use among takes of one colour, and why: unclipped beats
    clipped, then the sharpest; within RETAKE_TIE the later take wins."""
    scored = [(p,) + tuple(quality(p, colour)) for p in paths]
    ok = [x for x in scored if x[2] <= CLIP_SHARE_MAX] or scored
    best = max(x[1] for x in ok)
    near = [x for x in ok if x[1] >= best * (1 - RETAKE_TIE)]
    use = near[-1][0]                                  # name order: the later take
    why = "sharper" if len(near) == 1 else "the later take"
    if len(ok) < len(scored):
        why = "not clipped" if len(near) == 1 else why
    return use, why


def _reading(r):
    """(shares, picture) from what a reader returned: a `LightReading`, or
    plain shares (a test's reader, or anything that only knows the light)."""
    if isinstance(r, st.LightReading):
        return r.shares, r.picture
    return r, None


def rescue_weak_frames(colours, shares, pictures, reach=2):
    """Rule 3 (spec/smart-triplets.md revision 2026-11-02): a frame that read
    "white or mixed" is taken as colour c when its largest share is c (at
    least RESCUE_SHARE_MIN) and it and the frames within `reach` either side
    that show the SAME picture (known, not "can't tell") are exactly one red,
    one green and one blue (counting another weak frame by its largest share).
    Returns the new colour list and the indices rescued."""
    def lead(j):
        """A frame's colour: read, or (weak but rescuable) its largest share."""
        if colours[j] in COLOURS:
            return colours[j]
        if colours[j] is None and shares[j] is not None:
            k = int(np.argmax(shares[j]))
            if st.rescuable(shares[j], k):
                return COLOURS[k]
        return None

    out = list(colours)
    rescued = []
    for i, c in enumerate(colours):
        if c is not None or pictures[i] is None or lead(i) is None:
            continue
        # this frame and its neighbours that show the same picture; two weak
        # frames of one shot are taken together when the three leads are R, G, B
        shot = [i] + [j for j in range(max(0, i - reach), min(len(colours), i + reach + 1))
                      if j != i and lead(j) is not None
                      and st.same_picture(pictures[i], pictures[j]) is True]
        if sorted(lead(j) for j in shot) == sorted(COLOURS):
            out[i] = lead(i)
            rescued.append(i)
    return out, rescued


def natural_key(path):
    """A file name as a person reads it: 'DSCF0002_2' before 'DSCF0002_10'."""
    import re
    name = os.path.basename(path).lower()
    parts = re.split(r"(\d+)", name)
    return (tuple((1, int(t), "") if t.isdigit() else (0, 0, t) for t in parts if t),
            os.path.normcase(path))


def capture_order(paths, time_fn=None, workers=1):
    """`paths` in the order they were SHOT (revision 2026-11-07): by each
    file's EXIF capture time when every file has one, the name read
    naturally breaking ties; otherwise in natural name order. Name order
    alone interleaved shots: a camera whose counter restarted gives
    DSCF0001.raf, DSCF0001_1.raf, ... (the tether's "_1 on a name clash"),
    so DSCF0001 of every session came first, then every DSCF0002."""
    paths = list(paths)
    time_fn = time_fn or st.capture_time
    if workers > 1 and len(paths) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            times = list(ex.map(time_fn, paths))
    else:
        times = [time_fn(p) for p in paths]
    if paths and all(times):
        when = dict(zip(paths, times))
        return sorted(paths, key=lambda p: (when[p], natural_key(p)))
    return sorted(paths, key=natural_key)


def plan_triplets(paths, shares_fn=None, quality=frame_quality, workers=None,
                  time_fn=None):
    """Plan the merge of `paths` (any order, any count), taken in the order
    they were shot (`capture_order`; `time_fn(path)` gives a capture time or
    None, default `scan_type.capture_time`). `shares_fn(path)` returns a
    `scan_type.LightReading` (the default, `read_light`) or plain shares."""
    shares_fn = shares_fn or st.read_light
    # Four readers at most: each holds a whole RAW while it reads (~100 MB for
    # 50 MP), and on a 2-core machine more threads were slower (measured).
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    ordered = capture_order(paths, time_fn, workers)
    if workers > 1 and len(ordered) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            readings = list(ex.map(shares_fn, ordered))
    else:
        readings = [shares_fn(p) for p in ordered]
    shares, pictures = zip(*(_reading(r) for r in readings)) if readings else ((), ())
    colours = [colour_of(sh) for sh in shares]
    colours, _rescued = rescue_weak_frames(colours, list(shares), list(pictures))
    plan = Plan()
    seen = {}
    raw_quality = quality

    def quality(p, c):                         # each frame is read once
        if (p, c) not in seen:
            seen[(p, c)] = raw_quality(p, c)
        return seen[(p, c)]

    if ordered and all(c == "?" for c in colours):
        # nothing to tell the light by (a monochrome camera): name order
        n = len(ordered) // 3 * 3
        plan.sets = [tuple(ordered[i:i + 3]) for i in range(0, n, 3)]
        if ordered[n:]:
            plan.notes.append(_note(ordered[n:], "Left over",
                                    f"{len(ordered) - n} file(s) after the last full set "
                                    "of three (their light couldn't be read, so they are "
                                    "taken in name order)"))
        return plan

    # Which colour starts a shot isn't known (R -> G -> B here, but a folder can
    # begin mid-shot, or another app may shoot in another order): plan with
    # each start and keep the plan that makes the most sets (then the fewest
    # notes; red first on a tie).
    best = None
    for start in COLOURS:
        cand = _walk(ordered, colours, start, quality, pictures)
        key = (len(cand.sets), -len(cand.notes))
        if best is None or key > best[0]:
            best = (key, cand)
    return best[1]


def _walk(ordered, colours, start, quality, pictures=None):
    plan = Plan()
    cur = {}                                   # colour -> [paths, in name order]
    pictures = list(pictures) if pictures is not None else [None] * len(ordered)
    shown = []                                 # pictures of the shot being collected

    def close():
        if not cur:
            return
        if all(c in cur for c in COLOURS):
            trip, spares = [], []
            for c in COLOURS:
                takes = cur[c]
                if len(takes) == 1:
                    trip.append(takes[0])
                    continue
                use, why = pick_best(takes, c, quality)
                trip.append(use)
                rest = [p for p in takes if p != use]
                plan.chosen.append((c, use, rest))
                spares += [(c, sp, use, why) for sp in rest]
            plan.sets.append(tuple(trip))
            for c, sp, use, why in spares:
                alt = [sp if c2 == c else p2 for c2, p2 in zip(COLOURS, trip)]
                plan.notes.append(_note(
                    alt, "Unused retake",
                    f"{NAMES[c]} was shot twice; FreeCCR used {os.path.basename(use)} "
                    f"({why}). Merge Anyway makes the frame with "
                    f"{os.path.basename(sp)} instead"))
        else:
            files = sorted((p for c in COLOURS for p in cur.get(c, [])),
                           key=ordered.index)
            have = ", ".join(NAMES[c] for c in COLOURS if c in cur)
            plan.notes.append(_note(files, "Frames missing",
                                    f"only {have} for this shot: no full red, green, "
                                    "blue set"))
        cur.clear()
        shown.clear()

    for path, c, pic in zip(ordered, colours, pictures):
        if c not in COLOURS:
            why = ("its light couldn't be read" if c == "?"
                   else "it looks lit by white or mixed light, not one colour")
            plan.notes.append(_note([path], "Not one colour", f"{os.path.basename(path)}: {why}"))
            continue
        # one picture per set: a frame known to show another picture than
        # the shot being collected starts a new one, whatever its colour
        # (spec/smart-triplets.md revision 2026-11-02, rule 2)
        if cur and any(st.same_picture(pic, q) is False for q in shown) \
                and not any(st.same_picture(pic, q) is True for q in shown):
            close()
        complete = all(k in cur for k in COLOURS)
        if c == start and cur and (complete or any(k != start for k in cur)):
            close()                              # a new shot starts
        cur.setdefault(c, []).append(path)
        if pic is not None:
            shown.append(pic)
    close()
    return plan


def _note(paths, short, reason):
    return {"paths": list(paths), "short": short, "reason": reason}
