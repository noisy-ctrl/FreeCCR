#!/usr/bin/env python3
"""Fit the marks' default model (spec/marks-learning.md): a logistic per
kind fitted to the RULE's verdicts at Sensitivity 50 on every candidate the
structural gate lets through, in his frames, and write it to
src/core/marks_default.py. Prints how the model's flags compare with the
rule's at Sensitivity 0 / 25 / 50 / 75 / 100.

    python tools/fit_marks_model.py --folder "<folder holding the RAWs>"

The RAWs named in tools/marks_frames.json are looked for anywhere under
--folder.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from core import ir_dust, film_marks, marks_model  # noqa: E402
from fit_ir_dust_model import fit_logistic  # noqa: E402

LAMBDA = 0.5


def find_file(folder, name):
    for root, _dirs, files in os.walk(folder):
        if name in files:
            return os.path.join(root, name)
    raise FileNotFoundError(name)


def candidates(folder, spec):
    rows = []
    for f, files in spec["frames"].items():
        vis = [ir_dust.read_blocks(find_file(folder, v)) for v in files["visible"]]
        ir_z = fl = None
        if files.get("ir"):
            dm = ir_dust.dust_map(ir_dust.read_blocks(find_file(folder, files["ir"])), vis)
            ir_z = dm.z.astype(np.float32)
            fl = np.zeros(dm.z.shape, bool)
            for i in ir_dust.flagged(dm, ir_dust.SENS_DEFAULT):
                c = dm.cands[i]
                fl[c.y0:c.y0 + c.mask.shape[0], c.x0:c.x0 + c.mask.shape[1]] |= c.mask
        mm = film_marks.find_marks(vis, ir_z, fl)
        n = 0
        for mk in mm.marks:
            if not film_marks.gate(mk, mm.has_ir):
                continue
            n += 1
            rows.append((f, mk.kind, mk.feats.astype(float).tolist(),
                         {s: film_marks.rule_passes(mk, s) for s in (0, 25, 50, 75, 100)}))
        print(f"{f}: {len(mm.marks)} candidates, {n} through the gate", flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--frames", default=os.path.join(HERE, "marks_frames.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "src", "core", "marks_default.py"))
    a = ap.parse_args()
    spec = json.load(open(a.frames))
    rows = candidates(a.folder, spec)
    models, report = {}, []
    for kind in ("light", "dark"):
        R = [r for r in rows if r[1] == kind]
        X = marks_model.features([r[2] for r in R])
        y = np.array([r[3][50] for r in R], float)
        mu, sd, w, b = fit_logistic(X, y, LAMBDA)
        models[kind] = {"MU": mu, "SD": sd, "W": w, "B": b}
        z = ((X - mu) / sd) @ w + b
        line = [f"{kind}: {len(R)} gated"]
        for s in (0, 25, 50, 75, 100):
            d, o = film_marks.thresholds(s)
            v = mu.copy()
            v[0], v[1] = np.log(d), np.log(o)
            v50 = mu.copy()
            d50, o50 = film_marks.thresholds(50)
            v50[0], v50[1] = np.log(d50), np.log(o50)
            sh = float(((v - mu) / sd) @ w) - float(((v50 - mu) / sd) @ w)
            rule = np.array([r[3][s] for r in R])
            mod = z >= sh
            line.append(f"s{s} rule {int(rule.sum())} model {int(mod.sum())} both {int((rule & mod).sum())}")
        report.append("; ".join(line))
        print(report[-1])

    def arr(v):
        return "[" + ", ".join(f"{float(x):.6g}" for x in v) + "]"
    with open(a.out, "w") as f:
        f.write('"""The marks\' default model, WRITTEN by tools/fit_marks_model.py: a\n'
                "logistic per kind fitted to the rule's verdicts at Sensitivity 50\n"
                "(spec/marks-learning.md). Don't edit by hand.\n\n")
        for line in report:
            f.write(line + "\n")
        f.write('"""\n\nMODELS = {\n')
        for kind, m in models.items():
            f.write(f'    "{kind}": {{"MU": {arr(m["MU"])},\n'
                    f'              "SD": {arr(m["SD"])},\n'
                    f'              "W": {arr(m["W"])},\n'
                    f'              "B": {float(m["B"]):.6g}}},\n')
        f.write("}\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
