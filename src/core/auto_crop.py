"""Auto crop: find the picture area of a negative scan, straighten it, and
return it as a crop (spec/auto-crop.md).

Everything runs on the raw (unconverted) preview in log10 luminance:

  straighten -> candidate straight edges -> rectangle search (edge strength,
  flat band outside, format prior, 35mm sprocket rows) -> peel holder and
  rebate -> trim narrow border bands -> inset past the edge blur.

The result is a normalised `crop_rect` in the UNROTATED image plus a
`crop_angle`, i.e. exactly what the Crop panel produces, so preview, export and
the catalog need nothing new.
"""

from dataclasses import dataclass, field
import itertools
from typing import Optional, Tuple

import cv2
import numpy as np

# --- Formats -----------------------------------------------------------------
FORMATS = {"35mm 3:2": 1.5, "645": 56 / 41.5, "6x6": 1.0, "6x7": 69 / 56,
           "6x9": 84 / 56, "half-frame": 24 / 18, "xpan": 65 / 24}
# Formats that should not win on proportions alone.
RARE = {"half-frame": 0.08, "xpan": 0.08}

# --- Tuning (fitted on 30 scans from three setups; see spec) ------------------
TAU = 0.05                # gradient (log10 / 2 px) that counts as an edge pixel
CANDIDATES = 10           # strongest columns / rows kept as candidate edges
MIN_SIDE = 0.35           # each side of the frame >= 35% of the scan
ASPECT_SLACK = 0.03       # within ~3% of a format: no penalty (gates/masks vary)
ASPECT_WEIGHT = 3.0
STRIP_SLACK = 0.01        # a 35mm gate is a precise 36x24
STRIP_WEIGHT = 15.0       # a strip frame that is not 3:2 is not a frame
ROUGHNESS_WEIGHT = 4.0
BORDER_COV = 0.1          # token coverage of an edge placed on the scan border
BORDER_OUT = 0.05         # ...and a small roughness: nothing outside vouches for it
OUT_BAND = 6
OUT_GAPS = (3, 8, 14, 22, 32, 44)   # the outside band may start past the blur
SYM_WEIGHT = 15.0         # strip: frame off-centre between the sprocket rows
GAP_PENALTY = 0.6         # strip: inter-frame gap inside a side edge
HOLDER_GAP = 0.8          # log10: outside this much darker than inside = holder
INSET_FRAC = 0.006        # final inset, fraction of the short side
MAX_SKEW = 4.0


@dataclass
class FrameCrop:
    """A detected frame. `rect` (normalised x1, y1, x2, y2 in the unrotated
    image) and `angle` (degrees, Qt clockwise-positive) are drop-in values for
    CCRImage.crop_rect / crop_angle."""
    rect: Optional[Tuple[float, float, float, float]]
    angle: float = 0.0
    confidence: str = "none"          # high | medium | low | none
    soft: Tuple[str, ...] = ()        # sides where the gate is invisible
    strip: bool = False               # 35mm strip (sprocket rows seen)
    fmt: str = ""                     # nearest film format
    reason: str = ""
    debug: dict = field(default_factory=dict, repr=False)

    @property
    def usable(self) -> bool:
        return self.rect is not None and self.confidence in ("high", "medium")

    def summary(self) -> str:
        if self.rect is None:
            return self.reason or "no frame found"
        bits = [self.fmt or "frame", f"{self.angle:+.2f}°"]
        if self.strip:
            bits.append("35mm strip")
        if self.soft:
            bits.append("soft edge " + "/".join(_SIDE_NAMES[s] for s in self.soft))
        return ", ".join(bits)


_SIDE_NAMES = {"L": "left", "R": "right", "T": "top", "B": "bottom"}


# --- Primitives --------------------------------------------------------------
def log_luminance(raw: np.ndarray) -> np.ndarray:
    raw = np.asarray(raw)
    m = raw.astype(np.float32).mean(axis=2) if raw.ndim == 3 else raw.astype(np.float32)
    full = 65535.0 if raw.dtype == np.uint16 or m.max() > 255.5 else 255.0
    return np.log10(np.maximum(m, full * 16.0 / 65535.0) / full)


