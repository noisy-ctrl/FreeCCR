#!/usr/bin/env python3
"""Build core/x_tones.npz: the measured tone tools measured from
HALD exports, as changes on FreeCCR's display values (spec/x-tone-tools.md).

    python3.11 tools/fit_x_tones.py --base base.tiff \
        --hx-100 l-100.tiff --hx-50 l-50.tiff --hx+50 l+50.tiff --hx+100 l+100.tiff \
        --sx-100 ... --px+100 ... [--out src/core/x_tones.npz] [--grid 33]

Every input is a level-16 HALD (4096 x 4096, 256 steps per channel) exported
from the measured positive mode: `--base` with no adjustment, the others with one
tool at -100 / -50 / +50 / +100 and nothing else.
"""
import argparse
import os
import sys

import cv2
import numpy as np

TOOLS = ("hx", "sx", "px")
AMOUNTS = ("-100", "-50", "+50", "+100")


def read_hald(path):
    """A level-16 HALD as a [b, g, r, 3] RGB LUT in 0..1."""
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None or im.shape[:2] != (4096, 4096):
        raise SystemExit(f"{path}: not a level-16 HALD (4096 x 4096)")
    scale = 65535.0 if im.dtype == np.uint16 else 255.0
    rgb = im[..., [2, 1, 0]].reshape(-1, 3).astype(np.float32) / np.float32(scale)
    return rgb.reshape(256, 256, 256, 3)


def lut_at(L, rgb):
    """Trilinear lookup of a 256^3 [b, g, r] LUT at rgb (M, 3) in 0..1."""
    p = np.clip(rgb, 0.0, 1.0) * 255.0
    i0 = np.minimum(np.floor(p).astype(np.int64), 254)
    f = (p - i0).astype(np.float32)
    out = 0.0
    for db in (0, 1):
        wb = f[:, 2:3] if db else 1 - f[:, 2:3]
        for dg in (0, 1):
            wg = f[:, 1:2] if dg else 1 - f[:, 1:2]
            for dr in (0, 1):
                wr = f[:, 0:1] if dr else 1 - f[:, 0:1]
                out = out + wb * wg * wr * L[i0[:, 2] + db, i0[:, 1] + dg, i0[:, 0] + dr]
    return out


def invert(base, Y, iters=25):
    """x with base(x) ~= Y (Gauss-Newton, x kept in the cube) and the residual."""
    gi = np.arange(256) / 255.0
    grey = np.array([base[i, i, i] for i in range(256)])
    x = np.stack([np.interp(Y[:, c], grey[:, c], gi) for c in range(3)], 1)
    h = 1.0 / 255.0
    for _ in range(iters):
        f = lut_at(base, x) - Y
        J = np.empty((len(x), 3, 3))
        for c in range(3):
            e = np.zeros(3)
            e[c] = h
            xp, xm = np.clip(x + e, 0, 1), np.clip(x - e, 0, 1)
            J[:, :, c] = (lut_at(base, xp) - lut_at(base, xm)) / np.maximum((xp - xm)[:, c:c + 1], 1e-6)
        dx = np.linalg.solve(J + 1e-6 * np.eye(3), -f[..., None])[..., 0]
        x = np.clip(x + np.clip(dx, -0.1, 0.1), 0, 1)
    return x, np.abs(lut_at(base, x) - Y).max(1)


def harmonic_fill(G, known, iters=3000):
    """Extend G from the `known` nodes into the rest: the discrete Laplace
    equation, Neumann at the cube faces."""
    G = G.copy()
    unk = ~known
    for _ in range(iters):
        P = np.pad(G, ((1, 1), (1, 1), (1, 1), (0, 0)), mode="edge")
        avg = (P[2:, 1:-1, 1:-1] + P[:-2, 1:-1, 1:-1] + P[1:-1, 2:, 1:-1]
               + P[1:-1, :-2, 1:-1] + P[1:-1, 1:-1, 2:] + P[1:-1, 1:-1, :-2]) / 6.0
        G[unk] = avg[unk]
    return G


def build(base, tools, n=33):
    """{key: array} for np.savez: per tool and sign the quadratic's A and B on
    an n^3 grid [b, g, r, 3] of FreeCCR display values."""
    v = np.linspace(0.0, 1.0, n)
    bb, gg, rr = np.meshgrid(v, v, v, indexing="ij")
    U = np.stack([rr, gg, bb], -1).reshape(-1, 3)
    b0, w0 = base[0, 0, 0].astype(np.float64), base[255, 255, 255].astype(np.float64)
    Y = b0 + U * (w0 - b0)
    X, res = invert(base, Y)
    B = lut_at(base, X)
    known = (res < 1.0 / 255.0).reshape(n, n, n)
    out = {"black": b0.astype(np.float32), "white": w0.astype(np.float32),
           "reachable": known}
    for tool in TOOLS:
        for sign in "+-":
            d = {}
            for a in ("50", "100"):
                D = (lut_at(tools[(tool, sign + a)], X) - B) / (w0 - b0)
                d[a] = harmonic_fill(D.reshape(n, n, n, 3), known)
            k = f"{tool}_{'pos' if sign == '+' else 'neg'}"
            out[k + "_A"] = (4 * d["50"] - d["100"]).astype(np.float16)
            out[k + "_B"] = (2 * d["100"] - 4 * d["50"]).astype(np.float16)
    print(f"grid {n}^3: {known.mean():.0%} of the nodes inside the measured render")
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True)
    for t in TOOLS:
        for a in AMOUNTS:
            ap.add_argument(f"--{t}{a}", required=True, dest=f"{t}{a}")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "src",
                                                  "core", "x_tones.npz"))
    ap.add_argument("--grid", type=int, default=33)
    a = ap.parse_args(argv)
    base = read_hald(a.base)
    tools = {(t, am): read_hald(getattr(a, f"{t}{am}")) for t in TOOLS for am in AMOUNTS}
    data = build(base, tools, a.grid)
    np.savez_compressed(a.out, **data)
    print("wrote", os.path.abspath(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
