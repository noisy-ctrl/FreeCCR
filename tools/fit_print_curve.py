"""Fit the print curve of the picture-levels conversion (spec/picture-levels-conversion.md §8).

Reads tools/print_curve_quantiles.json: per frame and channel, the scan's
density quantiles and the reference converter's output quantiles inside the
same picture area. Each frame/channel is put into transmission levels set from
its own picture (black = thinnest 0.1 %, white = densest 0.2 %), and one curve
F(u) -> display is fitted to every frame and channel at once: least squares on
piecewise-linear weights at 11 uniform knots with a small second-difference
penalty, then made monotone. Writes src/core/print_curve_data.py and prints
each frame's RMS through the monotone cubic the app evaluates.

Usage:  python tools/fit_print_curve.py [quantiles.json] [out.py]
"""

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

BLACK_P, WHITE_P = 0.1, 99.8      # density percentiles: thinnest 0.1 %, densest 0.2 %
USE_LO, USE_HI = 0.1, 99.8        # quantiles used in the fit
SMOOTH = 0.02                     # second-difference penalty
N_KNOTS = 11


def levels_u(density, P):
    """Transmission levels of one channel's density quantiles."""
    d = np.asarray(density, float)
    t = 10.0 ** (-d)
    tb, tw = t[P.index(BLACK_P)], t[P.index(WHITE_P)]
    return (tb - t) / (tb - tw)


def weights(x, n):
    x = np.clip(x, 0.0, 1.0)
    a = np.zeros((len(x), n))
    i = np.minimum((x * (n - 1)).astype(int), n - 2)
    t = x * (n - 1) - i
    a[np.arange(len(x)), i] = 1.0 - t
    a[np.arange(len(x)), i + 1] = t
    return a


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "print_curve_quantiles.json")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "src", "core", "print_curve_data.py")
    data = json.load(open(src))
    P = [float(p) for p in data["percentiles"]]
    use = np.array([USE_LO <= p <= USE_HI for p in P])
    us, os_ = [], []
    for fr in data["frames"].values():
        for c in range(3):
            u = levels_u(fr["density"][c], P)[use]
            o = np.asarray(fr["reference"][c], float)[use] / 255.0
            keep = (o > 1 / 255) & (o < 254 / 255)        # clipped / censored ends out
            us.append(u[keep])
            os_.append(o[keep])
    u = np.concatenate(us)
    o = np.concatenate(os_)
    a = weights(u, N_KNOTS)
    d2 = np.zeros((N_KNOTS - 2, N_KNOTS))
    for k in range(N_KNOTS - 2):
        d2[k, k:k + 3] = (1.0, -2.0, 1.0)
    m = np.vstack([a, SMOOTH * np.sqrt(len(u) / (N_KNOTS - 2)) * d2])
    b = np.concatenate([o, np.zeros(N_KNOTS - 2)])
    y = np.linalg.lstsq(m, b, rcond=None)[0]
    y = np.maximum.accumulate(np.maximum(y, 0.0))
    for k in range(1, N_KNOTS):                            # strictly increasing
        y[k] = max(y[k], y[k - 1] + 1e-4)

    with open(dst, "w") as f:
        f.write('"""The print curve of the picture-levels conversion: display value at u = 0, 0.1, ... 1\n'
                '(spec/picture-levels-conversion.md §8). WRITTEN by tools/fit_print_curve.py from\n'
                'tools/print_curve_quantiles.json - never edit by hand."""\n\n')
        f.write("KNOTS = (" + ", ".join(f"{v:.6f}" for v in y) + ")\n")
    print("knots x255:", np.round(y * 255, 1).tolist())

    import importlib
    import core.print_curve_data as pcd
    importlib.reload(pcd)
    import core.picture_levels as pl
    importlib.reload(pl)
    for name, fr in data["frames"].items():
        e = []
        for c in range(3):
            uu = levels_u(fr["density"][c], P)[use]
            e.append(pl.F(uu) - np.asarray(fr["reference"][c], float)[use] / 255.0)
        print(f"{name}: RMS {np.sqrt(np.mean(np.square(e))) * 255:.1f} levels")


if __name__ == "__main__":
    main()
