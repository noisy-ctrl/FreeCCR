#!/usr/bin/env python3
"""Fit a Film Look (.cube) from lab scans + your own scans of the same negatives.

    python3.11 tools/fit_look.py --lab "<lab scans folder>" \\
        --scans "<your RAW folder>" --name "Noritsu Lomo 800" [--install]

--scans holds RGB-light triplets (grouped in threes by file name, red-green-
blue, exactly like FreeCCR's 3-way merge) or, with --single, one RAW per frame.
--lab holds the lab's JPEG/TIFF files; each scan is paired with its lab file
automatically (feature matching, mirror/rotation handled). Frames without a
lab match, and lab files without a scan, are simply skipped.

Writes into --out (default: a folder named after --name):
    <name>.cube         the look: FreeCCR Film Look / any app with DOMAIN support
    report.txt / .json  per-frame accuracy and the Channel Levels values that
                        reproduce each lab scan
    compare_*.jpg       lab scan | your scan through the look
--install also copies the .cube into FreeCCR's looks library so it appears in
Channel Levels > Film Look.

See spec/film-look-lut.md.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core import look_fit as lf  # noqa: E402
from core import look_lut  # noqa: E402

RAW_EXTS = (".raf", ".nef", ".cr2", ".cr3", ".arw", ".dng", ".orf", ".rw2", ".pef", ".srw")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def decode(paths):
    """FreeCCR's own negative decode (camera profile None, half size), as
    float RGB 0..1. A triplet goes through the app's 3-way merge."""
    from core.ccr_image import CCRImage
    img = CCRImage.__new__(CCRImage)
    img.source_ops = []
    img.is_merged = len(paths) == 3
    img.merge_sources = list(paths) if img.is_merged else None
    img.merge_demosaic = True
    img.merge_mono = False
    rgb = img.read_image(paths[0], preview=True, positive_override=False,
                         apply_input_icc=False)
    if rgb is None:
        raise ValueError(f"could not decode {paths}")
    return rgb.astype(np.float32) / 65535.0


def group_scans(folder, single):
    from core import ccr_merge
    raws = [os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(RAW_EXTS) and not f.startswith(".")]
    raws = ccr_merge.sort_for_merge(raws)
    if single:
        return [[p] for p in raws]
    if len(raws) % 3:
        log(f"warning: {len(raws)} RAW files is not a multiple of 3 - the last "
            f"{len(raws) % 3} are ignored (use --single for one RAW per frame)")
    return [list(t) for t in ccr_merge.group_into_triplets(raws)]


