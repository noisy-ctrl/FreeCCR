"""Where the camera's magnifier looked (spec/live-magnifier.md).

The magnified live view is matched against the last whole-frame one: SIFT
features, ratio test, a RANSAC similarity (scale + shift + a little turn).
The centre of the magnified picture mapped back into the whole frame is the
spot, as fractions of the whole frame. Accepted only with enough inliers and
a scale close to the level's, so a frame that changed in between, or a view
too soft to match (the GFX's maximum), gives None rather than a wrong box.
Qt-free; the monitor runs it on a background thread (~0.1-0.2 s at 1024 px).
"""
import cv2
import numpy as np

MIN_INLIERS = 12
SCALE_TOLERANCE = 0.30       # accepted scale within 30 % of the level's
MAX_SIDE = 1024              # both pictures are matched at most this size


def _grey(img):
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape[:2]
    f = min(1.0, MAX_SIDE / float(max(h, w)))
    if f < 1.0:
        g = cv2.resize(g, (int(round(w * f)), int(round(h * f))), interpolation=cv2.INTER_AREA)
    return g, f


def locate(whole, zoomed, expected_scale):
    """(fx, fy) of the magnified view's centre in the whole frame, or None."""
    if whole is None or zoomed is None:
        return None
    a, fa = _grey(whole)
    b, fb = _grey(zoomed)
    try:
        sift = cv2.SIFT_create(3000)
    except AttributeError:
        return None
    ka, da = sift.detectAndCompute(a, None)
    kb, db = sift.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < MIN_INLIERS or len(kb) < MIN_INLIERS:
        return None
    pairs = cv2.BFMatcher().knnMatch(db, da, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < MIN_INLIERS:
        return None
    pb = np.float32([kb[m.queryIdx].pt for m in good])
    pa = np.float32([ka[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(pb, pa, method=cv2.RANSAC,
                                         ransacReprojThreshold=4.0)
    if M is None or inl is None or int(inl.sum()) < MIN_INLIERS:
        return None
    # M maps the zoomed picture into the whole one: its scale is 1/magnification
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    if scale <= 0:
        return None
    mag = (1.0 / scale) * (fa / fb)
    if abs(mag / float(expected_scale) - 1.0) > SCALE_TOLERANCE:
        return None
    hb, wb = b.shape[:2]
    cx, cy = M @ np.array([wb / 2.0, hb / 2.0, 1.0])
    ha, wa = a.shape[:2]
    fx, fy = cx / wa, cy / ha
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return None
    return float(fx), float(fy)
