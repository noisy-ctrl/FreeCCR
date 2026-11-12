"""Export on a coloured canvas (a border): the finished frame, centred on a
canvas of a chosen size and colour. See spec/export-canvas-border.md."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

CANVAS_MIN = 16
CANVAS_MAX = 20000
DEFAULT_WIDTH = 2000
DEFAULT_HEIGHT = 2000
DEFAULT_LONG_EDGE = 1946
DEFAULT_COLOUR = (255, 255, 255)


@dataclass(frozen=True)
class Canvas:
    """Canvas size in px, the frame's long edge in px, and the background as
    an 8-bit sRGB triple."""
    width: int
    height: int
    long_edge: int
    colour: Tuple[int, int, int] = DEFAULT_COLOUR
    # The least border round the frame, px (0 = the frame may touch the
    # canvas's edges, as before), and screen sharpening applied to the frame
    # after its resize as (amount, radius) in the Details sliders' units
    # ((0, 0) = none). spec/instagram-export.md
    margin: int = 0
    sharpen: Tuple[int, int] = (0, 0)

    @property
    def neutral(self) -> bool:
        r, g, b = self.colour
        return r == g == b


def colour_to_hex(colour) -> str:
    r, g, b = (int(c) for c in colour)
    return f"#{r:02x}{g:02x}{b:02x}"


def colour_from_hex(text, default=DEFAULT_COLOUR):
    s = str(text or "").strip().lstrip("#")
    if len(s) != 6:
        return tuple(default)
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return tuple(default)


def placed_size(src_w: int, src_h: int, canvas: Canvas) -> Tuple[int, int]:
    """(w, h) of the frame on the canvas: its long edge at `long_edge`, then
    shrunk to fit if either side would overflow. Never cropped, never 0."""
    src_w, src_h = max(1, int(src_w)), max(1, int(src_h))
    m = max(0, int(canvas.margin or 0))
    box_w = max(1, canvas.width - 2 * m)
    box_h = max(1, canvas.height - 2 * m)
    s = float(canvas.long_edge) / max(src_w, src_h)
    s = min(s, box_w / src_w, box_h / src_h)
    w = max(1, min(box_w, int(round(src_w * s))))
    h = max(1, min(box_h, int(round(src_h * s))))
    return w, h


def borders(src_w: int, src_h: int, canvas: Canvas) -> Tuple[int, int, int, int]:
    """(left, right, top, bottom) border in px for a frame of this size,
    exactly as compose() places it (odd remainders go right/bottom)."""
    w, h = placed_size(src_w, src_h, canvas)
    left, top = (canvas.width - w) // 2, (canvas.height - h) // 2
    return left, canvas.width - w - left, top, canvas.height - h - top


def frame_shape(img) -> Optional[Tuple[int, int]]:
    """(w, h) of the frame an image will export - its crop, turned by its
    90-degree rotation - at preview resolution. Only the SHAPE matters for
    the border (placed_size rescales to the long edge), so the preview's
    size is enough. None when unknown."""
    base = getattr(img, "resized_raw", None)
    if base is None:
        return None
    H, W = base.shape[:2]
    rect = getattr(img, "crop_rect", None)
    if rect:
        fx1, fy1, fx2, fy2 = rect
        w, h = (fx2 - fx1) * W, (fy2 - fy1) * H
    else:
        w, h = float(W), float(H)
    if w < 2 or h < 2:
        return None
    if int(getattr(img, "rotation_angle", 0) or 0) % 180 == 90:
        w, h = h, w
    # Express at a fixed high resolution so rounding matches a full export.
    k = 6000.0 / max(w, h)
    return max(1, int(round(w * k))), max(1, int(round(h * k)))


def describe_borders(shapes, canvas: Canvas) -> str:
    """The Export window's border readout for a list of frame (w, h): px and
    % of the canvas side, as a range when the frames differ."""
    shapes = [s for s in shapes if s]
    if not shapes:
        return ""
    b = [borders(w, h, canvas) for w, h in shapes]
    sides = [min(l, r) for l, r, _, _ in b] + [max(l, r) for l, r, _, _ in b]
    tops = [min(t, u) for _, _, t, u in b] + [max(t, u) for _, _, t, u in b]

    def span(vals, total):
        lo, hi = min(vals), max(vals)
        pl, ph = 100.0 * lo / total, 100.0 * hi / total
        if hi - lo <= 1:          # a centring remainder is not a difference
            return f"{lo} px ({pl:.1f}%)"
        return f"{lo}\u2013{hi} px ({pl:.1f}\u2013{ph:.1f}%)"

    text = (f"Border: sides {span(sides, canvas.width)}, "
            f"top/bottom {span(tops, canvas.height)}")
    longest = max(max(placed_size(w, h, canvas)) for w, h in shapes)
    if longest < canvas.long_edge:
        from dataclasses import replace as _replace
        bare = _replace(canvas, margin=0)
        without = max(max(placed_size(w, h, bare)) for w, h in shapes)
        why = ("the border width" if canvas.margin and without > longest
               else "the canvas size")
        text += (f"\nThe image comes out {longest} px on its long edge, "
                 f"not {canvas.long_edge}: {why} limits it.")
    if len(shapes) > 1 and (max(sides) - min(sides) > 1 or max(tops) - min(tops) > 1):
        text += ("\nVaries because these frames' crops differ in shape; "
                 "the long edge is the same on all of them.")
    return text


def _resize(img: np.ndarray, w: int, h: int, interp_down=cv2.INTER_AREA,
            interp_up=cv2.INTER_LANCZOS4) -> np.ndarray:
    ih, iw = img.shape[:2]
    if (iw, ih) == (w, h):
        return img
    interp = interp_down if (w <= iw and h <= ih) else interp_up
    return cv2.resize(img, (w, h), interpolation=interp)


def compose(rgb_u16: np.ndarray, canvas: Canvas,
            over: Optional[np.ndarray] = None):
    """Centre the frame on the canvas. Returns (canvas_rgb_u16, canvas_over):
    the picture on a background of ``canvas.colour`` (8-bit sRGB × 257), and
    the HDR map resized the same way and padded with 0 so the border never
    glows (None when there is no map). The inputs are not modified."""
    ih, iw = rgb_u16.shape[:2]
    w, h = placed_size(iw, ih, canvas)
    frame = _resize(rgb_u16, w, h)
    if frame.dtype != np.uint16:
        frame = np.clip(frame, 0, 65535).astype(np.uint16)
    amount, radius = (canvas.sharpen or (0, 0))
    if amount and radius:
        # Screen sharpening at the output size, on the frame only, so the
        # border's edge gets no halo (spec/instagram-export.md).
        from core.ccr_processor import apply_sharpening
        frame = apply_sharpening(frame, float(amount), float(radius), 0.0, 1.0)
    x0 = (canvas.width - w) // 2
    y0 = (canvas.height - h) // 2
    channels = rgb_u16.shape[2] if rgb_u16.ndim == 3 else 1
    if channels == 1:
        out = np.full((canvas.height, canvas.width),
                      int(canvas.colour[0]) * 257, np.uint16)
        out[y0:y0 + h, x0:x0 + w] = frame if frame.ndim == 2 else frame[..., 0]
    else:
        fill = np.array([int(c) * 257 for c in canvas.colour], np.uint16)
        out = np.empty((canvas.height, canvas.width, channels), np.uint16)
        out[...] = fill[:channels]
        out[y0:y0 + h, x0:x0 + w] = frame
    over_out = None
    if over is not None:
        o = _resize(np.asarray(over, np.float32), w, h,
                    interp_up=cv2.INTER_AREA)
        over_out = np.zeros((canvas.height, canvas.width), np.float32)
        over_out[y0:y0 + h, x0:x0 + w] = o
    return out, over_out
