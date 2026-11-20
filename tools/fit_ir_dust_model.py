#!/usr/bin/env python3
"""Fit IR dust's default model (spec/ir-dust-v2.md §3.3) on hand-checked
candidates, through the module's own feature code, and write it to
src/core/ir_dust_default.py.

    python tools/fit_ir_dust_model.py --folder "<folder with the RAWs>"

The labels (tools/ir_dust_labels.json) name each frame's IR and visible RAWs
and give block-grid positions of candidates with 1 = dust, 0 = not. Each label
is matched to the module's nearest candidate within MATCH blocks. Prints the
held-out-by-frame result (trained on the other frames) before fitting on all.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from core import ir_dust  # noqa: E402

MATCH = 3.0
LAMBDA = 2.0


def fit_logistic(X, y, lam=LAMBDA):
    """(mu, sd, w, b): L2-regularised logistic regression on standardised
    features (the bias unpenalised), Newton's method."""
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    th = np.zeros(Z.shape[1])
    reg = lam * np.r_[np.ones(Z.shape[1] - 1), 0.0]
    for _ in range(60):
        p = 1 / (1 + np.exp(-np.clip(Z @ th, -40, 40)))
        g = Z.T @ (p - y) + reg * th
        H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg + 1e-9)
        step = np.linalg.solve(H, g)
        th -= step
        if np.max(np.abs(step)) < 1e-10:
            break
    return mu, sd, th[:-1], float(th[-1])


def matched(folder, spec):
    rows = []
    for f, files in spec["frames"].items():
        ir = ir_dust.read_blocks(os.path.join(folder, files["ir"]))
        vis = [ir_dust.read_blocks(os.path.join(folder, v)) for v in files["visible"]]
        dm = ir_dust.dust_map(ir, vis)
        cs = np.array([c.centre() for c in dm.cands]) - 0.5
        lost = 0
        for lab in (x for x in spec["labels"] if x["frame"] == f):
            d = np.hypot(cs[:, 0] - lab["x"], cs[:, 1] - lab["y"])
            j = int(np.argmin(d))
            if d[j] > MATCH:
                lost += 1
                continue
            rows.append((f, dm.cands[j].feats.astype(np.float64), lab["dust"]))
        print(f"{f}: {len(dm.cands)} candidates, {lost} labels without one", flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default="/mnt/user-data/uploads/Dust detection samples")
    ap.add_argument("--labels", default=os.path.join(HERE, "ir_dust_labels.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "src", "core", "ir_dust_default.py"))
    a = ap.parse_args()
    spec = json.load(open(a.labels))
    rows = matched(a.folder, spec)
    frames = sorted({r[0] for r in rows})
    tp = fp = n1 = n0 = 0
    for f in frames:
        tr = [r for r in rows if r[0] != f]
        te = [r for r in rows if r[0] == f]
        m = fit_logistic(np.array([r[1] for r in tr]), np.array([r[2] for r in tr], float))
        X = np.array([r[1] for r in te])
        p = 1 / (1 + np.exp(-(m[3] + ((X - m[0]) / m[1]) @ m[2])))
        y = np.array([r[2] for r in te])
        a1, a0 = int(((p > 0.5) & (y == 1)).sum()), int(((p > 0.5) & (y == 0)).sum())
        tp, fp, n1, n0 = tp + a1, fp + a0, n1 + int(y.sum()), n0 + int((1 - y).sum())
        print(f"held out {f}: dust {a1}/{int(y.sum())}, false {a0}/{int((1 - y).sum())}")
    print(f"held out, all: dust {tp}/{n1}, false {fp}/{n0}")
    mu, sd, w, b = fit_logistic(np.array([r[1] for r in rows]), np.array([r[2] for r in rows], float))
    fmt = lambda v: "(" + ", ".join(f"{x:.6g}" for x in v) + ")"  # noqa: E731
    with open(a.out, "w") as fh:
        fh.write('"""IR dust\'s default model (spec/ir-dust-v2.md §3.3), WRITTEN by\n'
                 'tools/fit_ir_dust_model.py from tools/ir_dust_labels.json: re-run it,\n'
                 'never edit by hand.\n\n'
                 f'Fitted on {len(rows)} hand-checked candidates ({int(sum(r[2] for r in rows))} dust) '
                 f'from {len(frames)} frames;\nheld out by frame: dust {tp}/{n1} found, '
                 f'{fp}/{n0} false.\n\n'
                 'p = 1 / (1 + exp(-(B + sum(W * (x - MU) / SD)))), x in ir_dust.FEATURES order.\n"""\n\n')
        fh.write(f"FEATURES = {tuple(ir_dust.FEATURES)!r}\n")
        fh.write(f"MU = {fmt(mu)}\nSD = {fmt(sd)}\nW = {fmt(w)}\nB = {b:.6g}\n")
    print("wrote", os.path.normpath(a.out))
    for n, v in zip(ir_dust.FEATURES, w):
        print(f"   {n:16s} {v:+.3f} per SD")


if __name__ == "__main__":
    main()
