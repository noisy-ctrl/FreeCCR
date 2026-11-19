"""Straighten inside the crop (spec/straighten-in-crop.md): the crop box at
a new angle, kept inside the picture. Qt-free.

The box is in UNROTATED image space as `apply_crop_to_image` reads it:
normalised (x1, y1, x2, y2) about centre C, sampled at C + R(angle) q with R
Qt's clockwise rotate() in y-down pixels."""
import math

FULL = (0.0, 0.0, 1.0, 1.0)


def fit_box(rect, angle, w, h):
    """`rect` (normalised) scaled about its centre by the largest factor <= 1
    that keeps the box, turned by `angle` degrees, inside the w x h picture.
    Its centre and shape are kept. Returns the new normalised rect."""
    x1, y1, x2, y2 = (float(v) for v in rect)
    w, h = float(w), float(h)
    cx, cy = (x1 + x2) / 2.0 * w, (y1 + y2) / 2.0 * h
    bw, bh = abs(x2 - x1) * w, abs(y2 - y1) * h
    t = math.radians(float(angle or 0.0))
    c, s = math.cos(t), math.sin(t)
    k = 1.0
    for qx, qy in ((-bw / 2, -bh / 2), (bw / 2, -bh / 2), (bw / 2, bh / 2), (-bw / 2, bh / 2)):
        dx, dy = c * qx - s * qy, s * qx + c * qy
        if dx > 1e-12:
            k = min(k, (w - cx) / dx)
        elif dx < -1e-12:
            k = min(k, cx / -dx)
        if dy > 1e-12:
            k = min(k, (h - cy) / dy)
        elif dy < -1e-12:
            k = min(k, cy / -dy)
    k = max(k, 1e-3)
    hw, hh = bw * k / 2.0, bh * k / 2.0
    return ((cx - hw) / w, (cy - hh) / h, (cx + hw) / w, (cy + hh) / h)


def corners(rect, angle, w, h):
    """The turned box's corners in pixels (for tests and the overlay)."""
    x1, y1, x2, y2 = rect
    cx, cy = (x1 + x2) / 2.0 * w, (y1 + y2) / 2.0 * h
    bw, bh = (x2 - x1) * w, (y2 - y1) * h
    t = math.radians(float(angle or 0.0))
    c, s = math.cos(t), math.sin(t)
    return [(cx + c * qx - s * qy, cy + s * qx + c * qy)
            for qx, qy in ((-bw / 2, -bh / 2), (bw / 2, -bh / 2),
                           (bw / 2, bh / 2), (-bw / 2, bh / 2))]


def is_full(rect, tol=1e-4):
    return rect is not None and all(abs(a - b) <= tol for a, b in zip(rect, FULL))
