"""Dust heal by patch synthesis (spec/dust-patch-fill.md).

Two steps, both decided on the buffer being rendered, at its own resolution:

1. **The speck's own pixels.** A brushed or found spot is an AREA that holds
   a defect; only the defect is replaced. `speck_mask` finds it inside the
   area: a morphological top-hat (an opening for a speck brighter than its
   surroundings, a closing for a darker one) at the smallest size that holds
   the whole speck, measured against the grain's own noise round the area,
   seeded where it stands well clear of the grain and grown along what
   touches it, then widened a little for its soft edge. Nothing standing out
   = the whole area is replaced, as before.

2. **Patch synthesis.** The hole is rebuilt from many small patches of the
   picture round about (PatchMatch: Barnes et al. 2009; completion by
   iterated nearest patches and voting: Wexler et al. 2007), coarse to fine.
   Each patch is matched against what surrounds the hole, so edges and lines
   carry on through it; on the finest level every hole pixel is COPIED from
   one source pixel, never averaged, so the grain in the hole is real grain.
   The distances are taken on sqrt-encoded display values; the pixels copied
   are the buffer's own 16-bit codes (headroom kept).

The old heal copied one disc from one place, which left discs where it
crossed an edge (the user's frames, 2026-11-23).
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Tuple

import cv2
import numpy as np

PATCH = 7                   # patch side (px)
_HP = PATCH // 2
ITERS_COARSE = 6            # PatchMatch iterations on the coarsest level
ITERS_FINE = 4              # ... on every finer level
HOLE_WEIGHT = 0.25          # a hole pixel's guess in the patch distance, against 1 for the picture
CENTRE_ITERS = 1            # last iterations of the finest level that copy, not average
SEED_Z = 7.0                # a speck stands this far clear of the grain somewhere (clean grain: p99 5.7) ...
GROW_Z = 2.0                # ... and is everything joined to it this far clear (inside the area)
GROW_Z_OUT = 3.0            # ... or this far clear just outside it (a fibre running out of the area)
GROW_REACH = 4              # ... up to this x the speck's size past the area
GROW_SHARE = 0.25           # ... and, for a speck standing far out, above this share of its peak
GROW_SHARE_OUT = 0.35       # ... (outside the area)
OTHER_POLARITY = 1.5        # the unexpected polarity wins only when this much stronger
SIZES = (5, 7, 9, 13, 17, 23, 31, 41, 55)   # top-hat sizes tried (px; 3 measures single grains)
SIZE_HOLD = 0.8             # the speck's size holds this share of what a size two steps up finds
EDGE_SHARE = 0.2            # the hole is widened by this x the speck's size (its soft edge)
PAD_MIN, PAD_MAX = 28, 160  # source area round a hole (px)
SMALL_R = 4.5               # a spot this narrow (px) at the size rendered is replaced whole
FOUND_KINDS = ("ir", "auto", "mark")   # spots a finder placed (the rest were painted)
THIN_R = 2.5                # a hole no wider than twice this (px) is diffused (+ grain), not rebuilt
GRAIN_SIGMA = 1.0           # px: the grain is what a blur this wide takes out


# --------------------------------------------------------------------------- #
# what a spot expects
# --------------------------------------------------------------------------- #
def polarity_of(spot: dict) -> int:
    """+1 = the defect is brighter than the picture round it (dust on a
    negative prints white), -1 = darker (a mark that prints dark), 0 = either
    (a brush stroke: whichever stands out more)."""
    kind = spot.get("kind")
    if kind == "mark":
        return -1 if spot.get("mk_k") == "light" else 1
    if kind in ("ir", "auto"):
        return 1
    return 0


def spot_region(spot: dict, h: int, w: int) -> Optional[Tuple[np.ndarray, int, int]]:
    """(mask, y0, x0): the spot's area at this resolution, with exactly
    rasterize_dust_mask's geometry, in its own box. None when empty."""
    r_px = max(1, int(round(float(spot.get("r", 0.0)) * w)))
    pts = []
    for p in (spot.get("pts") or []):
        try:
            pts.append((int(round(float(p[0]) * w)), int(round(float(p[1]) * h))))
        except (TypeError, ValueError, IndexError):
            continue
    if not pts:
        return None
    x0 = max(0, min(p[0] for p in pts) - r_px - 1)
    x1 = min(w, max(p[0] for p in pts) + r_px + 2)
    y0 = max(0, min(p[1] for p in pts) - r_px - 1)
    y1 = min(h, max(p[1] for p in pts) + r_px + 2)
    if x1 <= x0 or y1 <= y0:
        return None
    m = np.zeros((y1 - y0, x1 - x0), np.uint8)
    prev = None
    for (x, y) in pts:
        q = (x - x0, y - y0)
        cv2.circle(m, q, r_px, 1, -1)
        if prev is not None:
            cv2.line(m, prev, q, 1, thickness=2 * r_px)
        prev = q
    if not m.any():
        return None
    return m > 0, y0, x0


