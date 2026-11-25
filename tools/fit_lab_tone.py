#!/usr/bin/env python3
"""Measure Lab Tone's curve (spec/lab-tone.md) from registered pairs.

    python3.11 tools/fit_lab_tone.py --quantiles DIR [--write]

DIR holds one .npz per frame with `qa` and `qt` (100 OKLab L quantiles,
0.5 .. 99.5 %, of FreeCCR's render and of the lab scan over the frame's
registered area; `frame_quantiles` below makes them from two aligned
images) and `roll` (the lab). FreeCCR's render is the default render with
the frame's six Channel Levels numbers fitted to the lab scan (as
tools/fit_organics.py), and FreeCCR's own conversion-time Auto tone (`auto_tone.measure` on
the de-windowed base inside the registered area, as Convert does), then
Organics v2 50 / Organic Saturation 50 / Clean Lights 50; the registration is tools/fit_look.py's.

Per frame the quantile pairs give its mapping (inside its own range only);
per lab the median over its frames at each L; the labs averaged (each the
same), filled where no lab reaches, smoothed with a 5-point binomial and
sampled at the knots. --write writes src/core/lab_tone_data.py."""
import argparse
import glob
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np  # noqa: E402

from core.oklab import to_oklab  # noqa: E402

Q = np.linspace(0.5, 99.5, 100)
G = np.linspace(0.0, 1.0, 101)
KNOTS = (0.00, 0.08, 0.16, 0.24, 0.32, 0.40, 0.48, 0.56, 0.64, 0.72, 0.80, 0.88, 0.94, 1.00)


def frame_quantiles(ours_rgb, lab_rgb, mask):
    """(qa, qt): OKLab L quantiles of two aligned display images in 0..1."""
    La = to_oklab(np.asarray(ours_rgb, np.float32)[mask].reshape(-1, 3))[:, 0]
    Lt = to_oklab(np.asarray(lab_rgb, np.float32)[mask].reshape(-1, 3))[:, 0]
    return np.percentile(La, Q), np.percentile(Lt, Q)


def lab_curves(frames):
    """{lab: curve on G with NaN where none of its frames reaches}."""
    by = {}
    for qa, qt, lab in frames:
        qa = np.maximum.accumulate(np.asarray(qa, float) + np.arange(len(qa)) * 1e-9)
        mp = np.where((G >= qa[0]) & (G <= qa[-1]), np.interp(G, qa, qt), np.nan)
        by.setdefault(lab, []).append(mp)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # L no frame reaches
        return {k: np.nanmedian(np.array(v), 0) for k, v in by.items()}


def knot_values(curves):
    arr = np.array(list(curves.values()), float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        avg = np.nanmean(arr, 0)
    ok = ~np.isnan(avg)
    avg = np.interp(G, G[ok], avg[ok])
    sm = np.convolve(np.pad(avg, 2, mode="edge"), np.array([1, 4, 6, 4, 1]) / 16.0, "valid")
    vals = np.interp(KNOTS, G, sm)
    return np.maximum.accumulate(vals + np.arange(len(vals)) * 1e-6)


def write(values, path):
    with open(path) as fh:
        head = fh.read().split("\nKNOTS = ")[0] + "\n"
    with open(path, "w") as fh:
        fh.write(head + "KNOTS = (" + ", ".join(f"{k:.2f}" for k in KNOTS) + ")\n"
                 "VALUES = (" + ", ".join(f"{v:.3f}" for v in values) + ")\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quantiles", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    frames = []
    for f in sorted(glob.glob(os.path.join(a.quantiles, "*.npz"))):
        z = np.load(f)
        frames.append((z["qa"], z["qt"], str(z["roll"])))
    curves = lab_curves(frames)
    vals = knot_values(curves)
    print(len(frames), "frames,", len(curves), "labs")
    print("knots", " ".join(f"{k:.2f}:{v:.3f}" for k, v in zip(KNOTS, vals)))
    for lab in curves:
        rest = knot_values({k: c for k, c in curves.items() if k != lab})
        print(f"without {lab}:", " ".join(f"{v:.3f}" for v in rest))
    if a.write:
        path = os.path.join(HERE, "..", "src", "core", "lab_tone_data.py")
        write(vals, path)
        print("wrote", path)


if __name__ == "__main__":
    main()
