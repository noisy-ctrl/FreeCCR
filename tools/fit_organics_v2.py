#!/usr/bin/env python3
"""Measure Colour Organics v2's tables from lab scans + your own scans of the
same negatives (spec/colour-organics-v2.md).

    python3.11 tools/fit_organics_v2.py --lab "<lab scans>" --scans "<RAWs>" \\
        [--lab ... --scans ...] [--samples pairs.npz] [--write]

Each --lab/--scans pair is one lab. Frames are paired, decoded and balanced
exactly as tools/fit_organics.py does (each frame's six Channel Levels
numbers fitted to its lab scan, FreeCCR's default render). Then, per lab, a
Gaussian-kernel average over (OKLab hue, OKLab L) knots of:

- the lab's hue minus ours (HUE_MOVE), on FreeCCR's default render;
- log of the lab's chroma over ours (CHROMA_LOG), on the default render with
  Clean Lights at its labs' amount applied per sample, capped at 0;

each node shrunk towards 0 by its support (eff / (eff + 150)) and the labs
combined weighted by their support (a node only one lab measured keeps that
lab's value, shrunk). Every frame counts the same inside a lab.

--samples keeps the paired samples (ours, lab, frame, lab id) so a refit needs
no decoding. With two or more labs each lab is also predicted from the others.
--write writes src/core/organics_v2_data.py.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "src"), HERE]

import numpy as np  # noqa: E402

from core import clean_lights as cl  # noqa: E402
from core import look_fit as lf  # noqa: E402
from core import organics_fit as of  # noqa: E402
from core import organics_v2 as v2  # noqa: E402
from core.oklab import to_oklab, from_oklab  # noqa: E402

HK = np.arange(0.0, 360.0, 15.0)
LK = np.array([0.15, 0.30, 0.45, 0.60, 0.75, 0.90])
SHRINK_N = 150.0


def lch(rgb):
    o = to_oklab(np.clip(rgb, 0, 1).astype(np.float32)).astype(np.float64)
    return o[:, 0], np.hypot(o[:, 1], o[:, 2]), np.degrees(np.arctan2(o[:, 2], o[:, 1])) % 360


def with_clean_lights(rgb):
    o = to_oklab(np.clip(rgb, 0, 1).astype(np.float32))
    m = cl.strength(o[:, 0], cl.LAB_AMOUNT)
    o[:, 1:] *= (1.0 - m)[:, None]
    return np.clip(from_oklab(o), 0, 1).astype(np.float64)


def kernel(h, L):
    dx = ((h[:, None] - HK[None, :] + 180) % 360) - 180
    return (np.exp(-0.5 * (dx / v2.SIGMA_H) ** 2),
            np.exp(-0.5 * ((L[:, None] - LK[None, :]) / v2.SIGMA_L) ** 2))


def lab_table(ours, lab, frame, y_of):
    """One lab's (value, support) on the knots for the measure y_of(ours, lab)."""
    w = 1.0 / np.bincount(frame)[frame]
    Lo, Co, ho = lch(ours)
    _, Ct, _ = lch(lab)
    keep = (Co > 0.025) & (Ct > 0.012) & (Lo > 0.06) & (Lo < 0.96)
    y = y_of(ours, lab)[keep]
    ww = (w * np.clip((Co - 0.015) / 0.03, 0, 1))[keep]
    kh, kl = kernel(ho[keep], Lo[keep])
    wt = ww[:, None, None] * kh[:, :, None] * kl[:, None, :]
    den = wt.sum(0)
    val = np.einsum("n,nhl->hl", y, wt) / np.maximum(den, 1e-12)
    eff = den / max(w[keep].mean(), 1e-12)
    s = eff / (eff + SHRINK_N)
    return val * s, s


def combine(tables):
    S = np.sum([s for _, s in tables], 0)
    return np.sum([v for v, _ in tables], 0) / np.maximum(S, 1.0)


def hue_move(ours, lab):
    _, _, ho = lch(ours)
    _, _, ht = lch(lab)
    return np.clip((ht - ho + 180) % 360 - 180, -40, 40)