def _rotate(img, deg):
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), deg, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def skew_angle(L: np.ndarray, max_deg: float = MAX_SKEW) -> float:
    """Angle (degrees, getRotationMatrix2D convention) that makes the strongest
    straight edges axis-aligned: the one maximising the peakiness of the column
    projection of |dL/dx| and the row projection of |dL/dy|."""
    def grads(img):
        s = cv2.GaussianBlur(img, (0, 0), 1.0)
        gx = np.abs(cv2.Sobel(s, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(s, cv2.CV_32F, 0, 1, ksize=3))
        return (np.minimum(gx, np.percentile(gx, 99.5)),
                np.minimum(gy, np.percentile(gy, 99.5)))

    def score(gx, gy, a):
        rx = _rotate(gx, a).sum(axis=0)
        ry = _rotate(gy, a).sum(axis=1)
        return float((rx ** 2).sum() / max(rx.sum(), 1e-9) ** 2 * rx.size
                     + (ry ** 2).sum() / max(ry.sum(), 1e-9) ** 2 * ry.size)

    small = cv2.resize(L, (max(8, L.shape[1] // 2), max(8, L.shape[0] // 2)),
                       interpolation=cv2.INTER_AREA)
    sgx, sgy = grads(small)
    coarse = np.arange(-max_deg, max_deg + 1e-6, 0.25)
    a0 = float(coarse[int(np.argmax([score(sgx, sgy, a) for a in coarse]))])
    gx, gy = grads(L)
    fine = np.arange(a0 - 0.25, a0 + 0.25 + 1e-6, 0.02)
    return float(fine[int(np.argmax([score(gx, gy, a) for a in fine]))])


def _peaks(p, k, min_sep):
    out = []
    for i in np.argsort(p)[::-1]:
        if p[i] <= 0:
            break
        if all(abs(int(i) - j) >= min_sep for j in out):
            out.append(int(i))
        if len(out) >= k:
            break
    return sorted(out)


class _Edges:
    """Cumulative strong-gradient counts (coverage of any edge segment in O(1))
    and integral images for band statistics (on a dust-filtered copy: specks
    and hairline scratches in a rebate band must not make it look like
    picture)."""

    def __init__(self, L, tau=TAU):
        gx = np.zeros_like(L); gx[:, 1:-1] = L[:, 2:] - L[:, :-2]
        gy = np.zeros_like(L); gy[1:-1, :] = L[2:, :] - L[:-2, :]
        z_r = np.zeros((1, L.shape[1])); z_c = np.zeros((L.shape[0], 1))
        self.cx_p = np.vstack([z_r, np.cumsum(gx > tau, axis=0)])
        self.cx_n = np.vstack([z_r, np.cumsum(gx < -tau, axis=0)])
        self.cy_p = np.hstack([z_c, np.cumsum(gy > tau, axis=1)])
        self.cy_n = np.hstack([z_c, np.cumsum(gy < -tau, axis=1)])
        ii = cv2.integral2(cv2.medianBlur(L.astype(np.float32), 5).astype(np.float64))
        self.s1, self.s2 = ii[0], ii[1]
        self.h, self.w = L.shape

    def vcov(self, x, y0, y1):
        n = max(1, y1 - y0)
        best = 0.0
        for xx in (x - 1, x, x + 1):
            if 0 <= xx < self.w:
                best = max(best, (self.cx_p[y1, xx] - self.cx_p[y0, xx]) / n,
                           (self.cx_n[y1, xx] - self.cx_n[y0, xx]) / n)
        return best

    def hcov(self, y, x0, x1):
        n = max(1, x1 - x0)
        best = 0.0
        for yy in (y - 1, y, y + 1):
            if 0 <= yy < self.h:
                best = max(best, (self.cy_p[yy, x1] - self.cy_p[yy, x0]) / n,
                           (self.cy_n[yy, x1] - self.cy_n[yy, x0]) / n)
        return best

    def _sums(self, x0, y0, x1, y1):
        x0, x1 = max(0, min(self.w, x0)), max(0, min(self.w, x1))
        y0, y1 = max(0, min(self.h, y0)), max(0, min(self.h, y1))
        n = (x1 - x0) * (y1 - y0)
        if n <= 0:
            return 0, 0.0, 0.0
        s = self.s1[y1, x1] - self.s1[y0, x1] - self.s1[y1, x0] + self.s1[y0, x0]
        q = self.s2[y1, x1] - self.s2[y0, x1] - self.s2[y1, x0] + self.s2[y0, x0]
        return n, s, q

    def mean(self, x0, y0, x1, y1):
        n, s, _q = self._sums(x0, y0, x1, y1)
        return float(s / n) if n else 0.0

    def std(self, x0, y0, x1, y1):
        n, s, q = self._sums(x0, y0, x1, y1)
        return float(np.sqrt(max(q / n - (s / n) ** 2, 0.0))) if n else 0.0


# --- 35mm strips -------------------------------------------------------------
def _periodic_holes(bright_frac) -> bool:
    m = bright_frac > 0.5
    runs, start = [], None
    for i, v in enumerate(m):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i)); start = None
    runs = [r for r in runs if r[1] - r[0] >= 4]
    if len(runs) < 4:
        return False
    widths = np.array([r[1] - r[0] for r in runs], float)
    steps = np.diff([(r[0] + r[1]) / 2 for r in runs])
    return bool(widths.std() / widths.mean() < 0.35 and steps.std() / steps.mean() < 0.2)


def sprocket_bands(L, clip):
    """(inner edge of the top sprocket band, inner edge of the bottom one) of a
    horizontal 35mm strip, None for a band that isn't there: a run of rows near
    the top/bottom whose clipped pixels form evenly spaced holes."""
    h, w = L.shape
    bright = L > clip - 0.08
    rowfrac = bright[:, int(w * .02):int(w * .98)].mean(axis=1)
    hole_row = (rowfrac > 0.12) & (rowfrac < 0.8)

    def find(rows):
        idx = [y for y in rows if hole_row[y]]
        if len(idx) < 12:
            return None
        lo, hi = min(idx), max(idx) + 1
        return (lo, hi) if _periodic_holes(bright[lo:hi].mean(axis=0)) else None

    top = find(range(0, int(h * .25)))
    bot = find(range(int(h * .75), h))
    return (top[1] if top else None), (bot[0] if bot else None)


def _strip_gap_columns(L, st, sb, tol=0.035):
    """35mm strip: columns that are bare film base over the picture rows (the
    gap between frames). The base is read from the rebate between the sprocket
    holes, so it is exact for this frame; the column MEDIAN is used because a
    gap can carry a light leak or the neighbour's edge."""
    h, w = L.shape
    clip = float(L.max())
    parts = []
    if st is not None:
        parts.append(L[max(0, st - 30):st])
    if sb is not None:
        parts.append(L[sb:min(h, sb + 30)])
    if not parts:
        return None
    reb = np.concatenate([p.ravel() for p in parts])
    reb = reb[reb < clip - 0.1]
    if reb.size < 100:
        return None
    base = float(np.median(reb))
    r0 = (st if st is not None else 0) + 40
    r1 = (sb if sb is not None else h) - 40
    if r1 - r0 < 50:
        return None
    colmed = np.median(L[r0:r1], axis=0)
    return np.abs(colmed - base) < tol


# --- Rectangle search ----------------------------------------------------------
def _format_fit(ar, strip):
    fmts = {"35mm 3:2": 1.5} if strip else FORMATS
    slack = STRIP_SLACK if strip else ASPECT_SLACK
    best = (9.0, "")
    for name, v in fmts.items():
        for f in (v, 1 / v):
            d = max(0.0, abs(np.log(ar / f)) - slack) + RARE.get(name, 0.0)
            if d < best[0]:
                best = (d, name)
    return best


def _score(E, rect, strip, gap=None, bands=None):
    w, h = E.w, E.h
    x0, y0, x1, y1 = rect
    my, mx = int((y1 - y0) * .06), int((x1 - x0) * .06)
    covs = [E.vcov(x0, y0 + my, y1 - my), E.vcov(x1, y0 + my, y1 - my),
            E.hcov(y0, x0 + mx, x1 - mx), E.hcov(y1, x0 + mx, x1 - mx)]
    border = [x0 <= 1, x1 >= w - 2, y0 <= 1, y1 >= h - 2]
    covs = [BORDER_COV if bd else c for c, bd in zip(covs, border)]

    def out_std(side):
        b, vals = OUT_BAND, []
        for g in OUT_GAPS:
            if side == 0 and x0 - g - b >= 0:
                vals.append(E.std(x0 - g - b, y0 + my, x0 - g, y1 - my))
            elif side == 1 and x1 + g + b <= w:
                vals.append(E.std(x1 + g, y0 + my, x1 + g + b, y1 - my))
            elif side == 2 and y0 - g - b >= 0:
                vals.append(E.std(x0 + mx, y0 - g - b, x1 - mx, y0 - g))
            elif side == 3 and y1 + g + b <= h:
                vals.append(E.std(x0 + mx, y1 + g, x1 - mx, y1 + g + b))
        # no room for an outside band (an edge hugging the scan border):
        # nothing vouches for it, as for the border itself
        return min(vals) if vals else BORDER_OUT

    outs = [BORDER_OUT if bd else out_std(i) for i, bd in enumerate(border)]
    # A band far darker than the picture beside it is holder, whatever its
    # gradient (vignetting, a mask bevel): as good as flat.
    ins = [E.mean(x0 + 4, y0 + my, x0 + 16, y1 - my), E.mean(x1 - 16, y0 + my, x1 - 4, y1 - my),
           E.mean(x0 + mx, y0 + 4, x1 - mx, y0 + 16), E.mean(x0 + mx, y1 - 16, x1 - mx, y1 - 4)]
    outm = [E.mean(x0 - 30, y0 + my, x0 - 4, y1 - my), E.mean(x1 + 4, y0 + my, x1 + 30, y1 - my),
            E.mean(x0 + mx, y0 - 30, x1 - mx, y0 - 4), E.mean(x0 + mx, y1 + 4, x1 - mx, y1 + 30)]
    outs = [0.02 if (not bd and om < im - HOLDER_GAP) else o
            for o, om, im, bd in zip(outs, outm, ins, border)]
    ar = (x1 - x0) / max(1, (y1 - y0))
    fit, fmt = _format_fit(ar, strip)
    area = (x1 - x0) * (y1 - y0) / float(w * h)
    score = (sum(covs)
             - ROUGHNESS_WEIGHT * sum(min(max(o - 0.02, 0.0), 0.25) for o in outs)
             + area - (STRIP_WEIGHT if strip else ASPECT_WEIGHT) * fit)
    if bands is not None and bands[0] is not None and bands[1] is not None:
        # a 35mm frame sits centred between its sprocket rows
        st, sb = bands
        score -= SYM_WEIGHT * abs((y0 - st) - (sb - y1)) / max(1.0, sb - st)
    if gap is not None:
        # 35mm strip: bare film base just inside a side edge means the box has
        # taken in the gap between frames
        for a, b in ((x0 + 2, x0 + 12), (x1 - 12, x1 - 2)):
            seg = gap[max(0, a):max(0, b)]
            if seg.size and seg.mean() > 0.5:
                score -= GAP_PENALTY
    return dict(score=score, rect=rect, covs=covs, outs=outs, border=border,
                aspect=ar, fit=fit, fmt=fmt, area=area)


def _format_snap(E, best, strip, colp, gap=None, bands=None, ratio=1.5, search=0.015):
    """35mm strip: a gate edge at base density is too faint to be shortlisted.
    Try each vertical edge at the exact 3:2 position (snapped to the strongest
    column within ±1.5%) and keep it when the score improves."""
    x0, y0, x1, y1 = best["rect"]
    want = int(round(ratio * (y1 - y0)))
    r = max(2, int(search * E.w))
    trials = []
    c = x0 + want
    if c < E.w - 2:
        lo, hi = max(0, c - r), min(E.w, c + r + 1)
        trials.append((x0, y0, lo + int(np.argmax(colp[lo:hi])), y1))
    c = x1 - want
    if c > 1:
        lo, hi = max(0, c - r), min(E.w, c + r + 1)
        trials.append((lo + int(np.argmax(colp[lo:hi])), y0, x1, y1))
    for t in trials:
        sc = _score(E, t, strip, gap, bands)
        if sc["score"] > best["score"]:
            best = sc
    return best


def _search(L):
    h, w = L.shape
    E = _Edges(L)
    yc0, yc1, xc0, xc1 = int(h * .2), int(h * .8), int(w * .2), int(w * .8)
    colp = np.array([E.vcov(x, yc0, yc1) for x in range(w)])
    rowp = np.array([E.hcov(y, xc0, xc1) for y in range(h)])
    st, sb = sprocket_bands(L, float(L.max()))
    strip = st is not None or sb is not None
    xs = sorted(set(_peaks(colp, CANDIDATES, 4) + [1, w - 2]))
    ys = sorted(set(_peaks(rowp, CANDIDATES, 4) + [1, h - 2]))
    gap = None
    if strip:
        lo = st if st is not None else 1
        hi = sb if sb is not None else h - 2
        ys = [y for y in ys if lo + 2 <= y <= hi - 2]
        gap = _strip_gap_columns(L, st, sb)
        if gap is not None:
            # boundaries of base-level column runs are where frames start/stop
            edges = np.flatnonzero(np.diff(gap.astype(np.int8)) != 0)
            xs = sorted(set(xs + [int(e) + 1 for e in edges if 1 < e < w - 2]))
        if st is not None and sb is not None:
            # The frame sits centred between the sprocket rows: mirror each
            # candidate, so an invisible gate edge still has a candidate.
            ys = sorted(set(ys + [st + (sb - y) for y in ys] + [sb - (y - st) for y in ys]))
            ys = [y for y in ys if st + 2 <= y <= sb - 2]
    best = None
    for x0, x1 in itertools.combinations(xs, 2):
        if x1 - x0 < MIN_SIDE * w:
            continue
        for y0, y1 in itertools.combinations(ys, 2):
            if y1 - y0 < MIN_SIDE * h:
                continue
            sc = _score(E, (x0, y0, x1, y1), strip, gap, (st, sb))
            if best is None or sc["score"] > best["score"]:
                best = sc
    if best is not None and strip:
        best = _format_snap(E, best, strip, colp, gap, (st, sb))
    return best, strip


# --- Edge clean-up -------------------------------------------------------------
def _normal_profile(L, rect, side, reach, outside=6, margin=.15):
    """Median brightness along `side` of `rect`, from `outside` px outside to
    `reach` px inside (index `outside` = the edge line)."""
    x0, y0, x1, y1 = rect
    h, w = L.shape
    my, mx = int((y1 - y0) * margin), int((x1 - x0) * margin)
    ks = range(-outside, reach + 1)
    if side in ("L", "R"):
        sgn, pos = (1, x0) if side == "L" else (-1, x1)
        rows = slice(y0 + my, y1 - my)
        return np.array([np.median(L[rows, min(w - 1, max(0, pos + sgn * k))]) for k in ks])
    sgn, pos = (1, y0) if side == "T" else (-1, y1)
    cols = slice(x0 + mx, x1 - mx)
    return np.array([np.median(L[min(h - 1, max(0, pos + sgn * k)), cols]) for k in ks])


def _shrink(rect, d):
    x0, y0, x1, y1 = rect
    return (x0 + d["L"], y0 + d["T"], x1 - d["R"], y1 - d["B"])


def _peel(L, rect, max_frac=0.05, flat=0.05, step=0.12):
    """Holder edge -> picture edge. When the band outside an edge is far darker
    than inside, walk in through the blur, across the bright rebate plateau, to
    the next step (the gate). None within `max_frac`: stop at the plateau and
    call the edge soft (picture at base density there)."""
    x0, y0, x1, y1 = rect
    moved, soft, plateaus = {}, {}, []
    for side in ("L", "R", "T", "B"):
        dim = (x1 - x0) if side in ("L", "R") else (y1 - y0)
        reach = max(12, int(dim * max_frac))
        p = _normal_profile(L, rect, side, reach)
        out = float(np.median(p[:4]))
        k = 6
        while k + 2 < len(p) and abs(p[k + 2] - p[k]) > 0.04:
            k += 1
        plateau = float(p[k])
        if plateau - out < HOLDER_GAP:
            moved[side], soft[side] = 0, False
            continue
        plateaus.append(plateau)
        j = k
        while j + 1 < len(p) and abs(p[j + 1] - plateau) < flat:
            j += 1
        if j + 4 < len(p) and abs(p[j + 4] - plateau) > step:
            g = j + 1
            while g + 2 < len(p) and abs(p[g + 2] - p[g]) > 0.04:
                g += 1
            moved[side], soft[side] = max(0, g - 6), False
        else:
            moved[side], soft[side] = max(0, k - 6), True
    return _shrink(rect, moved), moved, soft, plateaus


def _trim(L, rect, base, max_frac=0.03, min_w=3, flat=0.05):
    """Every edge, scan border included: past any ramp, a narrow band that is
    holder-black or at the rebate's level, followed by a step to the picture,
    is cut off."""
    x0, y0, x1, y1 = rect
    out = {}
    for side in ("L", "R", "T", "B"):
        dim = (x1 - x0) if side in ("L", "R") else (y1 - y0)
        lim = int(dim * max_frac)
        p = _normal_profile(L, rect, side, lim + 16)[6:]
        inside = float(np.median(p[-8:]))
        k = 0
        while k + 2 < len(p) and abs(p[k + 2] - p[k]) > 0.04 and k < lim:
            k += 1
        lvl = float(p[k])
        holder = lvl < inside - HOLDER_GAP
        baseish = lvl > inside + 0.25 and (base is None or abs(lvl - base) < 0.15)
        if not (holder or baseish):
            out[side] = 0
            continue
        j = k
        if holder:
            while j + 1 < len(p) and p[j + 1] < inside - HOLDER_GAP:
                j += 1
            ok = j - k + 1 >= min_w and j <= int(dim * 0.06)
        else:
            while j + 1 < len(p) and abs(p[j + 1] - lvl) < flat:
                j += 1
            ok = j - k + 1 >= min_w and j <= lim
        if not ok:
            out[side] = 0
            continue
        g = j + 1
        while g + 2 < len(p) and abs(p[g + 2] - p[g]) > 0.04:
            g += 1
        reached = abs(p[min(g, len(p) - 1)] - inside) < abs(lvl - inside) * 0.5
        out[side] = g if reached else 0
    return _shrink(rect, out), out


def _blur_inward(L, rect, reach=14, frac=0.9):
    """Each edge moved inward to where its blur ends: 90% of the step from the
    outside level (5..14 px out) to the inside level (8..14 px in), along the
    median profile across the edge."""
    x0, y0, x1, y1 = rect
    h, w = L.shape
    d = {}
    for side in ("L", "R", "T", "B"):
        at_border = ((side == "L" and x0 <= 1) or (side == "R" and x1 >= w - 2)
                     or (side == "T" and y0 <= 1) or (side == "B" and y1 >= h - 2))
        d[side] = 0
        if at_border:
            continue
        p = _normal_profile(L, rect, side, reach, outside=reach, margin=.1)
        out_lvl = float(np.median(p[:reach - 5]))
        in_lvl = float(np.median(p[reach + 8:]))
        stp = in_lvl - out_lvl
        if abs(stp) < 0.04:
            continue
        target = out_lvl + frac * stp
        for j in range(reach - 4, len(p)):
            if (p[j] - target) * np.sign(stp) >= 0:
                d[side] = max(0, j - reach)
                break
    return _shrink(rect, d), d


# --- Public API ----------------------------------------------------------------
def detect_frame(raw: np.ndarray, inset_frac: float = INSET_FRAC) -> FrameCrop:
    """Detect the picture area of an unconverted scan (H, W, 3 or H, W)."""
    if raw is None or np.asarray(raw).ndim not in (2, 3) or min(np.asarray(raw).shape[:2]) < 64:
        return FrameCrop(None, reason="image too small")
    L0 = log_luminance(raw)
    h, w = L0.shape
    ang = skew_angle(L0)
    L = cv2.GaussianBlur(_rotate(L0, ang), (0, 0), 1.0)
    best, strip = _search(L)
    if best is None:
        return FrameCrop(None, angle=ang, reason="no frame edges found", strip=strip)
    rect0 = best["rect"]
    rect, peeled, soft, plateaus = _peel(L, rect0)
    if strip:
        # the sprocket rows and the exact 3:2 already place a strip frame; a
        # band of picture at base density would look like rebate to the trim
        trimmed = {"L": 0, "R": 0, "T": 0, "B": 0}
    else:
        rect, trimmed = _trim(L, rect, max(plateaus) if plateaus else None)
    rect, blurred = _blur_inward(L, rect)
    x0, y0, x1, y1 = rect
    ins = int(round(inset_frac * min(x1 - x0, y1 - y0)))
    x0, y0, x1, y1 = x0 + ins, y0 + ins, x1 - ins, y1 - ins
    soft_sides = tuple(s for s in ("L", "R", "T", "B") if soft.get(s))
    debug = dict(best=best, peel=peeled, trim=trimmed, blur=blurred, inset=ins,
                 deskewed_rect=(x0, y0, x1, y1), plateaus=plateaus)

    n_border = sum(best["border"])
    real_covs = [c for c, bd in zip(best["covs"], best["border"]) if not bd]
    if x1 - x0 < MIN_SIDE * w * 0.8 or y1 - y0 < MIN_SIDE * h * 0.8:
        return FrameCrop(None, angle=ang, strip=strip, reason="frame too small", debug=debug)
    if n_border >= 3 or (best["area"] > 0.97 and n_border >= 2):
        return FrameCrop(None, angle=ang, strip=strip, debug=debug,
                         reason="no border found: the picture fills the scan")
    if n_border >= 2:
        conf, why = "low", "two edges are the scan border"
    elif real_covs and min(real_covs) < 0.2 and not (strip and best["fit"] == 0.0):
        # (on a 35mm strip that fits 3:2 exactly, a gate at base density is
        # expected to be faint: the sprocket rows and the format place it)
        conf, why = "low", "an edge is too faint"
    elif best["fit"] > 0.10:
        conf, why = "low", "proportions match no film format"
    elif soft_sides or n_border == 1 or (real_covs and min(real_covs) < 0.5):
        conf, why = "medium", ""
    else:
        conf, why = "high", ""

    # Deskewed pixel box -> normalised box in the unrotated image + angle.
    # getRotationMatrix2D(ang) maps original -> deskewed (x_d = A x_o + t);
    # apply_crop_to_image samples C + R(angle) q, and R(ang) = A^-1.
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
    A, t = m[:, :2], m[:, 2]
    cd = np.array([(x0 + x1) / 2.0, (y0 + y1) / 2.0])
    co = np.linalg.solve(A, cd - t)
    bw, bh = float(x1 - x0), float(y1 - y0)
    rect_n = ((co[0] - bw / 2) / w, (co[1] - bh / 2) / h,
              (co[0] + bw / 2) / w, (co[1] + bh / 2) / h)
    return FrameCrop(tuple(float(v) for v in rect_n), angle=float(ang),
                     confidence=conf, soft=soft_sides, strip=strip,
                     fmt=best["fmt"], reason=why, debug=debug)
