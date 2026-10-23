#!/usr/bin/env python3
"""Time the finishing tools (Golden Highlights, Shadow Neutralise, Glow) on
this machine: the numpy versions against the GPU ones.

    python3.11 tools/bench_finishing.py            # a synthetic picture
    python3.11 tools/bench_finishing.py scan.jpg   # any picture OpenCV reads

For each tool, and all three together, at 1080 / 2000 / 4000 px on the long
side: the median time of each, and the largest difference between them in
16-bit codes (257 = one step of an 8-bit display value). The GPU column is
the GPU finishing on its own, from the main kernel's buffer to the end,
which is what it adds to a render. Paste the whole output back to compare.
See spec/gpu-finishing-tools.md.
"""
import os
import platform
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

REPEAT = 5


def _median_ms(fn, repeat=REPEAT):
    fn()
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return float(np.median(ts))


def _picture(path):
    if path:
        im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if im is None:
            raise SystemExit(f"can't read {path}")
        if im.ndim == 2:
            im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
        im = im[..., :3][..., ::-1]
        return im.astype(np.float32) / (65535.0 if im.dtype == np.uint16 else 255.0)
    rng = np.random.default_rng(0)
    H, W = 2000, 3000
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    im = np.stack([0.5 + 0.4 * yy / H, 0.55 + 0.35 * yy / H, 0.75 + 0.2 * yy / H], -1)
    im[H // 3:, : W // 2] = (0.90, 0.84, 0.70)
    im[2 * H // 3:, W // 2:] = (0.07, 0.10, 0.11)
    im[np.hypot(yy - H * 0.25, xx - W * 0.8) < 60] = (1.0, 0.95, 0.85)
    return np.clip(im + rng.normal(0, 0.008, im.shape).astype(np.float32), 0, 1)


def main(argv):
    from core import ccr_processor as cp
    from core import finish_gpu as fg
    from core.golden import apply_golden
    from core.shadow_neutral import apply_shadow_neutralise
    from core.glow import apply_glow

    print(f"Python {platform.python_version()} on {platform.platform()} "
          f"({platform.machine()}, {os.cpu_count()} CPUs)")
    if not cp.OPENCL_AVAILABLE or not cp._initialize_opencl():
        print("No OpenCL here: the app runs the numpy versions.")
        return 1
    ctx, queue = cp._opencl_cache['ctx'], cp._opencl_cache['queue']
    dev = ctx.devices[0]
    used = fg.enabled_for(dev)
    print(f"OpenCL device: {dev.name.strip()} ({cp._device_kind(dev)}); the app "
          f"{'uses' if used else 'does NOT use'} the GPU finishing on it"
          f"{'' if used else ' (it does only on a GPU)'}\n")
    g = fg.for_queue(ctx, queue)
    import pyopencl.array as cla

    src = _picture(argv[0] if argv else None)
    cases = [("Golden Highlights", dict(golden=60), lambda x: apply_golden(x, 60)),
             ("Shadow Neutralise", dict(shadow=60), lambda x: apply_shadow_neutralise(x, 60)),
             ("Glow", dict(glow=60), lambda x: apply_glow(x, 60)),
             ("all three", dict(golden=60, shadow=60, glow=60),
              lambda x: apply_glow(apply_shadow_neutralise(apply_golden(x, 60), 60), 60))]
    print(f"{'':20s} {'long side':>9s} {'numpy':>9s} {'GPU':>9s}  max diff")
    for long_side in (1080, 2000, 4000):
        h, w = src.shape[:2]
        s = long_side / max(h, w)
        x = cv2.resize(src, (max(1, round(w * s)), max(1, round(h * s))),
                       interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        x = np.clip(x, 0, 1).astype(np.float32)
        H, W = x.shape[:2]
        base = cla.to_device(queue, (x * np.float32(65535.0)).reshape(-1, 3))
        for name, kw, cpu in cases:
            buf = base.copy()

            def run_gpu():
                buf[:] = base
                g.finish(buf, H, W, **kw)
                queue.finish()
            t_gpu = _median_ms(run_gpu)
            t_cpu = _median_ms(lambda: cpu(x), repeat=3)
            diff = np.abs(cpu(x).astype(np.float64) * 65535
                          - buf.get().reshape(H, W, 3).astype(np.float64)).max()
            print(f"{name:20s} {long_side:9d} {t_cpu:7.0f} ms {t_gpu:7.0f} ms  {diff:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
