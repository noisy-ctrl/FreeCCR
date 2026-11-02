"""Measuring Colour Organics' hue curve from pairs of (your scan, the lab's
scan) of the same negatives (spec/colour-organics.md, revision 2026-11-02).

The pure part of tools/fit_organics.py: render each pair's density samples
the way FreeCCR's default render does (after fitting that frame's six Channel
Levels numbers to the lab scan, so exposure and balance are the lab's), take
the lab's HSV hue minus ours on coloured samples, bin it, and fit the
periodic Catmull-Rom curve the render uses."""
import numpy as np

from core import look_fit as lf

SAT_OURS_MIN = 0.15        # samples coloured in both renders
SAT_LAB_MIN = 0.10
VALUE_MIN = 0.35           # mid and bright tones only (the dark cells disagree
                           # between labs, so they would teach noise)
VALUE_BANDS = ((0.35, 0.55), (0.55, 0.75), (0.75, 1.01))
BIN_DEG = 10.0
CELL_MIN = 25              # samples a (hue, value) cell needs
# A cell's weight is its sample count, i.e. the area of picture it covers.
# Weighting cells alike (sqrt n, capped) moved bright skies 2.4 degrees that the
# labs leave alone, and predicted one lab from the other WORSE than no curve,
# area-weighted (4.15 against 2.69); by count it beats no curve on every check.
BALANCE_EPS = 3e-3         # the render is uint16: a finite difference needs a step


def default_render(d):
    """FreeCCR's default render of a black-point conversion for density
    samples `d` (N, 3): the windowed base through adjust_image with the
    negative brightness baseline, as display values in [0, 1]."""
    from core import ccr_processor as cp
    x = np.asarray(d, np.float32).reshape(-1, 1, 3).copy()
    out = cp.adjust_image(cp.encode_window(x), brightness=-8.0, ws_windowed=True)
    return out.reshape(-1, 3).astype(np.float64) / 65535.0


def balance_frame(d, t, render=default_render, n=3000, seed=0):
    """The frame's six Channel Levels numbers (offset, contrast per channel)
    that bring `render` of its density closest to the lab scan `t`."""
    rng = np.random.default_rng(seed)
    sel = rng.choice(len(d), min(n, len(d)), replace=False)
    ds, ts = d[sel], t[sel]
    return lf.lm(lambda q: (lf.frame_apply(render, ds, q) - ts).ravel(),
                 np.zeros(6), iters=40, eps=BALANCE_EPS)


def hsv(x):
    """HSV hue (degrees), saturation and value of display values (N, 3)."""
    x = np.clip(np.asarray(x, np.float64), 0, 1)
    mx, mn = x.max(1), x.min(1)
    c = mx - mn
    r, g, b = x[:, 0], x[:, 1], x[:, 2]
    cs = np.maximum(c, 1e-9)
    h = np.where(mx == r, 60 * np.mod((g - b) / cs, 6),
                 np.where(mx == g, 60 * ((b - r) / cs + 2), 60 * ((r - g) / cs + 4)))
    s = np.where(mx > 1e-6, c / np.maximum(mx, 1e-6), 0)
    return h, s, mx


def hue_moves(ours, lab):
    """(our hue, lab hue minus ours in -180..180, weight, our value) for the
    samples coloured in both renders."""
    ho, so, vo = hsv(ours)
    hl, sl, _vl = hsv(lab)
    dh = np.mod(hl - ho + 180.0, 360.0) - 180.0
    ok = (so > SAT_OURS_MIN) & (sl > SAT_LAB_MIN)
    return ho[ok], dh[ok], np.minimum(so, sl)[ok], vo[ok]


def _wmedian(v, w):
    o = np.argsort(v)
    cw = np.cumsum(w[o])
    return float(v[o][np.searchsorted(cw, cw[-1] / 2.0)])


def cells(h, dh, w, v, bands=VALUE_BANDS, step=BIN_DEG, min_n=CELL_MIN):
    """Weighted-median hue move per (hue bin, value band): rows of
    (bin centre, move, weight, count)."""
    rows = []
    for lo in np.arange(0.0, 360.0, step):
        inbin = (h >= lo) & (h < lo + step)
        for a, b in bands:
            m = inbin & (v >= a) & (v < b)
            n = int(m.sum())
            if n >= min_n:
                rows.append((lo + step / 2.0, _wmedian(dh[m], w[m]), float(n), n))
    return np.array(rows, np.float64).reshape(-1, 4)


def fit_curve(c, lam=1.0, lam0=0.3):
    """The curve's knots (degrees) from cells `c` (centre, move, weight, ...):
    weighted least squares in the render's own Catmull-Rom basis, a
    second-difference penalty round the circle (smoothness) and a pull to 0
    (a hue nobody measured gets no move)."""
    from core.ccr_processor import organics_cr_weights, ORGANICS_KNOTS
    K = ORGANICS_KNOTS
    idx, wts = organics_cr_weights(c[:, 0])
    B = np.zeros((len(c), K))
    for j in range(4):
        np.add.at(B, (np.arange(len(c)), idx[:, j]), wts[:, j])
    w, y = c[:, 2], c[:, 1]
    D = np.zeros((K, K))
    for i in range(K):
        D[i, (i - 1) % K] += 1.0
        D[i, i] -= 2.0
        D[i, (i + 1) % K] += 1.0
    scale = max(w.sum(), 1.0) / K
    A = B.T @ (w[:, None] * B) + lam * scale * D.T @ D + lam0 * scale * np.eye(K)
    return np.linalg.solve(A, B.T @ (w * y))


def curve_rms(knots, c, alike=False):
    """RMS (degrees) of a curve against cells, weighted by the cells' weights
    (area of picture) or, `alike`, every cell the same; zeros = no move."""
    from core.ccr_processor import organics_cr_weights
    idx, wts = organics_cr_weights(c[:, 0])
    p = (wts * np.asarray(knots, np.float64)[idx]).sum(1)
    w = np.ones(len(c)) if alike else c[:, 2]
    return float(np.sqrt(np.sum(w * (p - c[:, 1]) ** 2) / max(w.sum(), 1e-9)))
