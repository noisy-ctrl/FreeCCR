"""What is clipping, as a layer over the picture (spec/histogram-clip-overlay.md).

The same definition as the histogram's corner wedges: an 8-bit channel at
0 (black) or 255 (white) on the pixels displayed. Solid where all three
channels clip, half-strength where one or two do."""
import numpy as np

COLOURS = {"black": (30, 120, 255), "white": (255, 40, 40)}
ALPHA_ALL = 235
ALPHA_SOME = 120
MAX_LONG = 4096


def clipped_channels(rgb8, kind):
    """Per pixel, how many channels sit at the extreme (0..3, uint8)."""
    rgb8 = np.asarray(rgb8)
    v = 0 if kind == "black" else 255
    return (rgb8 == v).sum(axis=-1).astype(np.uint8)


def _max_pool(a, k):
    if k <= 1:
        return a
    h, w = a.shape
    H, W = -(-h // k) * k, -(-w // k) * k
    pad = np.zeros((H, W), a.dtype)
    pad[:h, :w] = a
    return pad.reshape(H // k, k, W // k, k).max(axis=(1, 3))


def clip_rgba(rgb8, kind, max_long=MAX_LONG):
    """An RGBA uint8 layer: the clip colour where pixels clip, else clear.
    Larger than `max_long` on the long side, it is reduced by an integer
    max-pool, so a lone clipped pixel still shows."""
    n = clipped_channels(rgb8, kind)
    k = int(np.ceil(max(n.shape) / float(max_long))) if max(n.shape) > max_long else 1
    n = _max_pool(n, k)
    h, w = n.shape
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = COLOURS[kind]
    out[..., 3] = np.where(n >= 3, ALPHA_ALL, np.where(n > 0, ALPHA_SOME, 0))
    return out


def clipped_fraction(rgb8, kind):
    """(share with all channels clipped, share with any channel clipped)."""
    n = clipped_channels(rgb8, kind)
    if n.size == 0:
        return 0.0, 0.0
    return float(np.mean(n >= 3)), float(np.mean(n > 0))
