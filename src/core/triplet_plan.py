"""Grouping trichrome files into red / green / blue sets by the light that lit
them (spec/smart-triplets.md).

Opening files with 3-way merge used to take them in name order, three at a
time, and refuse a count that wasn't a multiple of 3. Here each RAW's light
colour is read off its mosaic (`scan_type.light_shares`), the files are walked
in name order and every complete set is merged; a colour shot twice in one set
keeps the sharper, unclipped frame (the other becomes an "unused retake");
whatever can't make a set is reported, nothing guessed.
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


def plan_triplets(paths, shares_fn=None, quality=frame_quality, workers=None):
    """Plan the merge of `paths` (any order, any count)."""
    shares_fn = shares_fn or st.light_shares
    ordered = sorted(paths, key=lambda p: (os.path.basename(p).lower(), os.path.normcase(p)))
    # Four readers at most: each holds a whole RAW while it reads (~100 MB for
    # 50 MP), and on a 2-core machine more threads were slower (measured).
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    if workers > 1 and len(ordered) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            shares = list(ex.map(shares_fn, ordered))
    else:
        shares = [shares_fn(p) for p in ordered]
    colours = [colour_of(sh) for sh in shares]
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
        cand = _walk(ordered, colours, start, quality)
        key = (len(cand.sets), -len(cand.notes))
        if best is None or key > best[0]:
            best = (key, cand)
    return best[1]


def _walk(ordered, colours, start, quality):
    plan = Plan()
    cur = {}                                   # colour -> [paths, in name order]

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

    for path, c in zip(ordered, colours):
        if c not in COLOURS:
            why = ("its light couldn't be read" if c == "?"
                   else "it looks lit by white or mixed light, not one colour")
            plan.notes.append(_note([path], "Not one colour", f"{os.path.basename(path)}: {why}"))
            continue
        complete = all(k in cur for k in COLOURS)
        if c == start and cur and (complete or any(k != start for k in cur)):
            close()                              # a new shot starts
        cur.setdefault(c, []).append(path)
    close()
    return plan


def _note(paths, short, reason):
    return {"paths": list(paths), "short": short, "reason": reason}
