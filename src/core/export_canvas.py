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
    s = float(canvas.long_edge) / max(src_w, src_h)
    s = min(s, canvas.width / src_w, canvas.height / src_h)
    w = max(1, min(canvas.width, int(round(src_w * s))))
    h = max(1, min(canvas.height, int(round(src_h * s))))
    return w, h


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
