"""Local Contrast: the Details list's methods (spec/local-contrast.md).

"edge" is the developer edge effect (core/edge_effect.py, on the base). The
other three run on the finished picture, on log luminance, and are applied as
one gain per pixel on linear RGB, so colour ratios are kept:

* "light"  Lighting & Detail: an edge-aware split (domain-transform
           recursive filter) into lighting and detail, each re-weighted.
* "llf"    Local Laplacian: the fast local Laplacian filter.
* "bands"  Detail Bands: Gaussian bands of texture, structure and shape.

Every method leaves the grain's own band (0.02 mm on the film) as it is,
limits any lift by the pixel's headroom (so nothing clips), and is an exact
no-op with its sliders at 0.
"""

import cv2
import numpy as np

from core import edge_effect as ee
from core.oklab import _lin, _enc, guided_smooth

METHODS = ("off", "edge", "light", "llf", "bands")
METHOD_NAMES = {"off": "Off", "edge": "Edge Effect", "light": "Lighting & Detail",
                "llf": "Local Laplacian", "bands": "Detail Bands"}
# each method's sliders (Edge Effect's is the edge_effect key)
METHOD_KEYS = {"off": (), "edge": ("edge_effect",),
               "light": ("lc_lighting", "lc_detail"),
               "llf": ("lc_lighting", "lc_detail"),
               "bands": ("lc_texture", "lc_structure", "lc_shape")}
LC_KEYS = ("lc_lighting", "lc_detail", "lc_texture", "lc_structure", "lc_shape")

Y_W = np.array([0.2126, 0.7152, 0.0722], np.float32)
LOG_FLOOR = -14.0          # log2 of the darkest luminance considered
GRAIN_MM = ee.GRAIN_MM     # the grain's size on the film: its band is left alone

# Lighting & Detail
LIGHT_SPREAD = 0.04        # the lighting's smoothing, share of the picture's long side
LIGHT_EDGE = 0.6           # a step this large (stops) is an edge of the lighting (1.0 left a halo by a dark tower: 0.013 vs 0.005)
LIGHT_ITERS = 3
LIGHT_ANCHOR_Q = 85        # the lighting turns about this percentile of itself
# Local Laplacian
LLF_SIGMA = 0.5            # steps smaller than this (stops) are detail
LLF_K = (8, 16)            # reference levels
LLF_TOP_PX = 2             # the pyramid runs down to about this (lighting lives there)
LLF_REACH = 4.0            # steps past this many sigmas are edges: left as they are
LLF_CORE_SCALE = 0.01      # the band read to decide where detail stands out from grain
CORE_SPREAD = 0.0025       # its energy is smoothed over this share of the long side
# Detail Bands: (key, inner, outer), inner/outer the smoothing radius as a share
# of the long side ("grain" = the grain's own size)
BANDS = (("lc_texture", "grain", 0.0025), ("lc_structure", 0.0025, 0.01),
         ("lc_shape", 0.01, 0.04))
BAND_LIMIT = {"lc_texture": 0.20, "lc_structure": 0.30, "lc_shape": 0.30}
BAND_EDGE = 0.35          # stops: steps much larger than this stay out of a band (0.5: +0.046 by a hard edge, 0.35: +0.025)


def normalize_method(m):
    return m if m in METHODS else None


def method_of(s):
    """The frame's method. Missing: "edge" when Edge Effect is set (frames
    edited before the list), else "off"."""
    s = s or {}
    m = normalize_method(s.get("local_contrast"))
    if m is not None:
        return m
    try:
        return "edge" if float(s.get("edge_effect", 0) or 0) > 0 else "off"
    except (TypeError, ValueError):
        return "off"


def active(s):
    """True when the frame's method would change the picture."""
    m = method_of(s)
    if m == "edge":
        return float((s or {}).get("edge_effect", 0) or 0) > 0
    return any(float((s or {}).get(k, 0) or 0) != 0 for k in METHOD_KEYS[m])


def lighting_factor(v):
    return float(2.0 ** (float(v) / 100.0))


def detail_factor(v):
    return float(2.0 ** (float(v) / 100.0))


# --------------------------------------------------------------------------- #
# shared pieces
# --------------------------------------------------------------------------- #

def _area(shape, crop):
    return ee._floor_area(shape, crop)


