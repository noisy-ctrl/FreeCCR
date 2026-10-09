#!/usr/bin/env python3
"""Time FreeCCR's preview render on this machine.

    python3.11 tools/bench_render.py DSCF1610.RAF DSCF1611.RAF DSCF1612.RAF
    python3.11 tools/bench_render.py frame.RAF

Three files are treated as one RGB-light triplet (FreeCCR's 3-way merge), one
file as a single scan. The frame is converted the way Convert does it with
Auto black point, then one slider-move render (the live preview, 1080 px) is
timed through:

  * every OpenCL device this machine has (the app uses device 0 by default),
  * the numpy path (FREECCR_OPENCL=0),
  * a half-resolution draft (what a slow machine shows mid-drag),

followed by the slowest stages of the numpy render. Each path's output is
compared with the numpy one; "max diff" is in 16-bit units (257 = one step of
an 8-bit display value). Paste the whole output back to diagnose a slow
machine. See spec/slider-speed.md.
"""
import cProfile
import io
import os
import platform
import pstats
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

REPEAT = 7


def _median_ms(fn, repeat=REPEAT):
    fn()                                       # warm-up (kernel build, caches)
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return float(np.median(ts))


def main(argv):
    files = [a for a in argv if not a.startswith("-")]
    if len(files) not in (1, 3):
        print(__doc__)
        return 2
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from core import ccr_processor as cp
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from core.auto_black_point import find_rebate
    from widgets.sliders_panel import SlidersPanel

    print(f"Python {platform.python_version()} on {platform.platform()} "
          f"({platform.machine()}, {os.cpu_count()} CPUs)")
    print(f"numpy {np.__version__}, OpenCV {cv2.__version__}, "
          f"PyOpenCL {'yes' if cp.OPENCL_AVAILABLE else 'no'}")

    t0 = time.perf_counter()
    if len(files) == 3:
        img = CCRImage(files[0], is_merged=True, merge_sources=files,
                       merge_demosaic=True)
    else:
        img = CCRImage(files[0])
    print(f"decode: {(time.perf_counter() - t0):.1f} s")
    rebate = find_rebate(img.resized_raw)
    bp = rebate.black_point
    img.resized_raw = cp.ccr_normalize_with_bwpoint(img, bp, None)
    img.converted = True
    img.conversion_inputs = {"mode": "bw",
                             "bw": (tuple(bp) if bp is not None else None, None),
                             "fine_rot": 0, "density": False, "slopes": None}
    s = {k: SlidersPanel.SLIDER_DEFAULTS.get(k, 0) for k in SlidersPanel.ADJUSTMENT_KEYS}
    s["cineon_log"] = True
    s["contrast"] = 10
    img.adjustment_settings = s
    ccr_backend.images = [img]
    h, w = img.resized_raw.shape[:2]
    print(f"preview base: {w}x{h}, black point "
          f"{'auto' if bp is not None else 'none (no-anchor)'}\n")

    def render():
        return img.render_preview_pixels()["preview8"]

    results = {}
    # numpy path
    os.environ["FREECCR_OPENCL"] = "0"
    cp.cleanup_opencl()
    ref = render()
    results["numpy"] = (_median_ms(render), 0)
    # every OpenCL device
    os.environ["FREECCR_OPENCL"] = "1"
    n_dev = 0
    if cp.OPENCL_AVAILABLE:
        try:
            n_dev = sum(len(p.get_devices()) for p in cp.cl.get_platforms())
        except Exception as e:
            print(f"OpenCL: {e}")
    for i in range(n_dev):
        os.environ["FREECCR_OPENCL_DEVICE"] = str(i)
        cp.cleanup_opencl()
        try:
            if not cp._initialize_opencl():
                continue
            out = render()
            diff = int(np.abs(img.apply_adjustments(img.resized_raw).astype(np.int32)
                              - _numpy_full(img, cp).astype(np.int32)).max())
            results[f"OpenCL {i}: {cp._opencl_cache['device_name']}"] = (
                _median_ms(render), diff)
            del out
        except Exception as e:
            print(f"OpenCL device {i} failed: {e}")
    os.environ.pop("FREECCR_OPENCL_DEVICE", None)
    cp.cleanup_opencl()
    cp._initialize_opencl()

    draft_long = max(h, w) // 2
    results[f"draft ({draft_long} px, default engine)"] = (
        _median_ms(lambda: img.render_preview_pixels(draft_long=draft_long)), None)

    print(f"{'one slider-move render':44s} {'median':>9s}  max diff vs numpy")
    for name, (ms, diff) in results.items():
        d = "" if diff is None else f"{diff}"
        print(f"  {name[:42]:42s} {ms:7.0f} ms  {d}")

    # Where the time goes, numpy path (the stages run in numpy either way).
    os.environ["FREECCR_OPENCL"] = "0"
    cp.cleanup_opencl()
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(3):
        render()
    pr.disable()
    out = io.StringIO()
    pstats.Stats(pr, stream=out).sort_stats("tottime").print_stats(12)
    print("\nslowest stages (numpy path, 3 renders, tottime):")
    keep = False
    for line in out.getvalue().splitlines():
        if line.strip().startswith("ncalls"):
            keep = True
        if keep and line.strip():
            print("  " + line.strip()[:110])
    del ref
    return 0


def _numpy_full(img, cp):
    """The 16-bit render on the numpy path, for the parity check."""
    saved = cp._initialize_opencl
    cp._initialize_opencl = lambda: False
    try:
        return img.apply_adjustments(img.resized_raw)
    finally:
        cp._initialize_opencl = saved


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
