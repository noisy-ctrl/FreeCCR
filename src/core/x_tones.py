"""Highlights ⅃ / Shadows L / Contrast L ⅃ (keys highlights_x / shadows_x /
punch_x): tone tools stored as measured 3D tables (spec/x-tone-tools.md).

Each tool is a change added to FreeCCR's display values (0..1), stored on a
33^3 grid per tool and sign as the quadratic through the measured 0, 50 and
100: change = A t + B t^2, t = |slider| / 100. The grid is built by
tools/fit_x_tones.py from graded HALD images, with their black..white mapped
onto FreeCCR's, so every near-neutral tone sits inside what was measured.
"""
import os
import threading

import numpy as np

XTONE_SHAPE_DEFAULT = {"hx_range": 50, "hx_keep": False,
                       "sx_range": 50, "sx_keep": False,
                       "px_keep_black": False, "px_keep_white": False}
KEEP_WHITE_FROM = 0.75    # Rec.709 luma where a kept white starts fading the change
KEEP_BLACK_TO = 0.25      # ...and where a kept black has it back in full
CHUNK = 1 << 20           # pixels per lookup pass (bounds memory on exports)

_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "x_tones.npz")
_lock = threading.Lock()
_data = None
_grids = {}


def normalize_xtone_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default."""
    if not isinstance(shape, dict):
        return None
    out = {}
    for k, dflt in XTONE_SHAPE_DEFAULT.items():
        v = shape.get(k, dflt)
        if isinstance(dflt, bool):
            out[k] = bool(v)
        else:
            try:
                out[k] = int(round(float(np.clip(float(v), 0.0, 100.0))))
            except (TypeError, ValueError):
                out[k] = dflt
    return None if out == XTONE_SHAPE_DEFAULT else out


def xtone_active(highlights_x=0.0, shadows_x=0.0, punch_x=0.0) -> bool:
    return bool(highlights_x) or bool(shadows_x) or bool(punch_x)


def _load():
    global _data
    with _lock:
        if _data is None:
            with np.load(_PATH) as z:
                _data = {k: z[k] for k in z.files}
        return _data


def _grid(tool, amount):
    """The change grid (N^3 flattened to [N*N*N, 3], float32) for `tool` at
    slider `amount`, cached per (tool, amount)."""
    key = (tool, round(float(amount), 3))
    g = _grids.get(key)
    if g is not None:
        return g
    d = _load()
    t = min(abs(float(amount)), 100.0) / 100.0
    k = f"{tool}_{'pos' if amount > 0 else 'neg'}"
    A = d[k + "_A"].astype(np.float32)
    B = d[k + "_B"].astype(np.float32)
    g = (A * np.float32(t) + B * np.float32(t * t)).reshape(-1, 3)
    with _lock:
        if len(_grids) > 24:
            _grids.clear()
        _grids[key] = g
    return g


def _lookup(G, u, n):
    """Trilinear lookup of a flattened n^3 [b, g, r] grid at u (M, 3) in 0..1:
    four lerps along r, two along g, one along b (float32 throughout; an
    int/float mix would promote to float64)."""
    p = np.clip(u, 0.0, 1.0) * np.float32(n - 1)
    i0 = np.minimum(p.astype(np.int32), n - 2)
    f = p - i0.astype(np.float32)
    base = (i0[:, 2] * n + i0[:, 1]) * n + i0[:, 0]
    fr = np.ascontiguousarray(f[:, 0])[:, None]
    fg = np.ascontiguousarray(f[:, 1])[:, None]
    fb = np.ascontiguousarray(f[:, 2])[:, None]

    def along_r(off):
        a = np.take(G, base + off, axis=0)
        b = np.take(G, base + (off + 1), axis=0)
        b -= a
        b *= fr
        a += b
        return a

    def along_g(off):
        a = along_r(off)
        b = along_r(off + n)
        b -= a
        b *= fg
        a += b
        return a

    a = along_g(0)
    b = along_g(n * n)
    b -= a
    b *= fb
    a += b
    return a


def _bias(u, k):
    """Per-channel tone remap with fixed ends: q = u / (u + k(1 - u))."""
    if k == 1.0:
        return u
    return u / np.maximum(u + np.float32(k) * (1.0 - u), np.float32(1e-6))


def _smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _keep_white(y, start=KEEP_WHITE_FROM):
    return 1.0 - _smooth((y - start) / (1.0 - start))


def _keep_black(y, end=KEEP_BLACK_TO):
    return _smooth(y / end)


def _apply_chunk(u, hx, sx, px, shape, n):
    out = u.copy()
    luma = (u @ _LUMA)[:, None]
    if hx:
        k = 2.0 ** (-(shape["hx_range"] - 50) / 50.0)
        d = _lookup(_grid("hx", hx), _bias(u, k), n)
        if shape["hx_keep"]:
            d *= _keep_white(luma, shape.get("_kw", KEEP_WHITE_FROM))
        out += d
    if sx:
        k = 2.0 ** ((shape["sx_range"] - 50) / 50.0)
        d = _lookup(_grid("sx", sx), _bias(u, k), n)
        if shape["sx_keep"]:
            d *= _keep_black(luma, shape.get("_kb", KEEP_BLACK_TO))
        out += d
    if px:
        v = np.clip(out, 0.0, 1.0)
        d = _lookup(_grid("px", px), v, n)
        if shape["px_keep_black"] or shape["px_keep_white"]:
            lv = (v @ _LUMA)[:, None]
            if shape["px_keep_black"]:
                d *= _keep_black(lv, shape.get("_kb", KEEP_BLACK_TO))
            if shape["px_keep_white"]:
                d *= _keep_white(lv, shape.get("_kw", KEEP_WHITE_FROM))
        out = v + d
    return np.clip(out, 0.0, 1.0)


_GREY = np.repeat(np.linspace(0.0, 1.0, 513, dtype=np.float32)[:, None], 3, axis=1)


def _turns_back(hx, sx, px, shape, n):
    y = _apply_chunk(_GREY, hx, sx, px, shape, n)
    return bool((np.diff(y, axis=0) < -1e-5).any())


def _unfolded(hx, sx, px, shape, n):
    """Options that steepen a tool can turn the tone curve back on itself on
    their steepest stretches: a Range far from the middle (it reads the
    change at a remapped tone) or a Keep fading a large change over a short
    stretch. Pull the ranges towards the middle, then widen the kept stretch,
    just enough that the grey axis never turns back (a Range then reads "as
    wide as it can go"). Default options are left alone."""
    if not _turns_back(hx, sx, px, shape, n):
        return shape
    out = dict(shape)
    if shape["hx_range"] != 50 or shape["sx_range"] != 50:
        def scaled(f):
            o = dict(shape)
            for k in ("hx_range", "sx_range"):
                o[k] = 50 + f * (shape[k] - 50)
            return o
        lo, hi = 0.0, 1.0
        for _ in range(10):
            mid = 0.5 * (lo + hi)
            if _turns_back(hx, sx, px, scaled(mid), n):
                hi = mid
            else:
                lo = mid
        out = scaled(lo)
        if not _turns_back(hx, sx, px, out, n):
            return out
    if any(shape[k] for k in ("hx_keep", "sx_keep", "px_keep_black", "px_keep_white")):
        for f in np.linspace(0.0, 1.0, 21)[1:]:
            o = dict(out, _kb=KEEP_BLACK_TO + f * (0.6 - KEEP_BLACK_TO),
                     _kw=KEEP_WHITE_FROM - f * (KEEP_WHITE_FROM - 0.4))
            if not _turns_back(hx, sx, px, o, n):
                return o
    return out


def _composite(hx, sx, px, shape, n):
    """The three tools with these options as ONE change grid: the exact chain
    evaluated at the grid's own nodes, so a render is a single lookup."""
    key = (round(hx, 3), round(sx, 3), round(px, 3), tuple(sorted(shape.items())))
    g = _grids.get(key)
    if g is not None:
        return g
    shape = _unfolded(hx, sx, px, shape, n)
    v = np.linspace(0.0, 1.0, n, dtype=np.float32)
    bb, gg, rr = np.meshgrid(v, v, v, indexing="ij")
    nodes = np.stack([rr, gg, bb], -1).reshape(-1, 3)
    g = (_apply_chunk(nodes, hx, sx, px, shape, n) - nodes).astype(np.float32)
    with _lock:
        if len(_grids) > 24:
            _grids.clear()
        _grids[key] = g
    return g


def apply_x_tones(img01, highlights_x=0.0, shadows_x=0.0, punch_x=0.0, shape=None):
    """Apply the three tools to an HxWx3 float image in 0..1 (display values).
    Returns a new float32 array; the input when every tool is at 0."""
    if not xtone_active(highlights_x, shadows_x, punch_x):
        return img01
    shape = normalize_xtone_shape(shape) or dict(XTONE_SHAPE_DEFAULT)
    n = _load()["hx_pos_A"].shape[0]
    G = _composite(float(highlights_x or 0), float(shadows_x or 0), float(punch_x or 0),
                   shape, n)
    a = np.asarray(img01, dtype=np.float32)
    flat = a.reshape(-1, 3)
    out = np.empty_like(flat)
    for s in range(0, flat.shape[0], CHUNK):
        u = np.clip(flat[s:s + CHUNK], 0.0, 1.0)
        out[s:s + CHUNK] = np.clip(u + _lookup(G, u, n), 0.0, 1.0)
    return out.reshape(a.shape)


def grey_curve(xs, highlights_x=0.0, shadows_x=0.0, punch_x=0.0, shape=None):
    """The tools on the grey axis: output (green channel) for grey inputs `xs`
    (for the popup's preview)."""
    xs = np.asarray(xs, dtype=np.float32)
    g = np.repeat(xs[:, None], 3, axis=1)[None]
    return apply_x_tones(g, highlights_x, shadows_x, punch_x, shape)[0, :, 1]