def _sample(x, area):
    r0, r1, c0, c1 = area
    step = max(1, int(max(r1 - r0, c1 - c0) // 512))
    return x[r0:r1:step, c0:c1:step]


def grain_band(l, sg):
    if sg < 0.3:
        return np.zeros_like(l)
    return l - ee._blur(l, sg)


CORE = 3.0                 # a band no stronger than this x its floor is grain: left alone
FLOOR_Q = 10               # the band's floor: this percentile of its energy in the picture


def core_weight(band, sigma_e, area):
    """How much a band stands out from its floor (the grain, in the frame's
    plainest parts): r²/(1 + r²), r = its energy smoothed over sigma_e / (CORE²
    x the floor). Decided on smoothed energy, so grain can't steer it; C¹."""
    E = ee._blur(band * band, max(float(sigma_e), 0.5))
    n2 = float(np.percentile(_sample(E, area), FLOOR_Q)) + 1e-12
    r = E * np.float32(1.0 / (CORE * CORE * n2))
    r *= r
    return (r / (np.float32(1.0) + r)).astype(np.float32)


def _rf_rows(F, V):
    """The recursive filter along axis 1, left to right then back, in place."""
    w = F.shape[1]
    for i in range(1, w):
        F[:, i] += V[:, i] * (F[:, i - 1] - F[:, i])
    for i in range(w - 2, -1, -1):
        F[:, i] += V[:, i + 1] * (F[:, i + 1] - F[:, i])
    return F


def edge_aware_smooth(I, sigma_s, sigma_r, iters=LIGHT_ITERS):
    """Domain-transform recursive filter (Gastal & Oliveira 2011) guided by I:
    smooth within regions, stopping at steps much larger than sigma_r."""
    I = np.ascontiguousarray(I, np.float32)
    k = np.float32(sigma_s / sigma_r)
    dHdx = np.ones_like(I)
    dHdx[:, 1:] += k * np.abs(np.diff(I, axis=1))
    dVdy = np.ones_like(I)
    dVdy[1:, :] += k * np.abs(np.diff(I, axis=0))
    dVdyT = np.ascontiguousarray(dVdy.T)
    F = I.copy()
    for i in range(iters):
        s = sigma_s * np.sqrt(3.0) * 2.0 ** (iters - (i + 1)) / np.sqrt(4.0 ** iters - 1)
        la = np.float32(-np.sqrt(2.0) / max(s, 1e-3))
        F = _rf_rows(F, np.exp(la * dHdx))
        FT = _rf_rows(np.ascontiguousarray(F.T), np.exp(la * dVdyT))
        F = np.ascontiguousarray(FT.T)
    return F


# --------------------------------------------------------------------------- #
# the methods, on log luminance
# --------------------------------------------------------------------------- #

def light_log(l, s, P, sg, area):
    c = lighting_factor(s.get("lc_lighting", 0) or 0)
    d = detail_factor(s.get("lc_detail", 0) or 0)
    base = edge_aware_smooth(l, max(LIGHT_SPREAD * P, 1.0), LIGHT_EDGE)
    fine = grain_band(l, sg)
    detail = l - base - fine
    w = core_weight(detail, CORE_SPREAD * P, area) if d != 1.0 else 0.0
    a = np.float32(np.percentile(_sample(base, area), LIGHT_ANCHOR_Q))
    return (a + np.float32(c) * (base - a) + (np.float32(1.0) + np.float32(d - 1.0) * w) * detail
            + fine).astype(np.float32)


def _gauss_pyr(x, n):
    G = [x]
    for _ in range(n - 1):
        G.append(cv2.pyrDown(G[-1]))
    return G


def _up(x, like):
    return cv2.pyrUp(x, dstsize=(like.shape[1], like.shape[0]))


def _lap_pyr(x, n):
    G = _gauss_pyr(x, n)
    return [G[k] - _up(G[k + 1], G[k]) for k in range(n - 1)] + [G[-1]]


def _collapse(L):
    x = L[-1]
    for k in range(len(L) - 2, -1, -1):
        x = _up(x, L[k]) + L[k]
    return x


def remap(i, g, c, d, sigma=LLF_SIGMA, k=LLF_REACH):
    """The local Laplacian's remapping about reference g: slope d for steps
    well under sigma (detail), slope c for large ones (lighting / edges), and
    back on the line g + c·Δ past about k·sigma, so a strong edge gets no
    extra step (no halo). The bump's slope (sech²(u) − sech²(u/k)/k)/(1 − 1/k)
    dips no lower than −0.2 for k = 4, so the slope stays between d and
    c − 0.2·(d − c) > 0 over every slider position: it never turns back."""
    D = i - np.float32(g)
    u = D * np.float32(1.0 / sigma)
    bump = (np.tanh(u) - np.tanh(u * np.float32(1.0 / k))) * np.float32(sigma / (1.0 - 1.0 / k))
    return np.float32(g) + np.float32(c) * D + (np.float32(d) - np.float32(c)) * bump


def llf_log(l, s, P, sg, area):
    c = lighting_factor(s.get("lc_lighting", 0) or 0)
    d = detail_factor(s.get("lc_detail", 0) or 0)
    h, w = l.shape
    n = max(2, int(np.floor(np.log2(max(min(h, w), 2) / LLF_TOP_PX))) + 1)
    G = _gauss_pyr(l, n)
    Lin = [G[k] - _up(G[k + 1], G[k]) for k in range(n - 1)]
    lo, hi = (float(v) for v in np.percentile(_sample(l, area), (0.1, 99.9)))
    if hi - lo < 1e-3:
        return l.copy()
    K = int(np.clip(np.ceil((hi - lo) / LLF_SIGMA) + 1, *LLF_K))
    refs = np.linspace(lo, hi, K).astype(np.float32)
    step = np.float32(refs[1] - refs[0])
    Gc = [np.clip(G[k], lo, hi) for k in range(n - 1)]
    Lout = [np.zeros_like(G[k]) for k in range(n - 1)]
    keep = [2.0 ** (k + 1) <= 2.0 * sg for k in range(n - 1)]   # the grain's levels
    if d != 1.0:
        # detail slope per pixel: 1 where the detail is no more than grain
        fine_l = l if sg < 0.3 else ee._blur(l, sg)
        w = core_weight(fine_l - ee._blur(l, LLF_CORE_SCALE * P), CORE_SPREAD * P, area)
        d = np.float32(1.0) + np.float32(d - 1.0) * w
    for g in refs:
        LR = _lap_pyr(remap(l, g, c, d), n)
        for k in range(n - 1):
            if keep[k]:
                continue
            wgt = np.maximum(np.float32(1.0) - np.abs(Gc[k] - g) / step, np.float32(0.0))
            Lout[k] += wgt * LR[k]
    for k in range(n - 1):
        if keep[k]:
            Lout[k] = Lin[k]
    # the coarsest level is the lighting at the frame's own size: it turns
    # about the same bright level as Lighting & Detail's, so the brightest
    # light is kept and the shadows open
    a = np.float32(np.percentile(_sample(l, area), LIGHT_ANCHOR_Q))
    top = a + np.float32(c) * (G[-1] - a)
    return _collapse(Lout + [top]).astype(np.float32)


def bands_log(l, s, P, sg, area):
    """Each band is the difference of two edge-aware smoothings (a guided
    filter guided by l itself, so a strong edge stays in the coarser one and
    throws no halo), boosted softly."""
    out = l.copy()
    smooth = {}

    def at(r):
        if r not in smooth:
            if r == "grain":
                smooth[r] = l if sg < 0.3 else ee._blur(l, sg)
            else:
                smooth[r] = guided_smooth(l, int(round(r * P)), BAND_EDGE * BAND_EDGE)
        return smooth[r]

    for key, inner, outer in BANDS:
        v = float(s.get(key, 0) or 0)
        if v == 0:
            continue
        band = at(inner) - at(outer)
        lim = np.float32(BAND_LIMIT[key])
        gain = np.float32(2.0 ** (v / 100.0) - 1.0)
        w = core_weight(band, 0.5 * outer * P, area)
        out += w * lim * np.tanh(gain * band / lim)
    return out.astype(np.float32)


LOGS = {"light": light_log, "llf": llf_log, "bands": bands_log}


# --------------------------------------------------------------------------- #
# the picture
# --------------------------------------------------------------------------- #

_LIN16 = None


def _lin16(img):
    """sRGB -> linear of a uint16 picture, by table (exactly oklab._lin)."""
    global _LIN16
    if _LIN16 is None:
        _LIN16 = _lin(np.arange(65536, dtype=np.float32) / np.float32(65535.0)).astype(np.float32)
    return _LIN16[img]


def limit_gain(g, top):
    """A lift is limited by the pixel's headroom h (stops until its brightest
    channel `top` reaches 1): h·tanh(g/h), slope 1 at no change, never past 1."""
    h = np.maximum(-np.log2(np.maximum(top, np.float32(2.0 ** LOG_FLOOR))),
                   np.float32(0.0)).astype(np.float32)
    pos = h * np.tanh(g / np.maximum(h, np.float32(1e-6)))
    return np.where(g > 0, pos, g).astype(np.float32)


def apply_local_contrast(img, s, px_mm, crop=None):
    """The chosen method on a finished picture (uint16 RGB display values,
    or float 0..1). Returns `img` itself when nothing would change."""
    s = s or {}
    m = method_of(s)
    if m not in LOGS or not active(s) or img is None or img.ndim != 3 or img.shape[2] < 3:
        return img
    rgb = img[..., :3]
    if img.dtype == np.uint16:
        scale = 65535.0
        lin = _lin16(rgb)
    else:
        scale = 255.0 if img.dtype == np.uint8 else 1.0
        lin = _lin(rgb.astype(np.float32) / np.float32(scale)).astype(np.float32)
    R, G, B = lin[..., 0], lin[..., 1], lin[..., 2]
    Y = R * Y_W[0] + G * Y_W[1] + B * Y_W[2]
    l = np.log2(np.maximum(Y, np.float32(2.0 ** LOG_FLOOR))).astype(np.float32)
    top = np.maximum(np.maximum(R, G), B)
    P = ee.px_per_mm(img.shape, 1.0, crop)       # the picture's long side, px
    sg = GRAIN_MM * float(px_mm)
    area = _area(img.shape, crop)
    l2 = LOGS[m](l, s, P, sg, area)
    g = limit_gain((l2 - l).astype(np.float32), top)
    lin *= np.exp2(g)[..., None]
    enc = _enc(lin)
    out = img.copy()
    if scale == 1.0:
        out[..., :3] = np.minimum(enc, np.float32(1.0)).astype(img.dtype)
    else:
        out[..., :3] = (np.minimum(enc, np.float32(1.0)) * np.float32(scale)
                        + np.float32(0.5)).astype(img.dtype)
    same = np.abs(g) < 1e-7
    if same.any():
        out[same] = img[same]
    return out
