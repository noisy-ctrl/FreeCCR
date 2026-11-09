"""The roll's black point from Capture's film base Measure
(spec/film-base-black-point.md).

Measure's accepted shots are loaded exactly as Open loads them (the current
camera profile and merge mode), and the film base patch is read on that
decoded preview (`resized_raw`, B G R), so the black point is in Convert's
own units with no model of the decode in between. Qt-free.
"""
import os

import numpy as np

from core import base_exposure as be

CLIP_SHARE = 0.98          # a median this close to full scale is clipped
MAX_SPREAD = 0.08          # IQR / median, per channel (as Measure's own check)


def load_shots(paths):
    """The accepted shot(s) as FreeCCR's own image: a 3-way merge for R, G, B,
    else the one RAW. Uses the backend's current merge settings, as Open."""
    from core.ccr_image import CCRImage
    from core.ccr_backend import ccr_backend
    paths = [p for p in paths if p]
    if len(paths) == 3:
        return CCRImage(paths[0], is_merged=True, merge_sources=list(paths),
                        merge_demosaic=getattr(ccr_backend, "rgb_merge_demosaic", True),
                        merge_mono=getattr(ccr_backend, "rgb_merge_mono", False))
    if len(paths) == 1:
        return CCRImage(paths[0])
    raise ValueError(f"expected 1 or 3 shots, got {len(paths)}")


def measure(raw, rect):
    """((b, g, r), "") from the patch `rect` (frame fractions x0, y0, x1, y1,
    as Measure read it) of the decoded preview `raw` (H, W, 3, B G R); (None,
    why) when it is clipped or uneven. Read the way Measure reads its patch:
    the largest group of the patch's 4 x 4 cells that agree (all channels
    together), so a patch touching a frame's edge still reads its base."""
    if raw is None or getattr(raw, "ndim", 0) != 3:
        return None, "no picture"
    h, w = raw.shape[:2]
    r0, r1, c0, c1 = be._pixel_box(rect, w, h)
    patch = raw[r0:r1, c0:c1].astype(np.float64)
    if patch.shape[0] < 4 or patch.shape[1] < 4:
        return None, "the patch is too small"
    full = 65535.0 if raw.dtype == np.uint16 else (255.0 if raw.dtype == np.uint8 else 1.0)
    ph, pw = patch.shape[:2]
    ry = np.minimum(np.arange(ph) * 4 // ph, 3)
    rx = np.minimum(np.arange(pw) * 4 // pw, 3)
    cells = (ry[:, None] * 4 + rx[None, :]).reshape(-1)
    px = patch.reshape(-1, 3)
    lum = px.mean(axis=1)
    keep = be._agreeing_cells(lum, cells)
    px = px[keep]
    med = np.median(px, axis=0)
    if np.any(med >= CLIP_SHARE * full):
        return None, "the film base is clipped in the decoded shot"
    if np.any(med <= 0):
        return None, "the film base reads black"
    q75, q25 = np.percentile(px, [75, 25], axis=0)
    spread = (q75 - q25) / med
    if np.any(spread > MAX_SPREAD):
        return None, (f"the patch isn't even in the decoded shot "
                      f"({100 * float(spread.max()):.0f} %)")
    return tuple(float(v) for v in med), ""


def measure_shots(paths, rect, setup, load=None):
    """The film base's black point for the entry, or raises with why: load the
    accepted shots, read the patch, the exposure it was read at."""
    from core.ccr_backend import CCRBackend
    img = (load or load_shots)(paths)
    raw = getattr(img, "resized_raw", None)
    bgr, why = measure(raw, rect)
    if bgr is None:
        raise ValueError(why)
    exp = CCRBackend.image_exposure(img)
    if not exp:
        raise ValueError("the shots carry no exposure")
    return {"bgr": list(bgr), "exposure": float(exp), "setup": dict(setup or {})}


def current_setup(camera, scan_type):
    """What a stored black point must match: camera, camera profile file,
    merge mode, scan type."""
    from core.ccr_backend import ccr_backend
    prof = getattr(ccr_backend, "active_profile_path", None)
    return {"camera": str(camera or ""),
            "profile": os.path.basename(str(prof)) if prof else "",
            "demosaic": bool(getattr(ccr_backend, "rgb_merge_demosaic", True)),
            "mono": bool(getattr(ccr_backend, "rgb_merge_mono", False)),
            "scan_type": str(scan_type or "")}