# --------------------------------------------------------------------------- #
# 1. the speck's own pixels
# --------------------------------------------------------------------------- #
def _disc(d: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))


def speck_mask(Y: np.ndarray, region: np.ndarray, polarity: int = 0,
               avoid: Optional[np.ndarray] = None):
    """The defect's pixels in `region` (bool, same shape as Y), as a bool
    mask over Y's box, or None when nothing in the region stands out of the
    grain (the caller then replaces the whole region). `Y` is a float
    luminance on a perceptual scale. `avoid` (bool) are pixels that are no
    part of the picture's noise estimate (other dust, outside the crop).
    Returns (mask or None, info)."""
    region = region.astype(bool)
    n_reg = int(region.sum())
    if n_reg == 0:
        return None, {}
    r_eq = float(np.sqrt(n_reg / np.pi))
    sizes = [s for s in SIZES if s <= 2.0 * r_eq + 9] or [SIZES[0]]
    ring = (cv2.dilate(region.astype(np.uint8), _disc(4)) > 0) & ~region
    if avoid is not None:
        ring &= ~avoid
    if ring.sum() < 12:
        ring = ~region
        if avoid is not None:
            ring &= ~avoid
    if ring.sum() < 12:
        return None, {}
    best = None
    for pol in ((1, -1) if polarity == 0 else (polarity, -polarity)):
        zs, ths = [], []
        for s in sizes + [s2 for s2 in SIZES if s2 > sizes[-1]][:2]:
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (s, s))
            if pol > 0:
                th = Y - cv2.morphologyEx(Y, cv2.MORPH_OPEN, se)
            else:
                th = cv2.morphologyEx(Y, cv2.MORPH_CLOSE, se) - Y
            th = cv2.GaussianBlur(th, (0, 0), 0.8)
            # how far it stands out of what this size finds in the grain
            # round the area (a larger top-hat gathers more of the grain)
            v = th[ring]
            med = float(np.median(v))
            spread = 1.4826 * float(np.median(np.abs(v - med))) + 1e-6
            ths.append(th)
            zs.append((th - med) / spread)
        # the speck's size: the smallest top-hat that already holds
        # SIZE_HOLD of what one two sizes larger finds at its own brightest
        # point (larger sizes go on gathering the picture round the speck);
        # sizes at which nothing stands out of the grain yet don't count
        # (their brightest point is a grain peak, not the speck)
        k = None
        for i in range(len(sizes)):
            t = np.where(region, zs[i], -np.inf)
            c = np.unravel_index(int(np.argmax(t)), t.shape)
            if zs[i][c] < SEED_Z:
                continue
            if ths[i][c] >= SIZE_HOLD * ths[min(i + 2, len(ths) - 1)][c]:
                k = i
                break
        if k is None:
            k = int(np.argmax([float(zz[region].max()) for zz in zs[:len(sizes)]]))
        z = zs[k]
        zmax = float(z[region].max())
        weight = 1.0 if (polarity == 0 or pol == polarity) else 1.0 / OTHER_POLARITY
        if best is None or zmax * weight > best[0]:
            best = (zmax * weight, pol, sizes[k], z, zmax)
    _, pol, s, z, zmax = best
    info = {"z": round(zmax, 2), "polarity": pol, "size": s}
    if zmax < SEED_Z:
        return None, info
    seeds = (z > SEED_Z) & region
    near = cv2.dilate(region.astype(np.uint8), _disc(max(3, GROW_REACH * s))) > 0
    # relative to how far the speck stands out: a bright speck is followed
    # only as far as it is clearly brighter, so a soft glow round it in the
    # picture is not taken with it
    g_in = max(GROW_Z, GROW_SHARE * zmax)
    g_out = max(GROW_Z_OUT, GROW_SHARE_OUT * zmax)
    grow = ((z > g_in) & region) | ((z > g_out) & near)
    if avoid is not None:
        grow &= ~avoid | region
    n, lab = cv2.connectedComponents(grow.astype(np.uint8), connectivity=8)
    keep = np.unique(lab[seeds])
    keep = keep[keep > 0]
    m = np.isin(lab, keep)
    out_ = m & ~region
    if int(out_.sum()) > n_reg:
        # running on further than the area itself is picture joined to
        # the speck (a bright line it lies on), not more of the speck
        m &= region
        info["held"] = True
    d = max(1, int(round(EDGE_SHARE * s)))
    m = cv2.dilate(m.astype(np.uint8), _disc(d)) > 0
    info["edge"] = d
    return m, info