def to_u8(img):
    return (np.clip(img, 0, 1)[..., ::-1] * 255 + 0.5).astype(np.uint8)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", required=True, help="folder of lab scans (JPEG/TIFF)")
    ap.add_argument("--scans", required=True, help="folder of your RAW scans")
    ap.add_argument("--name", required=True, help='look name, e.g. "Noritsu Portra 400"')
    ap.add_argument("--out", help="output folder (default: ./<name>)")
    ap.add_argument("--single", action="store_true",
                    help="one RAW per frame instead of RGB triplets")
    ap.add_argument("--size", type=int, default=33, help="cube size (default 33)")
    ap.add_argument("--smooth", type=float, default=1.0,
                    help="smoothness of the 3D part (higher = smoother; default 1)")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip the leave-one-out accuracy check (faster)")
    ap.add_argument("--install", action="store_true",
                    help="copy the .cube into FreeCCR's looks library")
    a = ap.parse_args(argv)

    out = a.out or os.path.join(os.getcwd(), a.name)
    os.makedirs(out, exist_ok=True)

    lab_paths = lf.list_lab_scans(a.lab)
    if not lab_paths:
        sys.exit(f"no JPEG/TIFF/PNG files in {a.lab}")
    log(f"reading {len(lab_paths)} lab scans")
    labs = [lf.prepare_lab(lf.read_lab_scan(p)) for p in lab_paths]

    groups = group_scans(a.scans, a.single)
    if not groups:
        sys.exit(f"no RAW files in {a.scans}")
    pairs = {}
    for paths in groups:
        names = ", ".join(os.path.basename(p) for p in paths)
        log(f"decoding {names}")
        v = decode(paths)
        bp, side, bands = lf.rebate_black_point(v)
        mask = lf.image_area_mask(v, bands)
        vmax = float(v.max())
        clipped = float((v[mask] >= vmax - 0.002).mean()) if vmax > 0.9 else 0.0
        d = lf.density(v, bp).astype(np.float32)
        reg = lf.register(d, mask, labs)
        if reg is None or reg["inliers"] < 40:
            log(f"  no lab scan matches {names} - skipped")
            continue
        li = reg["index"]
        lab_name = os.path.basename(lab_paths[li])
        if li in pairs and pairs[li]["inliers"] >= reg["inliers"]:
            log(f"  {lab_name} already paired with a better match - skipped")
            continue
        lab_small = labs[li][0]
        warped, mw = lf.warp_to_lab(reg, lab_small)
        dS, tS = lf.sample_blocks(warped, mw, lab_small)
        log(f"  -> {lab_name} ({reg['orient']}, {reg['inliers']} matches, "
            f"{reg['reproj_px']:.2f} px), black point from {side} rebate "
            f"{np.round(bp, 3).tolist()}, {len(dS)} samples")
        if clipped > 0.005:
            log(f"  warning: {clipped:.1%} of the image is clipped in at least "
                f"one channel - expose that channel shorter")
        pairs[li] = dict(scans=[os.path.basename(p) for p in paths], lab=lab_name,
                         orient=reg["orient"], inliers=reg["inliers"],
                         reproj_px=round(reg["reproj_px"], 2),
                         black_point=[round(float(x), 4) for x in bp],
                         black_point_side=side, clipped=round(clipped, 4),
                         d=dS, t=tS, warped=warped, mask=mw)
    keys = sorted(pairs)
    if len(keys) < 2:
        sys.exit("need at least two matched pairs to fit a look")
    P = [pairs[k] for k in keys]

    bps = np.array([p["black_point"] for p in P])
    drift = np.abs(bps / np.median(bps, 0) - 1).max(0)
    if drift.max() > 0.15:
        log("note: the light level changed between frames by up to "
            + ", ".join(f"{c} {x:.0%}" for c, x in zip("RGB", drift))
            + " (same film base) - the per-frame black point absorbs it, but a "
            "steady light lets one black point serve a whole roll")

    np.savez_compressed(os.path.join(out, "samples.npz"),
                        **{f"d{k}": p["d"] for k, p in enumerate(P)},
                        **{f"t{k}": p["t"] for k, p in enumerate(P)})
    frames = [(p["d"], p["t"]) for p in P]
    log(f"fitting the look on {len(frames)} frames")
    look, q = lf.fit_look(frames, lam_s=a.smooth, progress=lambda m: log("  " + m))
    for k, p in enumerate(P):
        e = lf.frame_error(look, p["d"], p["t"], q[k])
        p["q"] = q[k]
        p["in_sample"] = (float(np.median(e)), float(np.percentile(e, 90)))
        p["render_q"], p["render_look"] = q[k], look

    if not a.no_validate and len(frames) >= 3:
        for j, p in enumerate(P):
            log(f"validating: {p['lab']} held out")
            train = [f for k, f in enumerate(frames) if k != j]
            lj, _ = lf.fit_look(train, lam_s=a.smooth)
            qj = lf.fit_frame(lj, p["d"], p["t"])
            e = lf.frame_error(lj, p["d"], p["t"], qj)
            p["held_out"] = (float(np.median(e)), float(np.percentile(e, 90)))
            p["render_q"], p["render_look"] = qj, lj

    cube = os.path.join(out, f"{a.name}.cube")
    look_lut.write_cube(
        cube, look.cube_table(a.size), (lf.DOMAIN[0],) * 3, (lf.DOMAIN[1],) * 3,
        title=a.name,
        comments=[f"FreeCCR Film Look '{a.name}' - fitted by tools/fit_look.py",
                  "Input: FreeCCR density base after Channel Levels "
                  f"(0.8*log10(black point / scan)), domain {lf.DOMAIN[0]}..{lf.DOMAIN[1]}",
                  "Output: sRGB-encoded display values",
                  f"Fitted on: {', '.join(p['lab'] for p in P)}"])
    log(f"wrote {cube}")

    lines = [f"Film Look '{a.name}'", "",
             "Accuracy is CIEDE2000 against the lab scan (1 = just noticeable side",
             "by side). 'Held out' = the look fitted WITHOUT that frame, then only",
             "that frame's Channel Levels set - what you get on a new frame.", ""]
    report = {"name": a.name, "cube": os.path.basename(cube), "frames": []}
    for p in P:
        cl = {k: int(round(v)) for k, v in lf.channel_levels_for(p["q"]).items()}
        ho = p.get("held_out")
        lines.append(f"{p['lab']}  <-  {', '.join(p['scans'])}")
        lines.append(f"  in sample: median {p['in_sample'][0]:.2f}, 90% {p['in_sample'][1]:.2f}"
                     + (f"   held out: median {ho[0]:.2f}, 90% {ho[1]:.2f}" if ho else ""))
        lines.append("  Channel Levels for this frame: Master Shift "
                     f"{cl['ch_master_shift']:+d} | R Shift {cl['ch_r_shift']:+d} Gain "
                     f"{cl['ch_r_gain']:+d} | G Shift {cl['ch_g_shift']:+d} Gain "
                     f"{cl['ch_g_gain']:+d} | B Shift {cl['ch_b_shift']:+d} Gain "
                     f"{cl['ch_b_gain']:+d}")
        lines.append("")
        report["frames"].append({k: p[k] for k in ("lab", "scans", "orient", "inliers",
                                                   "reproj_px", "black_point",
                                                   "black_point_side", "clipped")}
                                | {"in_sample": p["in_sample"], "held_out": ho,
                                   "channel_levels": cl})
        # comparison image: lab | look
        lab_small = labs[keys[P.index(p)]][0]
        H, W = lab_small.shape[:2]
        pred = np.clip(lf.frame_apply(p["render_look"], p["warped"].reshape(-1, 3).astype(float),
                                      p["render_q"]), 0, 1).reshape(H, W, 3)
        pred[~p["mask"]] = 0
        gap = np.ones((H, 8, 3), np.float32)
        comp = np.hstack([lab_small, gap, pred.astype(np.float32)])
        cv2.imwrite(os.path.join(out, f"compare_{os.path.splitext(p['lab'])[0]}.jpg"),
                    to_u8(comp), [cv2.IMWRITE_JPEG_QUALITY, 90])
    with open(os.path.join(out, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("\n".join(lines))

    if a.install:
        name = look_lut.import_look(cube)
        log(f"installed as '{name}' in {look_lut.looks_dir()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