def chroma_log(ours, lab):
    _, Co, _ = lch(ours)
    _, Ct, _ = lch(lab)
    return np.clip(np.log(np.maximum(Ct, 1e-4) / np.maximum(Co, 1e-4)), -0.8, 0.8)


def fit(labs):
    A = combine([lab_table(s["ours"], s["lab"], s["frame"], hue_move) for s in labs])
    B = combine([lab_table(with_clean_lights(s["ours"]), s["lab"], s["frame"], chroma_log)
                 for s in labs])
    return A, np.minimum(B, 0.0)


def collect(lab_dirs, scan_dirs):
    import fit_organics as fo
    out = []
    for k, (ld, sd) in enumerate(zip(lab_dirs, scan_dirs)):
        frames = fo.pair_samples(ld, sd)
        ours, labv, fr = [], [], []
        for j, (_name, _lab, d, t) in enumerate(frames):
            q = of.balance_frame(d, t)
            o = lf.frame_apply(of.default_render, d, q)
            ok = (o.min(1) > 0.003) & (o.max(1) < 0.997) & (t.min(1) > 0.01) & (t.max(1) < 0.985)
            ours.append(o[ok]); labv.append(t[ok]); fr.append(np.full(ok.sum(), j))
        out.append(dict(ours=np.concatenate(ours), lab=np.concatenate(labv),
                        frame=np.concatenate(fr), name=os.path.basename(ld.rstrip("/"))))
    return out


def write(A, B, path):
    rows = lambda T, f: "\n".join("    (" + ", ".join(f.format(v) for v in r) + ")," for r in T)
    with open(path) as fh:
        head = fh.read().split("\nHUE_KNOTS = ")[0] + "\n"
    with open(path, "w") as fh:
        fh.write(head + "HUE_KNOTS = tuple(float(h) for h in range(0, 360, 15))\n"
                 "L_KNOTS = (0.15, 0.30, 0.45, 0.60, 0.75, 0.90)\n\n"
                 f"HUE_MOVE = (\n{rows(A, '{:+.2f}')}\n)\n\n"
                 f"CHROMA_LOG = (\n{rows(B, '{:+.3f}')}\n)\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lab", action="append", default=[])
    ap.add_argument("--scans", action="append", default=[])
    ap.add_argument("--samples", help="an .npz to keep / reuse the paired samples")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if a.samples and os.path.exists(a.samples):
        z = np.load(a.samples, allow_pickle=True)
        labs = list(z["labs"])
    else:
        if not a.lab or len(a.lab) != len(a.scans):
            ap.error("give --lab/--scans pairs (or an existing --samples file)")
        labs = collect(a.lab, a.scans)
        if a.samples:
            np.savez(a.samples, labs=np.array(labs, dtype=object))
    A, B = fit(labs)
    np.set_printoptions(precision=2, suppress=True, linewidth=160)
    print("hue move (deg), rows = hue every 15, cols = L", LK)
    print(A)
    print("chroma ratio on top of Clean Lights 50")
    print(np.exp(B))
    if len(labs) > 1:
        for k, s in enumerate(labs):
            A2, _ = fit([t for j, t in enumerate(labs) if j != k])
            Lo, Co, ho = lch(s["ours"])
            _, _, ht = lch(s["lab"])
            sel = Co > 0.03
            before = np.abs(((ht - ho + 180) % 360 - 180)[sel])
            kh, kl = kernel(ho[sel], Lo[sel])
            pred = ((kh @ A2) * kl).sum(1) / (kh.sum(1) * kl.sum(1))
            after = np.abs(((ht - ho + 180) % 360 - 180)[sel] - pred)
            print(f"{s['name']}: median |hue error| {np.median(before):.2f} -> "
                  f"{np.median(after):.2f} deg predicted from the other labs")
    if a.write:
        path = os.path.join(HERE, "..", "src", "core", "organics_v2_data.py")
        write(A, B, path)
        print("wrote", path)


if __name__ == "__main__":
    main()