# --------------------------------------------------------------------------- #
# 2. patch synthesis
# --------------------------------------------------------------------------- #
_OFFS = np.arange(-_HP, _HP + 1)


def _patches(img: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    yy = ys[:, None, None] + _OFFS[None, :, None]
    xx = xs[:, None, None] + _OFFS[None, None, :]
    return img[yy, xx].reshape(len(ys), -1)


def _dist(img, ty, tx, sy, sx) -> np.ndarray:
    d = _patches(img, ty, tx) - _patches(img, sy, sx)
    return np.einsum("ij,ij->i", d, d) / d.shape[1]


def _targets(hole: np.ndarray):
    t = cv2.dilate(hole.astype(np.uint8), np.ones((PATCH, PATCH), np.uint8)) > 0
    t[:_HP] = False
    t[-_HP:] = False
    t[:, :_HP] = False
    t[:, -_HP:] = False
    return np.nonzero(t)


def _try(img, A, Wt, sy, sx, d, cy, cx, valid_c, sel=None):
    """Keep the candidate (cy, cx) where it is a valid source and closer.
    A = the targets' own patches (fixed within an iteration), Wt their
    pixels' weights (the hole's guesses count less than the picture)."""
    H, W = valid_c.shape
    inb = (cy >= 0) & (cy < H) & (cx >= 0) & (cx < W)
    cy = np.clip(cy, 0, H - 1)
    cx = np.clip(cx, 0, W - 1)
    good = inb & valid_c[cy, cx]
    if sel is not None:
        good &= sel
    g = np.nonzero(good)[0]
    if not len(g):
        return
    diff = A[g] - _patches(img, cy[g], cx[g])
    dc = np.einsum("ij,ij,ij->i", diff, diff, Wt[g])
    b = dc < d[g]
    gi = g[b]
    sy[gi], sx[gi], d[gi] = cy[g][b], cx[g][b], dc[b]


def _vote(img, hole, ty, tx, sy, sx, d, centre_only):
    H, W, C = img.shape
    out = img.copy()
    if centre_only:
        h = hole[ty, tx]
        out[ty[h], tx[h]] = img[sy[h], sx[h]]
        return out
    sig = max(float(np.percentile(d, 75)), 1e-10)
    wgt = np.exp(-d / (2.0 * sig))
    acc = np.zeros((H * W, C), np.float64)
    wsum = np.zeros(H * W, np.float64)
    flat = img.reshape(-1, C)
    for oy in _OFFS:
        for ox in _OFFS:
            yy, xx = ty + oy, tx + ox
            inh = hole[yy, xx]
            if not inh.any():
                continue
            dst = yy[inh] * W + xx[inh]
            src = (sy[inh] + oy) * W + (sx[inh] + ox)
            ww = wgt[inh]
            wsum += np.bincount(dst, ww, H * W)
            for c in range(C):
                acc[:, c] += np.bincount(dst, flat[src, c] * ww, H * W)
    m = hole.reshape(-1) & (wsum > 0)
    o = out.reshape(-1, C)
    o[m] = (acc[m] / wsum[m, None]).astype(np.float32)
    return out


def _patchmatch(img, hole, valid_c, sy, sx, iters, rng, last):
    """Iterate PatchMatch + voting on one level. Targets are every patch
    touching the hole. Returns (img, (ty, tx), (sy, sx))."""
    H, W = hole.shape
    C = img.shape[2]
    ty, tx = _targets(hole)
    index = -np.ones((H, W), np.int64)
    index[ty, tx] = np.arange(len(ty))
    # the distance weighs the picture round the hole fully and the hole's
    # own current guess at HOLE_WEIGHT (a guess pulls matches towards
    # itself: a hole half as wide as a patch drifted to the wrong colour)
    wimg = np.where(hole, np.float32(HOLE_WEIGHT), np.float32(1.0))
    Wt = np.repeat(_patches(wimg[..., None], ty, tx), C, axis=1)
    Wt /= Wt.sum(1, keepdims=True)

    def dist(A, sy_, sx_):
        diff = A - _patches(img, sy_, sx_)
        return np.einsum("ij,ij,ij->i", diff, diff, Wt)

    A = _patches(img, ty, tx)
    d = dist(A, sy, sx)
    for it in range(iters):
        order = ((0, 1), (1, 0)) if it % 2 == 0 else ((0, -1), (-1, 0))
        for dy, dx in order + tuple((-a, -b) for a, b in order):
            ny, nx = ty - dy, tx - dx          # the neighbour this one learns from
            ok = (ny >= 0) & (ny < H) & (nx >= 0) & (nx < W)
            j = np.where(ok, index[np.clip(ny, 0, H - 1), np.clip(nx, 0, W - 1)], -1)
            has = j >= 0
            jj = np.maximum(j, 0)
            _try(img, A, Wt, sy, sx, d, sy[jj] + dy, sx[jj] + dx, valid_c, has)
        r = max(H, W)
        while r >= 1:
            cy = sy + rng.integers(-r, r + 1, len(ty))
            cx = sx + rng.integers(-r, r + 1, len(ty))
            _try(img, A, Wt, sy, sx, d, cy, cx, valid_c)
            r //= 2
        img = _vote(img, hole, ty, tx, sy, sx, d, last and it >= iters - CENTRE_ITERS)
        A = _patches(img, ty, tx)
        d = dist(A, sy, sx)
    return img, (ty, tx), (sy, sx)


def _valid_centres(valid: np.ndarray) -> np.ndarray:
    v = cv2.erode(valid.astype(np.uint8), np.ones((PATCH, PATCH), np.uint8),
                  borderType=cv2.BORDER_CONSTANT, borderValue=0)
    return v > 0


def _diffuse(img: np.ndarray, hole: np.ndarray) -> np.ndarray:
    out = img.copy()
    m = hole.astype(np.uint8)
    for c in range(img.shape[2]):
        out[..., c] = cv2.inpaint(np.ascontiguousarray(img[..., c]), m, 3, cv2.INPAINT_TELEA)
    return out


def synthesize(E: np.ndarray, hole: np.ndarray, valid: np.ndarray,
               seed: int = 0):
    """Fill `hole` in the float image E (H, W, C) from the `valid` pixels.
    Returns (src_y, src_x): for every hole pixel (in np.nonzero(hole) order)
    the pixel of E it was copied from, or None when there is no valid
    source patch at all."""
    rng = np.random.default_rng(seed)
    hole = hole.astype(bool)
    valid = valid.astype(bool) & ~hole
    hy, hx = np.nonzero(hole)
    if not len(hy):
        return np.zeros(0, int), np.zeros(0, int)
    span = max(int(hy.max() - hy.min()), int(hx.max() - hx.min())) + 1
    thick = max(1.0, len(hy) / span)
    levels = 1
    while (thick / 2 ** levels > 1.5
           and min(E.shape[:2]) / 2 ** levels > 4 * PATCH):
        levels += 1
    pyr = [(E, hole, valid)]
    for _ in range(levels - 1):
        im, ho, va = pyr[-1]
        h2, w2 = (im.shape[0] + 1) // 2, (im.shape[1] + 1) // 2
        im2 = cv2.resize(im, (w2, h2), interpolation=cv2.INTER_AREA)
        if im2.ndim == 2:
            im2 = im2[..., None]
        ho2 = cv2.resize(ho.astype(np.float32), (w2, h2), interpolation=cv2.INTER_AREA) > 0
        va2 = cv2.resize(va.astype(np.float32), (w2, h2), interpolation=cv2.INTER_AREA) > 0.999
        pyr.append((im2, ho2, va2 & ~ho2))
    pad = _HP
    cur = None
    prev = None                         # (shape, ty, tx, sy, sx) of the coarser level
    src = None
    for li in range(len(pyr) - 1, -1, -1):
        im, ho, va = pyr[li]
        imp = cv2.copyMakeBorder(im, pad, pad, pad, pad, cv2.BORDER_REFLECT)
        if imp.ndim == 2:
            imp = imp[..., None]
        hop = np.pad(ho, pad)
        vc = _valid_centres(np.pad(va, pad))
        vy, vx = np.nonzero(vc)
        if not len(vy):
            return None
        if cur is None:
            imp = np.where(hop[..., None], _diffuse(imp, hop), imp)
        else:
            up = cv2.resize(cur, (im.shape[1], im.shape[0]), interpolation=cv2.INTER_LINEAR)
            if up.ndim == 2:
                up = up[..., None]
            upp = cv2.copyMakeBorder(up, pad, pad, pad, pad, cv2.BORDER_REFLECT)
            if upp.ndim == 2:
                upp = upp[..., None]
            imp = np.where(hop[..., None], upp, imp)
        ty, tx = _targets(hop)
        k = rng.integers(0, len(vy), len(ty))
        sy, sx = vy[k].copy(), vx[k].copy()
        if prev is not None:
            pshape, pty, ptx, psy, psx = prev
            oy = np.zeros(pshape, np.int64)
            ox = np.zeros(pshape, np.int64)
            has = np.zeros(pshape, bool)
            oy[pty, ptx], ox[pty, ptx], has[pty, ptx] = psy - pty, psx - ptx, True
            py = np.clip((ty - pad) // 2 + pad, 0, pshape[0] - 1)
            px = np.clip((tx - pad) // 2 + pad, 0, pshape[1] - 1)
            cy = ty + 2 * oy[py, px]
            cx = tx + 2 * ox[py, px]
            ok = has[py, px] & (cy >= 0) & (cy < hop.shape[0]) & (cx >= 0) & (cx < hop.shape[1])
            cy, cx = np.clip(cy, 0, hop.shape[0] - 1), np.clip(cx, 0, hop.shape[1] - 1)
            ok &= vc[cy, cx]
            sy = np.where(ok, cy, sy)
            sx = np.where(ok, cx, sx)
        it = ITERS_COARSE if li == len(pyr) - 1 else ITERS_FINE
        out, (ty, tx), (sy, sx) = _patchmatch(imp.astype(np.float32), hop, vc, sy, sx,
                                              it, rng, last=(li == 0))
        prev = (hop.shape, ty, tx, sy, sx)
        cur = out[pad:-pad, pad:-pad]
        if li == 0:
            srcy = -np.ones(hop.shape, np.int64)
            srcx = -np.ones(hop.shape, np.int64)
            srcy[ty, tx], srcx[ty, tx] = sy - pad, sx - pad
            src = (srcy[pad:-pad, pad:-pad][hole], srcx[pad:-pad, pad:-pad][hole])
    return src


SEAM_SIGMA = 1.5            # px: the boundary is compared on this smoothing (grain cancels)


def _seam_correction(box16: np.ndarray, hole: np.ndarray, valid: np.ndarray,
                     sy: np.ndarray, sx: np.ndarray) -> np.ndarray:
    """A smooth offset for the copied pixels so the fill meets the picture
    round the hole without a step (the healing brush's idea): along the hole's
    edge, the picture just outside is compared with what lies just outside
    each copied pixel's source, on smoothed values; the differences are
    smoothed along the edge and carried into the hole by diffusion. Returns
    (n_hole, C) float32 in the buffer's codes."""
    H, W, C = box16.shape
    img = box16.astype(np.float32)
    v = valid.astype(np.float32)
    g = lambda a: cv2.GaussianBlur(a, (0, 0), SEAM_SIGMA)
    den = g(v)
    blur = np.stack([g(img[..., c] * v) for c in range(C)], 2) / np.maximum(den, 1e-6)[..., None]
    srcy = np.zeros((H, W), np.int64)
    srcx = np.zeros((H, W), np.int64)
    hy, hx = np.nonzero(hole)
    srcy[hy, hx], srcx[hy, hx] = sy, sx
    acc = np.zeros((H, W, C), np.float32)
    cnt = np.zeros((H, W), np.float32)
    for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        qy, qx = hy + dy, hx + dx
        ok = (qy >= 0) & (qy < H) & (qx >= 0) & (qx < W)
        qy, qx, py, px = qy[ok], qx[ok], hy[ok], hx[ok]
        out_ = valid[qy, qx] & (den[qy, qx] > 0.2)
        qy, qx, py, px = qy[out_], qx[out_], py[out_], px[out_]
        ry, rx = srcy[py, px] + dy, srcx[py, px] + dx
        ok2 = (ry >= 0) & (ry < H) & (rx >= 0) & (rx < W)
        qy, qx, ry, rx = qy[ok2], qx[ok2], ry[ok2], rx[ok2]
        ok3 = den[ry, rx] > 0.2
        qy, qx, ry, rx = qy[ok3], qx[ok3], ry[ok3], rx[ok3]
        np.add.at(acc, (qy, qx), blur[qy, qx] - blur[ry, rx])
        np.add.at(cnt, (qy, qx), 1.0)
    edge = cnt > 0
    if not edge.any():
        return np.zeros((len(hy), C), np.float32)
    ge = lambda a: cv2.GaussianBlur(a, (0, 0), 2.0)
    w_e = ge(cnt)
    m = np.stack([ge(acc[..., c]) for c in range(C)], 2) / np.maximum(w_e, 1e-6)[..., None]
    have = w_e > 0.05
    ys, xs = np.nonzero(hole)
    y0, y1 = max(0, ys.min() - 8), min(H, ys.max() + 9)
    x0, x1 = max(0, xs.min() - 8), min(W, xs.max() + 9)
    fillmask = (hole | ~have)[y0:y1, x0:x1].astype(np.uint8)
    corr = np.zeros((len(hy), C), np.float32)
    for c in range(C):
        known = np.where(have, m[..., c], 0.0).astype(np.float32)[y0:y1, x0:x1]
        filled = cv2.inpaint(np.ascontiguousarray(known), fillmask, 5, cv2.INPAINT_TELEA)
        corr[:, c] = filled[hy - y0, hx - x0]
    return corr


def _diffuse_with_grain(box16: np.ndarray, hole: np.ndarray,
                        valid: np.ndarray) -> np.ndarray:
    """A thin hole filled by diffusion (Telea, at the buffer's 16 bits),
    with the grain of a clean stretch beside it added back: the fine
    detail (what a GRAIN_SIGMA blur removes) of the same shape moved just
    clear of the hole, in the direction where the picture round it looks
    most alike. Returns the whole box (only the hole is new)."""
    H, W, C = box16.shape
    m8 = hole.astype(np.uint8)
    out = np.empty((H, W, C), np.float32)
    for c in range(C):
        out[..., c] = cv2.inpaint(np.ascontiguousarray(box16[..., c]), m8, 3,
                                  cv2.INPAINT_TELEA).astype(np.float32)
    img = box16.astype(np.float32)
    v = valid.astype(np.float32)
    g = lambda a: cv2.GaussianBlur(a, (0, 0), GRAIN_SIGMA)
    den = np.maximum(g(v), 1e-6)
    hp = np.stack([img[..., c] - g(img[..., c] * v) / den for c in range(C)], 2)
    hy, hx = np.nonzero(hole)
    depth = float(cv2.distanceTransform(np.pad(m8, 1), cv2.DIST_L2, 5).max())
    step = int(np.ceil(2 * depth)) + 3
    best = None
    for a in range(8):
        dy = int(round(step * np.sin(a * np.pi / 4)))
        dx = int(round(step * np.cos(a * np.pi / 4)))
        sy, sx = hy + dy, hx + dx
        if sy.min() < 0 or sx.min() < 0 or sy.max() >= H or sx.max() >= W:
            continue
        if not valid[sy, sx].all():
            continue
        # how alike: the diffused fill against the picture there, smoothed
        diff = float(np.abs(out[hy, hx] - g(img)[sy, sx]).mean())
        if best is None or diff < best[0]:
            best = (diff, sy, sx)
    if best is not None:
        out[hy, hx] += hp[best[1], best[2]]
    return np.clip(np.rint(out), 0, 65535).astype(np.uint16)


# --------------------------------------------------------------------------- #
# the heal of a buffer
# --------------------------------------------------------------------------- #
def _seed(*vals) -> int:
    h = hashlib.blake2b(repr(vals).encode(), digest_size=8).digest()
    return int.from_bytes(h, "little")


def heal(img16: np.ndarray, spots, keep: Optional[np.ndarray] = None,
         dewindow=None, info: Optional[list] = None) -> np.ndarray:
    """Heal `spots` out of img16 (uint16, H x W x C or H x W). `keep`
    (uint8/bool H x W, optional) = the pixels inside the crop: nothing is
    sampled from outside it. `dewindow(v)` maps the buffer's codes to
    display-referred 0..65535 (a windowed working-space base); identity when
    None. Pixels outside the holes come back exactly. `info` (a list)
    receives one dict per spot (its hole size and how it was found)."""
    if not spots:
        return img16
    mono = img16.ndim == 2
    src16 = img16[..., None] if mono else img16
    h, w = src16.shape[:2]

    def enc(v):
        v = v.astype(np.float32)
        if dewindow is not None:
            v = dewindow(v)
        return np.sqrt(np.maximum(v, 0.0) / 65535.0).astype(np.float32)

    regions = []
    for s in spots:
        regions.append(spot_region(s, h, w))
    area_all = np.zeros((h, w), bool)
    for rg in regions:
        if rg is not None:
            m, y0, x0 = rg
            area_all[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] |= m
    outside = None if keep is None else ~(keep.astype(bool))
    holes = np.zeros((h, w), bool)
    for s, rg in zip(spots, regions):
        if rg is None:
            if info is not None:
                info.append({"hole": 0})
            continue
        m, y0, x0 = rg
        g = 4 + 2 * max(m.shape)                 # room for the noise ring and growth
        by0, bx0 = max(0, y0 - g), max(0, x0 - g)
        by1, bx1 = min(h, y0 + m.shape[0] + g), min(w, x0 + m.shape[1] + g)
        reg = np.zeros((by1 - by0, bx1 - bx0), bool)
        reg[y0 - by0:y0 - by0 + m.shape[0], x0 - bx0:x0 - bx0 + m.shape[1]] = m
        if float(s.get("r", 0.0)) * w <= SMALL_R:
            # a few pixels across at this size (the preview): too small to
            # trace a speck in (tracing it there took the picture round it
            # too), so the area goes as painted or found
            hole, sinfo = reg, {"small": True}
        else:
            Y = enc(src16[by0:by1, bx0:bx1]).mean(axis=2)
            avoid = area_all[by0:by1, bx0:bx1] & ~reg
            if outside is not None:
                avoid = avoid | outside[by0:by1, bx0:bx1]
            speck, sinfo = speck_mask(Y, reg, polarity_of(s), avoid)
            if speck is None:
                # nothing in it stands out of the grain: the area is
                # replaced as painted or found
                hole = reg
                sinfo["whole_area"] = True
            elif s.get("kind") in FOUND_KINDS:
                # a finder's area is a generous guess round the speck:
                # only the speck goes
                hole = speck
            else:
                # painted by hand: what was painted goes (as with any
                # inpainting brush; "Fit each click to the speck" sizes a
                # click to its soft edge), and the speck where it runs on
                hole = reg | speck
        holes[by0:by1, bx0:bx1] |= hole
        if info is not None:
            sinfo["hole"] = int(hole.sum())
            info.append(sinfo)
    if not holes.any():
        return img16
    out = src16.copy()
    invalid = holes | (outside if outside is not None else False)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(
        cv2.dilate(holes.astype(np.uint8), np.ones((3, 3), np.uint8)), connectivity=8)
    for i in range(1, n):
        x, y, cw, ch = (int(v) for v in stats[i, :4])
        pad = int(np.clip(2 * min(cw, ch) + 4 * PATCH, PAD_MIN, PAD_MAX))
        by0, bx0 = max(0, y - pad), max(0, x - pad)
        by1, bx1 = min(h, y + ch + pad), min(w, x + cw + pad)
        hole = holes[by0:by1, bx0:bx1] & (lab[by0:by1, bx0:bx1] == i)
        if not hole.any():
            continue
        valid = ~invalid[by0:by1, bx0:bx1]
        hy, hx = np.nonzero(hole)
        inscribed = float(cv2.distanceTransform(
            np.pad(hole, 1).astype(np.uint8), cv2.DIST_L2, 5).max())
        src = None
        if inscribed > THIN_R:
            E = enc(src16[by0:by1, bx0:bx1])
            src = synthesize(E, hole, valid, seed=_seed(round(y / h, 4), round(x / w, 4), cw, ch))
        if src is None:
            # a hole a few pixels wide (a speck at the preview's size, a
            # thin fibre): diffusion carries the edges across it cleanly
            # and the grain is taken from beside it; or nowhere to sample
            # from at all
            box16 = src16[by0:by1, bx0:bx1]
            out[by0 + hy, bx0 + hx] = _diffuse_with_grain(box16, hole, valid)[hy, hx]
            continue
        sy, sx = src
        fill = src16[by0 + sy, bx0 + sx].astype(np.float32)
        fill += _seam_correction(src16[by0:by1, bx0:bx1], hole, valid, sy, sx)
        out[by0 + hy, bx0 + hx] = np.clip(np.rint(fill), 0, 65535).astype(np.uint16)
    return out[..., 0] if mono else out
