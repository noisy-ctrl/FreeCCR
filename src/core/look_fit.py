"""Fit a Film Look (3-D LUT) from pairs of lab scans and our own scans.

numpy + OpenCV only (SciPy is not usable: FreeCCR pins numpy < 2).
See spec/film-look-lut.md for the model and the reasoning behind it.

    lab_sRGB  ~=  F( d * (1 + g_k) + o_k )

d    FreeCCR's density base: 0.8 * log10(black_point / x), the display value
     of the windowed black-point conversion (0 = film base).
F    the shared look = parametric prior (3x3 density matrix, monotone tone
     curve per channel) + smooth 17^3 residual (Laplacian-regularised).
o, g per-frame density offset / per-channel contrast (the lab operator's
     corrections), mean-zero over the frames.
"""
import os

import cv2
import numpy as np

DENSITY_SLOPE = 0.8              # == ccr_processor.DEFAULT_DENSITY_SLOPE
DOMAIN = (-0.25, 1.25)           # LUT input range in density display units
CH_SLIDER_DIV = 150.0            # == ccr_processor.CH_SLIDER_DIV

# --------------------------------------------------------------------------- #
# colour maths
# --------------------------------------------------------------------------- #
_M_SRGB_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]])
_WHITE = np.array([0.95047, 1.0, 1.08883])


def srgb_to_linear(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def srgb_to_lab(v):
    xyz = srgb_to_linear(v) @ _M_SRGB_XYZ.T / _WHITE
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def de2000(lab1, lab2):
    """CIEDE2000 colour difference (vectorised)."""
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(C1p * C2p == 0, 0, dh)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    hs = h1p + h2p
    hbp = np.where(np.abs(h1p - h2p) > 180, (hs + 360) / 2, hs / 2)
    hbp = np.where(C1p * C2p == 0, hs, hbp) % 360
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dtheta = 30 * np.exp(-((hbp - 275) / 25) ** 2)
    Rc = 2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dtheta)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                   + Rt * (dCp / Sc) * (dHp / Sh))


