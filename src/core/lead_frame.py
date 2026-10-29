"""Is a tethered capture lit? (spec/tether-black-point.md)

The roll's black point is sampled from the film base of a capture, so a frame
shot with the light off can't give one. `lit_level` measures the brightest
part of a capture's decoded, unconverted data; `is_lit` says whether it could
show film base. Measured on the user's RAFs: the dimmest lit frame is several
times above LIT_MIN; a frame with the light off is black level plus noise.
"""
import numpy as np

LIT_MIN = 0.03         # brightest 0.5 % of the frame, as a share of full scale
PERCENTILE = 99.5


def lit_level(img):
    """The 99.5th percentile of the brightest channel of `img.resized_raw`
    (every 2nd pixel), as a share of full scale; None when there's no data."""
    data = getattr(img, "resized_raw", None)
    if data is None:
        return None
    a = np.asarray(data)
    if a.size == 0:
        return None
    if a.dtype == np.uint16:
        scale = 65535.0
    elif a.dtype == np.uint8:
        scale = 255.0
    else:
        scale = 1.0
    m = a.max(axis=2) if a.ndim == 3 else a
    m = m[::2, ::2] if m.ndim == 2 and min(m.shape) >= 4 else m
    return float(np.percentile(m, PERCENTILE)) / scale


def is_lit(level):
    """Could this frame show film base? An unmeasured one (None) counts as
    lit, so a measuring failure never blocks the prompt."""
    return level is None or level >= LIT_MIN
