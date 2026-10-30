#!/usr/bin/env python3
"""How much of each picture clips at conversion, for each clipping mode
(spec/convert-crop-and-clip.md §2). Not a test: a report on real scans.

    python tools/measure_conversion_clipping.py FILE_OR_FOLDER [...]

Folders are planned into trichrome sets (core.triplet_plan) when they hold
colour-lit RAWs; other files convert singly. For each frame and mode it prints
the clipped share (any channel at 0 / 255 after a 3x3 median, inside the
picture), the median luma of the picture, and the guard's renders/time."""
import glob
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from core.catalog import create_images_for_merge  # noqa: E402
from core.clip_guard import MODES, clip_counts, _area  # noqa: E402
from core.triplet_plan import plan_triplets  # noqa: E402


def jobs(args):
    out = []
    for a in args:
        if os.path.isdir(a):
            files = sorted(glob.glob(os.path.join(a, "*.[Rr][Aa][Ff]")))
            plan = plan_triplets(files)
            out += [("merge", list(s)) for s in plan.sets] or [("single", [f]) for f in files]
        else:
            out.append(("single", [a]))
    return out


def main(argv):
    ccr_backend.crop_after_convert = False
    for kind, paths in jobs(argv):
        name = os.path.basename(paths[0])
        for mode in MODES:
            ccr_backend.clip_mode = mode
            ccr_backend.images.clear()
            img = (create_images_for_merge(paths)[0] if kind == "merge"
                   else CCRImage(paths[0]))
            ccr_backend.images.append(img)
            plan = ccr_backend.plan_frame_conversion(img)
            if plan["black"] is None:
                print(f"{name:30s} no film base found: skipped")
                break
            t0 = time.time()
            ccr_backend.apply_frame_conversion(img, plan)
            dt = time.time() - t0
            ci = img.conversion_inputs
            rect = ci.get("picture")
            out = img.apply_adjustments(img.resized_raw)
            b, w, n = clip_counts(out, rect)
            p8 = _area(cv2.convertScaleAbs(out, alpha=255 / 65535), rect)
            luma = float(np.median(p8.reshape(-1, p8.shape[-1]).mean(axis=1)))
            g = ci.get("clip_guard") or {}
            print(f"{name:30s} {mode:9s} black {100 * b / n:6.3f}%  white {100 * w / n:6.3f}%  "
                  f"median {luma:5.1f}  renders {g.get('renders', 0):2d}  convert {dt:4.1f}s")


if __name__ == "__main__":
    main(sys.argv[1:] or ["/mnt/user-data/uploads/Light unbalanced"])
