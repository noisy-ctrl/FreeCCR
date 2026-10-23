"""Glow: a soft highlight glow, bright areas blooming gently into their
surroundings like a diffusion filter on the enlarger (spec/highlight-glow.md).

On display values (sRGB-encoded 0..1), float32 throughout, the light itself
handled in LINEAR light (glow is light adding up):

- sources: each pixel's linear RGB times a soft knee on its lightness (OKLab
  L of the decision view, so grain can't switch a pixel on and off): from
  the display level Start (default 95 %) up to white, so only the brightest
  tones give light, in their own colour (revision 3, 2026-10-23: it began at
  OKLab L 0.65, and skies and pale walls bloomed);
- spread: box-averaged down to a size where the narrow kernel is still >= 4
  px, blurred with two Gaussians (sigma and 3 sigma, 0.65 / 0.35: a core with a
  longer tail, like real diffusion) and scaled back up - a field this smooth
  scales up without steps. Reach (default 5 %, 2-10 %) is how far from a
  highlight's edge the glow fades out (twice the tail's sigma), as a share of
  the picture's long side, so the glow is the same at every render size;
- the brightest areas keep their own detail: they take little of their own
  glow (a wider ramp than the source knee, so a ramp stays monotone), read on
  a lightness smoothed along edges at half the glow's size, so a highlight's
  texture can't modulate its own glow (that flattened it);
- screened on in linear light, 1 - (1 - lin)(1 - g): never above 1, never
  darker, local slope (1 - g) <= 1 so grain is never stretched;
- pixels with g < 1e-6 are returned exactly;
- with a crop (`area`), only light INSIDE it glows: the sources are masked
  to the crop box and the spread normalised by the spread of the mask
  (normalised convolution), so the white holder or sprocket holes outside a
  crop can't bloom into the picture's edges, and the edges don't dim either.
  No crop: exactly as before."""
import cv2
import numpy as np

from core.oklab import _lin, _enc, _M1, _M2, guided_smooth, guided_filter, scaled_radius, \
    smooth, DENOISE_R, DENOISE_EPS

# Revision 3 (2026-10-23): new keys, so options saved under the old meaning
# ("size" 0..100, "start" OKLab L x 100) fall back to the defaults.
#   reach: how far the glow spreads, tenths of a percent of the long side
#   above: the display level (%) the glow starts from; full at white
GLOW_SHAPE_DEFAULT = {"reach": 50, "above": 95}
GLOW_LIMITS = {"reach": (20, 100), "above": (85, 99)}
GAIN = 0.6                # glow field x this at amount 100
CORE_W, TAIL_W, TAIL_X = 0.65, 0.35, 3.0
REACH_SIGMAS = 2.0 * TAIL_X   # reach = this many core sigmas (twice the tail's)
HOLD = 1.0                # how much a bright area keeps out of its own glow
HOLD_SPAN = 0.30          # ... ramping in from this far (OKLab L) below Start, full at white
HOLD_R = 0.5              # the hold's lightness smoothing radius, x sigma
HOLD_EPS = 0.10 ** 2      # ... edge-preserving: texture smooths, outlines stay
MIN_SIGMA_PX = 4.0        # the narrow kernel's sigma at the size it is blurred
G_EPS = 1e-6
MASK_EPS = 1e-3           # the mask's spread is floored here (outside the crop)


