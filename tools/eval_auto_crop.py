#!/usr/bin/env python3
"""Run Auto crop (spec/auto-crop.md) over a folder of scans and write contact
sheets for checking it by eye.

    python3.11 tools/eval_auto_crop.py "<folder of RAWs>" --out "<folder>"
    python3.11 tools/eval_auto_crop.py a.RAF b.RAF c.RAF --merge   # trichrome

Writes:
    auto_crop_frames.jpg   each scan (log view) with the detected crop drawn
    auto_crop_crops.jpg    each crop as a rough positive: border left in shows
                           up as a dark or white line along an edge
    auto_crop_report.txt   one line per scan: confidence, format, angle, notes

--merge groups the files in threes (red, green, blue) like FreeCCR's 3-way
merge. .npy files of a raw preview are accepted too (development).
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

RAW_EXTS = (".raf", ".nef", ".cr2", ".cr3", ".arw", ".dng", ".orf", ".rw2",
            ".pef", ".srw", ".tif", ".tiff")


def _load(paths, merge):
    from core.ccr_image import CCRImage
    if paths[0].lower().endswith(".npy"):
        return np.load(paths[0])
    img = (CCRImage(paths[0], is_merged=True, merge_sources=list(paths), merge_demosaic=True)
           if merge else CCRImage(paths[0]))
    return img.resized_raw


def _sheet(tiles, cols=6, bg=60):
    h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 6, cv2.BORDER_CONSTANT,
                                value=(bg, bg, bg)) for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], bg))
    return np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    files = []
    for p in a.inputs:
        if os.path.isdir(p):
            files += sorted(f for f in glob.glob(os.path.join(p, "*"))
                            if f.lower().endswith(RAW_EXTS + (".npy",)))
        else:
            files.append(p)
    groups = ([files[i:i + 3] for i in range(0, len(files) - 2, 3)] if a.merge
              else [[f] for f in files])
    os.makedirs(a.out, exist_ok=True)
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from core.auto_crop import detect_frame, log_luminance, _rotate
    from core.ccr_processor import apply_crop_to_image

    frames, crops, lines = [], [], []
    for g in groups:
        name = os.path.splitext(os.path.basename(g[0]))[0]
        raw = _load(g, a.merge)
        fc = detect_frame(raw)
        lines.append(f"{name:28s} {fc.confidence:6s} {fc.summary()}"
                     + (f"  [{fc.reason}]" if fc.reason else ""))
        print(lines[-1], flush=True)
        L = log_luminance(raw)
        lo, hi = np.percentile(L, 1), np.percentile(L, 99.5)
        v = (np.clip((L - lo) / max(hi - lo, 1e-3), 0, 1) * 255).astype(np.uint8)
        vis = cv2.cvtColor(v, cv2.COLOR_GRAY2BGR)
        colour = {"high": (0, 200, 0), "medium": (0, 200, 255)}.get(fc.confidence, (0, 0, 255))
        if fc.rect is not None:
            hh, ww = L.shape
            x1, y1, x2, y2 = fc.rect
            box = cv2.boxPoints((((x1 + x2) / 2 * ww, (y1 + y2) / 2 * hh),
                                 ((x2 - x1) * ww, (y2 - y1) * hh), fc.angle))
            cv2.polylines(vis, [np.int32(box)], True, colour, 2)
            c = log_luminance(apply_crop_to_image(raw, fc.rect, fc.angle))
            clo, chi = np.percentile(c, 0.5), np.percentile(c, 99.5)
            pos = ((1 - np.clip((c - clo) / max(chi - clo, 1e-3), 0, 1)) ** 0.8 * 255).astype(np.uint8)
            ct = cv2.cvtColor(pos, cv2.COLOR_GRAY2BGR)
        else:
            ct = np.full((200, 300, 3), 40, np.uint8)
        for img, lst in ((vis, frames), (ct, crops)):
            t = cv2.resize(img, (380, max(1, int(380 * img.shape[0] / img.shape[1]))),
                           interpolation=cv2.INTER_AREA)
            t = cv2.copyMakeBorder(t, 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=colour)
            cv2.putText(t, f"{name} {fc.confidence}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, colour, 2)
            lst.append(t)
    cv2.imwrite(os.path.join(a.out, "auto_crop_frames.jpg"), _sheet(frames),
                [cv2.IMWRITE_JPEG_QUALITY, 82])
    cv2.imwrite(os.path.join(a.out, "auto_crop_crops.jpg"), _sheet(crops),
                [cv2.IMWRITE_JPEG_QUALITY, 82])
    with open(os.path.join(a.out, "auto_crop_report.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
