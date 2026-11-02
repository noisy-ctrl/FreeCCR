#!/usr/bin/env python3
"""Measure Colour Organics' hue curve from lab scans + your own scans of the
same negatives (spec/colour-organics.md, revision 2026-11-02).

    python3.11 tools/fit_organics.py --lab "<lab scans folder>" \\
        --scans "<your RAW folder>" [--lab ... --scans ...] [--write]

Each --lab/--scans pair is one lab (give the pairs in the same order). Scans
are paired with lab files exactly as tools/fit_look.py pairs them. Each frame
is rendered the way FreeCCR's default render does, after fitting its six
Channel Levels numbers to the lab scan (so exposure and colour balance are
the lab's), and the lab's hue minus ours is measured on coloured mid and
bright samples. The curve is fitted on all labs together; with two or more
labs each lab is also predicted from the others (the honest accuracy figure).

Writes a report into --out (default ./organics_fit) and, with --write,
src/core/organics_measured.py (the table the app renders with).
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

from core import look_fit as lf  # noqa: E402
from core import organics_fit as of  # noqa: E402


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pair_samples(lab_dir, scan_dir, single=False):
    """[(frame name, lab name, d samples, lab samples)] for one lab."""
    import fit_look as fl
    lab_paths = lf.list_lab_scans(lab_dir)
    labs = [lf.prepare_lab(lf.read_lab_scan(p)) for p in lab_paths]
    out, used = [], {}
    for paths in fl.group_scans(scan_dir, single):
        names = ", ".join(os.path.basename(p) for p in paths)
        log(f"decoding {names}")
        v = fl.decode(paths)
        bp, where, bands = lf.frame_black_point(v)
        if bp is None:
            log(f"  film base not measurable ({where}) - skipped")
            continue
        mask = lf.image_area_mask(v, bands)
        d = lf.density(v, bp).astype(np.float32)
        reg = lf.register(d, mask, labs)
        if reg is None or reg["inliers"] < 40:
            log(f"  no lab scan matches {names} - skipped")
            continue
        li = reg["index"]
        if li in used and used[li] >= reg["inliers"]:
            log(f"  {os.path.basename(lab_paths[li])} already paired - skipped")
            continue
        warped, mw = lf.warp_to_lab(reg, labs[li][0])
        dS, tS = lf.sample_blocks(warped, mw, labs[li][0])
        used[li] = reg["inliers"]
        out = [o for o in out if o[1] != os.path.basename(lab_paths[li])]
        out.append((os.path.basename(paths[0]), os.path.basename(lab_paths[li]), dS, tS))
        log(f"  -> {os.path.basename(lab_paths[li])} ({reg['inliers']} matches), "
            f"{len(dS)} samples")
    return out


def frame_moves(frames):
    """Per frame: the hue moves on its coloured samples."""
    rows = []
    for name, lab, d, t in frames:
        q = of.balance_frame(d, t)
        ours = lf.frame_apply(of.default_render, d, q)
        err = float(np.abs(np.clip(ours, 0, 1) - t).mean())
        h, dh, w, v = of.hue_moves(ours, t)
        keep = v >= of.VALUE_MIN
        log(f"  {name} ~ {lab}: render error {err:.3f}, {int(keep.sum())} coloured "
            "mid/bright samples")
        rows.append((h[keep], dh[keep], w[keep], v[keep]))
    return rows


def stack(rows):
    return [np.concatenate([r[i] for r in rows]) for i in range(4)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", action="append", required=True)
    ap.add_argument("--scans", action="append", required=True)
    ap.add_argument("--single", action="store_true", help="one RAW per frame")
    ap.add_argument("--smooth", type=float, default=1.0,
                    help="smoothness round the hue circle (default 1)")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "organics_fit"))
    ap.add_argument("--write", action="store_true",
                    help="write src/core/organics_measured.py")
    ap.add_argument("--moves", help="an .npz to keep the measured hue moves in: "
                    "written after measuring, read instead of measuring when it "
                    "exists (to try another --smooth without decoding again)")
    a = ap.parse_args(argv)
    if len(a.lab) != len(a.scans):
        sys.exit("give one --scans folder per --lab folder")
    os.makedirs(a.out, exist_ok=True)

    per_lab = []
    if a.moves and os.path.exists(a.moves):
        z = np.load(a.moves, allow_pickle=False)
        for lab_name in [str(n) for n in z["labs"]]:
            names = [str(n) for n in z[f"{lab_name}|frames"]]
            rows = [tuple(z[f"{lab_name}|{i}|{k}"] for k in "hdwv")
                    for i in range(len(names))]
            per_lab.append((lab_name, [(n.split("~")[0], n.split("~")[1], None, None)
                                       for n in names], rows))
        log(f"read the measured moves from {a.moves}")
    else:
        for lab_dir, scan_dir in zip(a.lab, a.scans):
            log(f"lab {lab_dir}")
            frames = pair_samples(lab_dir, scan_dir, a.single)
            log(f"rendering {len(frames)} frames")
            per_lab.append((os.path.basename(os.path.normpath(lab_dir)), frames,
                            frame_moves(frames)))
        if a.moves:
            store = {"labs": np.array([n for n, _f, _r in per_lab])}
            for name, frames, rows in per_lab:
                store[f"{name}|frames"] = np.array([f"{f[0]}~{f[1]}" for f in frames])
                for i, r in enumerate(rows):
                    for k, arr in zip("hdwv", r):
                        store[f"{name}|{i}|{k}"] = arr
            np.savez_compressed(a.moves, **store)
    cells_by_lab = [of.cells(*stack(rows)) for _n, _f, rows in per_lab]
    all_cells = np.concatenate(cells_by_lab)
    if len(all_cells) < 6:
        sys.exit("too few coloured samples to measure a curve")
    knots = of.fit_curve(all_cells, lam=a.smooth)

    from core.ccr_processor import organics_max_slope, organics_min_slope
    lines = ["Colour Organics: the labs' hue minus FreeCCR's default render",
             f"(mid and bright tones, value >= {of.VALUE_MIN}), degrees", ""]
    zero = np.zeros_like(knots)
    lines.append("RMS by area of picture, and (in brackets) with every cell alike")
    lines.append(f"in sample: {of.curve_rms(knots, all_cells):.2f} "
                 f"({of.curve_rms(knots, all_cells, alike=True):.2f}); no curve: "
                 f"{of.curve_rms(zero, all_cells):.2f} "
                 f"({of.curve_rms(zero, all_cells, alike=True):.2f})")
    if len(per_lab) > 1:
        for i, (name, _f, _r) in enumerate(per_lab):
            rest = np.concatenate([c for j, c in enumerate(cells_by_lab) if j != i])
            kr = of.fit_curve(rest, lam=a.smooth)
            c = cells_by_lab[i]
            lines.append(f"{name} predicted from the others: "
                         f"{of.curve_rms(kr, c):.2f} ({of.curve_rms(kr, c, alike=True):.2f}); "
                         f"no curve: {of.curve_rms(zero, c):.2f} "
                         f"({of.curve_rms(zero, c, alike=True):.2f})")
    lines.append(f"slope round the circle: {1 + organics_min_slope(knots):.2f} .. "
                 f"{1 + organics_max_slope(knots):.2f} at the measured amount")
    lines.append("")
    lines.append("hue   move   (cells: measured median by value band)")
    for k, kv in enumerate(knots):
        h = k * 10.0
        m = np.abs(all_cells[:, 0] - (h + 5.0)) < 1e-6
        meas = ", ".join(f"{x:+.1f}" for x in all_cells[m, 1])
        lines.append(f"{h:5.0f}  {kv:+5.1f}   {meas}")
    frames_used = [f"{name}: " + ", ".join(f"{fr[0]}~{fr[1]}" for fr in frames)
                   for name, frames, _r in per_lab]
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(a.out, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(report + "\n\nFrames:\n" + "\n".join(frames_used) + "\n")
    if a.write:
        path = os.path.join(HERE, "..", "src", "core", "organics_measured.py")
        body = ",\n    ".join(", ".join(f"{v:+.3f}" for v in knots[i:i + 6])
                              for i in range(0, len(knots), 6))
        head = "\n".join(f"#   {ln}".rstrip() for ln in lines[:5 + len(per_lab)])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                '"""Colour Organics\' measured hue curve (spec/colour-organics.md,\n'
                'revision 2026-11-02). Written by tools/fit_organics.py - do not edit\n'
                'by hand; re-run the tool with more pairs instead.\n\n'
                'TABLE[k] is the hue move in HSV degrees at hue 10*k, at the stored\n'
                'amount 100 (shown 50), read by a periodic Catmull-Rom spline."""\n\n'
                f"# Fitted on {sum(len(f) for _n, f, _r in per_lab)} pairs from "
                f"{len(per_lab)} lab(s), smoothness {a.smooth:g}:\n{head}\n\n"
                "HUE_STEP = 10.0\n"
                f"TABLE = (\n    {body},\n)\n")
        log(f"wrote {os.path.normpath(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