def normalize_glow_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default."""
    if not isinstance(shape, dict):
        return None
    out = {}
    for k, dflt in GLOW_SHAPE_DEFAULT.items():
        lo, hi = GLOW_LIMITS[k]
        try:
            out[k] = int(round(float(np.clip(float(shape.get(k, dflt)), lo, hi))))
        except (TypeError, ValueError):
            out[k] = dflt
    return None if out == GLOW_SHAPE_DEFAULT else out


def grey_lightness(level):
    """OKLab L of a grey at display level `level` (0..1): cbrt of its linear
    value (OKLab's matrices keep a grey grey)."""
    v = float(level)
    lin = v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return float(np.cbrt(lin))


def sigma_frac(shape):
    """Core-kernel sigma as a fraction of the long side for these options."""
    return shape["reach"] / 1000.0 / REACH_SIGMAS


def knees(shape):
    """(source start, source width, hold start, hold width) in OKLab L: the
    sources ramp from Start's lightness to white; the hold from HOLD_SPAN
    below Start to white (wider, so a ramp stays monotone)."""
    lo = grey_lightness(shape["above"] / 100.0)
    h_lo = lo - HOLD_SPAN
    return lo, 1.0 - lo, h_lo, 1.0 - h_lo


def _lightness(lin, chunk=1 << 20):
    """The decision view's lightness from LINEAR RGB (HxWx3): OKLab L,
    edge-preservingly denoised (chunked, so a 24 MP render needs no full
    OKLab copy)."""
    H, W = lin.shape[:2]
    flat = lin.reshape(-1, 3)
    L = np.empty(len(flat), np.float32)
    for s in range(0, len(flat), chunk):
        L[s:s + chunk] = np.cbrt(flat[s:s + chunk] @ _M1.T) @ _M2[0]
    return guided_smooth(L.reshape(H, W), scaled_radius(DENOISE_R, lin.shape),
                         DENOISE_EPS[0])


def _spread(b, sigma_px):
    """Two-Gaussian blur of the HxWx3 source map, on a reduced copy."""
    H, W = b.shape[:2]
    f = max(1, int(sigma_px // MIN_SIGMA_PX))
    if f > 1:
        small = cv2.resize(b, (max(1, round(W / f)), max(1, round(H / f))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = b
    s = sigma_px * small.shape[1] / float(W)
    core = cv2.GaussianBlur(small, (0, 0), s, borderType=cv2.BORDER_REFLECT)
    tail = cv2.GaussianBlur(small, (0, 0), s * TAIL_X, borderType=cv2.BORDER_REFLECT)
    g = CORE_W * core + TAIL_W * tail
    if f > 1:
        g = cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR)
    return np.maximum(g, 0.0)


def normalize_glow_area(area):
    """((x1, y1, x2, y2) normalised crop box, angle in degrees) or None when
    there is no crop, it is degenerate, or it is the whole unrotated frame."""
    if not area:
        return None
    try:
        rect, angle = area
        x1, y1, x2, y2 = (float(v) for v in rect)
        angle = float(angle or 0.0)
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((min(max(x1, 0.0), 1.0), min(max(x2, 0.0), 1.0)))
    y1, y2 = sorted((min(max(y1, 0.0), 1.0), min(max(y2, 0.0), 1.0)))
    if x2 - x1 <= 1e-6 or y2 - y1 <= 1e-6:
        return None
    if angle == 0.0 and x1 <= 0.0 and y1 <= 0.0 and x2 >= 1.0 and y2 >= 1.0:
        return None
    return (x1, y1, x2, y2), angle


def area_geometry(area, H, W):
    """The crop box in render pixels: centre, cos/sin of its angle, half
    sizes (the box apply_crop_to_image samples: C + R(angle) q)."""
    (x1, y1, x2, y2), angle = area
    t = np.radians(angle)
    return ((x1 + x2) / 2 * W, (y1 + y2) / 2 * H, float(np.cos(t)), float(np.sin(t)),
            (x2 - x1) * W / 2, (y2 - y1) * H / 2)


def crop_mask(area, H, W):
    """Coverage of each pixel by the crop box (0..1, a one-pixel soft edge),
    float32 HxW; analytic, so the GPU computes exactly the same."""
    cx, cy, c, s, hw, hh = area_geometry(area, H, W)
    dx = (np.arange(W, dtype=np.float32) + np.float32(0.5)) - np.float32(cx)
    dy = (np.arange(H, dtype=np.float32) + np.float32(0.5)) - np.float32(cy)
    qx = np.float32(c) * dx[None, :] + np.float32(s) * dy[:, None]
    qy = np.float32(-s) * dx[None, :] + np.float32(c) * dy[:, None]
    mx = np.clip(np.float32(hw) - np.abs(qx) + np.float32(0.5), 0.0, 1.0)
    my = np.clip(np.float32(hh) - np.abs(qy) + np.float32(0.5), 0.0, 1.0)
    return (mx * my).astype(np.float32)


def glow_field(img01, amount, shape, with_lin=False, area=None):
    """The linear-light glow g (HxWx3) to screen on (amount included), and,
    with `with_lin`, the picture's linear RGB computed on the way. With a
    crop `area`, only light inside it spreads (normalised at its edges)."""
    a = np.asarray(img01, np.float32)
    H, W = a.shape[:2]
    lin = _lin(a).astype(np.float32)
    L = _lightness(lin)
    lo, width, h_lo, h_w = knees(shape)
    src = smooth((L - lo) / width).astype(np.float32)
    b = lin * src[..., None]
    sigma_px = sigma_frac(shape) * max(H, W)
    area = normalize_glow_area(area)
    if area is None:
        g = _spread(np.ascontiguousarray(b), sigma_px)
    else:
        m = crop_mask(area, H, W)
        g = _spread(np.ascontiguousarray(b * m[..., None]), sigma_px)
        gm = _spread(m, sigma_px)
        g = g / np.maximum(gm, np.float32(MASK_EPS))[..., None]
    Lh = guided_filter(L, L, max(1, int(round(HOLD_R * sigma_px))), HOLD_EPS)
    hold = 1.0 - HOLD * smooth((Lh - h_lo) / h_w)
    k = np.float32(min(float(amount), 100.0) / 100.0 * GAIN) * hold.astype(np.float32)
    g = g.astype(np.float32, copy=False)
    g *= k[..., None]
    np.clip(g, 0.0, 1.0, out=g)
    return (g, lin) if with_lin else g


def _cmax(x):
    """Brightest channel of an HxWx3 array (faster than .max(axis=2))."""
    return np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2])


def _screen(lin, g):
    """Display value of lin screened with g, in linear light:
    1 - (1 - lin)(1 - g) = lin + g(1 - lin), encoded, in 0..1."""
    lit = 1.0 - lin
    lit *= g
    lit += lin
    out = _enc(lit)
    np.clip(out, 0.0, 1.0, out=out)
    return out


def weight_map(rgb01, shape=None):
    """0..1 map of where the glow lands (for the canvas): how much it lifts
    each pixel's display value at amount 100 (brightest channel), scaled so
    a lift of a quarter of the range reads full."""
    a = np.asarray(rgb01, np.float32)
    d = apply_glow(a, 100, shape) - a
    return np.clip(_cmax(d) * 4.0, 0.0, 1.0).astype(np.float32)


def apply_glow(img01, amount, shape=None, area=None):
    """Glow on an HxWx3 float image in 0..1. A new float32 array; the input
    itself at 0. `area`: the crop (normalize_glow_area), or None."""
    if not amount or amount <= 0:
        return img01
    shape = normalize_glow_shape(shape) or dict(GLOW_SHAPE_DEFAULT)
    a = np.asarray(img01, np.float32)
    g, lin = glow_field(a, amount, shape, with_lin=True, area=area)
    sel = _cmax(g) >= G_EPS
    n = int(np.count_nonzero(sel))
    if n == 0:
        return a.copy()
    if n > sel.size // 4:          # whole-array: faster than a big gather
        out = _screen(lin, g).astype(np.float32, copy=False)
        if n < sel.size:
            keep = ~sel
            out[keep] = a[keep]
        return out
    out = a.copy()
    out[sel] = _screen(lin[sel], g[sel])
    return out