# --------------------------------------------------------------------------- #
# model pieces
# --------------------------------------------------------------------------- #
def pchip_eval(xk, yk, x):
    """Monotone cubic (Fritsch-Carlson) through (xk, yk), linear outside."""
    h = np.diff(xk)
    dlt = np.diff(yk) / h
    m = np.empty_like(yk)
    m[0], m[-1] = dlt[0], dlt[-1]
    s = dlt[:-1] * dlt[1:]
    w1 = 2 * h[1:] + h[:-1]
    w2 = h[1:] + 2 * h[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        hm = (w1 + w2) / (w1 / dlt[:-1] + w2 / dlt[1:])
    m[1:-1] = np.where(s > 0, hm, 0.0)
    i = np.clip(np.searchsorted(xk, x) - 1, 0, len(xk) - 2)
    t = (x - xk[i]) / h[i]
    t2, t3 = t * t, t * t * t
    y = ((2 * t3 - 3 * t2 + 1) * yk[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
         + (-2 * t3 + 3 * t2) * yk[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    y = np.where(x < xk[0], yk[0] + m[0] * (x - xk[0]), y)
    return np.where(x > xk[-1], yk[-1] + m[-1] * (x - xk[-1]), y)


KNOTS = np.linspace(-0.3, 1.3, 13)


class Prior:
    """t_c = curve_c((M x)_c); curve = monotone cubic through 13 knots."""
    n_curve = len(KNOTS)

    @staticmethod
    def unpack(p):
        M = p[:9].reshape(3, 3)
        c = p[9:9 + 3 * Prior.n_curve].reshape(3, Prior.n_curve)
        y0 = c[:, :1]
        inc = np.logaddexp(0.0, c[:, 1:])          # softplus: increasing curve
        return M, np.concatenate([y0, y0 + np.cumsum(inc, 1)], 1)

    @staticmethod
    def init():
        c = np.zeros((3, Prior.n_curve))
        c[:, 0] = -0.1
        c[:, 1:] = np.log(np.expm1(0.9 / (Prior.n_curve - 1)))
        return np.concatenate([np.eye(3).ravel(), c.ravel()])

    @staticmethod
    def eval(p, x):
        M, yk = Prior.unpack(p)
        u = x @ M.T
        return np.stack([pchip_eval(KNOTS, yk[c], u[:, c]) for c in range(3)], 1)


def lm(resid, p0, iters=50, lam=1e-2, eps=1e-5):
    """Levenberg-Marquardt, forward-difference Jacobian."""
    p = np.asarray(p0, dtype=np.float64).copy()
    r = resid(p)
    cost = r @ r
    for _ in range(iters):
        J = np.empty((r.size, p.size))
        for j in range(p.size):
            dp = np.zeros_like(p)
            dp[j] = eps
            J[:, j] = (resid(p + dp) - r) / eps
        g = J.T @ r
        A = J.T @ J
        while True:
            step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), -g)
            r2 = resid(p + step)
            c2 = r2 @ r2
            if np.isfinite(c2) and c2 < cost:
                done = (cost - c2) < 1e-7 * cost
                p, r, cost, lam = p + step, r2, c2, max(lam / 3, 1e-7)
                break
            lam *= 4
            if lam > 1e8:
                return p
        if done:
            break
    return p


class Grid:
    def __init__(self, lo=DOMAIN[0], hi=DOMAIN[1], n=17):
        self.lo, self.hi, self.n = float(lo), float(hi), int(n)
        self.step = (self.hi - self.lo) / (self.n - 1)

    def weights(self, x):
        n = self.n
        x = np.nan_to_num(x, nan=0.0, posinf=self.hi, neginf=self.lo)
        u = np.clip((x - self.lo) / self.step, 0, n - 1 - 1e-9)
        i0 = np.floor(u).astype(int)
        f = u - i0
        idx, wts = [], []
        for dz in (0, 1):
            for dy in (0, 1):
                for dx in (0, 1):
                    idx.append((i0[:, 2] + dz) * n * n + (i0[:, 1] + dy) * n
                               + (i0[:, 0] + dx))
                    wts.append((f[:, 0] if dx else 1 - f[:, 0])
                               * (f[:, 1] if dy else 1 - f[:, 1])
                               * (f[:, 2] if dz else 1 - f[:, 2]))
        return np.stack(idx, 1), np.stack(wts, 1)

    def apply(self, table, x):
        idx, w = self.weights(x)
        return np.einsum("nk,nkc->nc", w, table[idx])


def fit_residual(grid, x, y, lam_s=1.0, lam_0=0.05):
    """Smooth 3-D residual table (N,3) minimising data misfit + Laplacian
    roughness + a pull to zero (so empty regions fall back to the prior)."""
    n = grid.n
    N = n ** 3
    idx, w = grid.weights(x)
    AtA = np.zeros((N, N))
    for a in range(8):
        for b in range(8):
            np.add.at(AtA, (idx[:, a], idx[:, b]), w[:, a] * w[:, b])
    Aty = np.zeros((N, 3))
    for a in range(8):
        np.add.at(Aty, idx[:, a], w[:, a, None] * y)
    D = np.zeros((n - 2, n))
    for i in range(n - 2):
        D[i, i:i + 3] = (1, -2, 1)
    K = D.T @ D
    I = np.eye(n)
    LtL = np.kron(np.kron(K, I), I)
    LtL += np.kron(np.kron(I, K), I)
    LtL += np.kron(np.kron(I, I), K)
    s = len(x) / N
    AtA += lam_s * s * LtL + lam_0 * s * np.eye(N)
    return np.linalg.solve(AtA, Aty)


class Look:
    def __init__(self, p, table, grid):
        self.p, self.table, self.grid = p, table, grid

    def __call__(self, x):
        out = Prior.eval(self.p, x)
        if self.table is not None:
            out = out + self.grid.apply(self.table, x)
        return out

    def cube_table(self, size=33):
        """(size, size, size, 3) table indexed [b][g][r] over DOMAIN, clipped
        to [0, 1] (sRGB display values)."""
        g = np.linspace(self.grid.lo, self.grid.hi, size)
        b, gg, r = np.meshgrid(g, g, g, indexing="ij")
        x = np.stack([r.ravel(), gg.ravel(), b.ravel()], 1)
        out = np.clip(self(x), 0.0, 1.0)
        return out.reshape(size, size, size, 3)


def frame_apply(model, d, q):
    return model(d * (1 + q[3:6]) + q[:3])


def fit_frame(model, d, t, q0=None, iters=30):
    """The six per-frame numbers (offset, contrast) for a fixed look."""
    q0 = np.zeros(6) if q0 is None else q0
    return lm(lambda q: (frame_apply(model, d, q) - t).ravel(), q0, iters=iters)


def frame_error(model, d, t, q):
    pred = np.clip(frame_apply(model, d, q), 0, 1)
    return de2000(srgb_to_lab(pred), srgb_to_lab(t))


GAIN_PRIOR = 0.05                # per-frame contrast this size is "normal"
GAIN_WEIGHT = 0.002              # ...and costs (0.002 sRGB)^2 per residual


def fit_look(frames, lam_s=1.0, lam_0=0.05, grid_n=17, sub=1500, rounds=3,
             seed=0, progress=None, lm_iters=50):
    """frames: [(d (N,3), t (N,3)), ...]. Returns (Look, q (K,6)).

    Two stages, because a joint fit started from zero can trade the look's
    per-channel curve against large per-frame contrasts and stall there:
      1. prior + per-frame OFFSETS only (exposure / colour balance);
      2. from that solution, add per-frame per-channel CONTRAST, with a mild
         ridge so it only moves when the data asks for it.
    Then the smooth 3-D residual, alternating with per-frame refits."""
    rng = np.random.default_rng(seed)
    subs = []
    for d, t in frames:
        sel = rng.choice(len(d), min(sub, len(d)), replace=False)
        subs.append((d[sel], t[sel]))
    K = len(frames)
    n_p = Prior.init().size
    n_data = sum(len(d) for d, _t in subs) * 3

    def unpack(z, npf):
        q = z[n_p:].reshape(K - 1, npf)
        q = np.vstack([q, -q.sum(0, keepdims=True)])         # gauge: mean zero
        if npf == 3:
            q = np.hstack([q, np.zeros((K, 3))])
        return z[:n_p], q

    def make_resid(npf):
        ridge = np.sqrt(n_data) * GAIN_WEIGHT / GAIN_PRIOR

        def resid(z):
            p, q = unpack(z, npf)
            f = lambda x: Prior.eval(p, x)
            r = [(frame_apply(f, d, q[k]) - t).ravel() for k, (d, t) in enumerate(subs)]
            if npf == 6:
                r.append(ridge * q[:, 3:].ravel())
            return np.concatenate(r)
        return resid

    if progress:
        progress("fitting tone curves, colour matrix and per-frame balance")
    z = lm(make_resid(3), np.concatenate([Prior.init(), np.zeros(3 * (K - 1))]),
           iters=lm_iters)
    p, q = unpack(z, 3)
    if progress:
        progress("adding per-frame contrast")
    z = lm(make_resid(6), np.concatenate([p, q[:-1].ravel()]), iters=lm_iters)
    p, q = unpack(z, 6)
    grid = Grid(n=grid_n)
    T = np.concatenate([t for _d, t in frames])
    table = None
    for r in range(rounds + 1):
        if progress:
            progress(f"fitting 3D residual (round {r + 1}/{rounds + 1})")
        X = np.concatenate([d * (1 + q[k, 3:]) + q[k, :3]
                            for k, (d, _t) in enumerate(frames)])
        table = fit_residual(grid, X, T - Prior.eval(p, X), lam_s, lam_0)
        if r == rounds:
            break
        look = Look(p, table, grid)
        q = np.array([fit_frame(look, d, t, q[k]) for k, (d, t) in enumerate(subs)])
        q -= q.mean(0)
    return Look(p, table, grid), q


def channel_levels_for(q):
    """Channel Levels slider values reproducing a frame's (offset, contrast):
    x = (d + s_c)/(1 - G_c) + ms  ==  d (1+g_c) + o_c."""
    o, g = np.asarray(q[:3], float), np.asarray(q[3:6], float)
    G = g / (1 + g)
    ms = float(o.mean())
    s = (o - ms) / (1 + g)
    out = {"ch_master_shift": ms * CH_SLIDER_DIV}
    for c, ch in enumerate("rgb"):
        out[f"ch_{ch}_shift"] = s[c] * CH_SLIDER_DIV
        out[f"ch_{ch}_gain"] = G[c] * CH_SLIDER_DIV
    return out


# --------------------------------------------------------------------------- #
# pair preparation
# --------------------------------------------------------------------------- #
def density(v, bp):
    return DENSITY_SLOPE * np.log10(np.asarray(bp)[None, None, :] / np.maximum(v, 1e-5))


def _edge_bands(v, depth=260, half=300):
    """Brightest band within `depth` px of each edge: {side: (pos, median_rgb)}."""
    h, w = v.shape[:2]
    out = {}
    cy, cx = h // 2, w // 2
    prof = v[:depth, cx - half:cx + half].mean(1)
    y = int(np.argmax(prof.sum(1)))
    out["top"] = (y, np.median(v[max(y - 6, 0):y + 7, cx - 2 * half:cx + 2 * half].reshape(-1, 3), 0))
    prof = v[h - depth:, cx - half:cx + half].mean(1)
    y = h - depth + int(np.argmax(prof.sum(1)))
    out["bottom"] = (y, np.median(v[y - 6:y + 7, cx - 2 * half:cx + 2 * half].reshape(-1, 3), 0))
    prof = v[cy - half:cy + half, :depth].mean(0)
    x = int(np.argmax(prof.sum(1)))
    out["left"] = (x, np.median(v[cy - 2 * half:cy + 2 * half, max(x - 5, 0):x + 6].reshape(-1, 3), 0))
    prof = v[cy - half:cy + half, w - depth:].mean(0)
    x = w - depth + int(np.argmax(prof.sum(1)))
    out["right"] = (x, np.median(v[cy - 2 * half:cy + 2 * half, x - 5:x + 6].reshape(-1, 3), 0))
    return out


def rebate_black_point(v):
    """Per-frame black point from the clear film between the image and the
    holder: the brightest of the four edge bands. Returns (bp, side, bands)."""
    bands = _edge_bands(v)
    side = max(bands, key=lambda k: float(bands[k][1].sum()))
    return np.asarray(bands[side][1], float), side, bands


def image_area_mask(v, bands, margin=25, erode=31):
    """Inside the four edge bands (rebate and holder excluded), eroded."""
    h, w = v.shape[:2]
    m = np.zeros((h, w), np.uint8)
    top, bot = bands["top"][0] + margin, bands["bottom"][0] - margin
    left, right = bands["left"][0] + margin, bands["right"][0] - margin
    if bot - top < h // 3 or right - left < w // 3:      # bands not found: inset
        top, bot, left, right = h // 12, h - h // 12, w // 12, w - w // 12
    m[top:bot, left:right] = 1
    return cv2.erode(m, np.ones((erode, erode), np.uint8)).astype(bool)


def _gray_u8(img, valid=None):
    vals = img[valid] if valid is not None else img
    a, b = np.percentile(vals, [1, 99])
    g = np.clip((img - a) / max(b - a, 1e-6), 0, 1)
    g = (g * 255).astype(np.uint8)
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)


ORIENTATIONS = {
    "none": lambda a: a,
    "hflip": lambda a: a[:, ::-1],
    "vflip": lambda a: a[::-1, :],
    "rot180": lambda a: a[::-1, ::-1],
}

_sift = None


def _features(g):
    global _sift
    if _sift is None:
        _sift = cv2.SIFT_create(nfeatures=6000)
    return _sift.detectAndCompute(g, None)


def _match(fa, fb):
    (ka, da), (kb, db) = fa, fb
    if da is None or db is None or len(ka) < 12 or len(kb) < 12:
        return None, 0, None
    mm = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
    good = [p[0] for p in mm if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 12:
        return None, len(good), None
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    H, inl = cv2.findHomography(pa, pb, cv2.RANSAC, 3.0, maxIters=5000, confidence=0.999)
    if H is None:
        return None, 0, None
    inl = inl.ravel().astype(bool)
    err = np.linalg.norm(cv2.perspectiveTransform(pa[inl][None], H)[0] - pb[inl], axis=1)
    return H, int(inl.sum()), float(np.median(err))


def lab_luminance(lab_img):
    return lab_img @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def prepare_lab(img, long_side=1200):
    """Lab scan (float sRGB 0..1) at registration scale + its features."""
    s = long_side / max(img.shape[:2])
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return small, _features(_gray_u8(lab_luminance(small)))


def register(d, mask, labs):
    """Register density image `d` (with valid `mask`) against prepared lab
    scans [(small, features), ...]. Returns dict(index, orient, H, inliers,
    reproj_px, d_small, m_small) for the best match, or None."""
    s = 1240.0 / d.shape[1]
    d_s = cv2.resize(d.astype(np.float32), None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    m_s = cv2.resize(mask.astype(np.uint8), (d_s.shape[1], d_s.shape[0]),
                     interpolation=cv2.INTER_NEAREST).astype(bool)
    g = d_s @ np.array([0.3, 0.6, 0.1], np.float32)
    g = np.where(m_s, g, np.median(g[m_s]))
    best = None
    for oname, fn in ORIENTATIONS.items():
        fo = _features(_gray_u8(np.ascontiguousarray(fn(g))))
        for i, (_small, feats) in enumerate(labs):
            H, n, err = _match(fo, feats)
            if H is not None and (best is None or n > best["inliers"]):
                best = dict(index=i, orient=oname, H=H, inliers=n, reproj_px=err)
    if best is None:
        return None
    fn = ORIENTATIONS[best["orient"]]
    best["d_small"] = np.ascontiguousarray(fn(d_s))
    best["m_small"] = np.ascontiguousarray(fn(m_s.astype(np.uint8)))
    return best


def warp_to_lab(reg, lab_small):
    Ht, Wt = lab_small.shape[:2]
    warped = cv2.warpPerspective(reg["d_small"], reg["H"], (Wt, Ht), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    mw = cv2.warpPerspective(reg["m_small"], reg["H"], (Wt, Ht),
                             flags=cv2.INTER_NEAREST) > 0
    return warped, mw


def sample_blocks(d_w, m_w, lab_small, block=6, flat_pct=60):
    """Block means of the aligned pair; flat, unclipped, fully-valid blocks."""
    Ht, Wt = lab_small.shape[:2]
    bh, bw = Ht // block, Wt // block

    def blocks(a):
        a = a[:bh * block, :bw * block]
        return a.reshape(bh, block, bw, block, -1)

    dB, lB = blocks(d_w), blocks(lab_small)
    mB = blocks(m_w[..., None].astype(np.float32))
    ok = mB.min((1, 3))[..., 0] > 0
    ok &= (lB.max((1, 3)).max(-1) < 0.985) & (lB.min((1, 3)).min(-1) > 0.01)
    if ok.sum() < 50:
        return np.zeros((0, 3)), np.zeros((0, 3))
    l_std = lB.std((1, 3)).max(-1)
    d_std = dB.std((1, 3)).max(-1)
    ok &= l_std < np.percentile(l_std[ok], flat_pct)
    ok &= d_std < np.percentile(d_std[ok], flat_pct)
    return dB.mean((1, 3))[ok].astype(np.float64), lB.mean((1, 3))[ok].astype(np.float64)


def read_lab_scan(path):
    """Lab scan as float sRGB [0,1] RGB (8- or 16-bit JPEG/TIFF/PNG)."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if img is None:
        raise ValueError(f"cannot read {path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img = img[..., :3][..., ::-1]
    scale = 65535.0 if img.dtype == np.uint16 else 255.0
    return np.ascontiguousarray(img.astype(np.float32) / scale)


LAB_EXTS = (".jpg", ".jpeg", ".tif", ".tiff", ".png")


def list_lab_scans(folder):
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.lower().endswith(LAB_EXTS) and not f.startswith("."))
