"""Film Look auto balance per frame (spec/film-look-auto-balance.md part 1).

A lab scanner balances every frame's colour by itself. Measured on the user's
nine Portra lab frames, the lab's colour correction follows the frame's mean
density colour at about half strength (correlation 0.95):

    colour offsets = alpha * (T - colour(mean density over the picture))

where colour(v) = v - mean(v) (so the offsets sum to 0: overall density, the
brightness, is left alone) and T, alpha belong to the lab rendering the look
was fitted from (stored in its cube, `look_lut.BALANCE_MARK`). Qt-free.
"""
import numpy as np

from core.look_lut import ALPHA_MAX, ALPHA_MIN

STRIDE = 4                 # every 4th pixel each way: the mean of a smooth frame
D_LO, D_HI = -0.25, 1.25   # density inside the look's domain (holder, holes out)
MIDDLE = (0.1, 0.1, 0.9, 0.9)   # the picture when nothing better is known


def colour(v):
    v = np.asarray(v, np.float64)
    return v - v.mean(axis=-1, keepdims=True)


def picture_rect(img):
    """The area to measure on (normalised x1, y1, x2, y2): the crop, else the
    picture Convert measured in, else the middle 80 %."""
    rect = getattr(img, "crop_rect", None)
    if rect is None:
        rect = (getattr(img, "conversion_inputs", None) or {}).get("picture")
    return tuple(float(v) for v in rect) if rect is not None else MIDDLE


def picture_mean_density(base, rect=None, look=None, windowed=True):
    """Per-channel mean of the look's input density over `rect` of a
    converted base (uint16; windowed working-space or full-range), BEFORE any
    Channel Levels: the de-windowed value through the look's input map
    (`FrameLook.map_input`, revision 6). Pixels outside D_LO..D_HI in any
    channel are left out. None when nothing is left."""
    from core.ccr_processor import WS_B, _WS_INV_WIDTH
    b = np.asarray(base)
    if b.ndim != 3 or b.shape[2] < 3:
        return None
    h, w = b.shape[:2]
    x1, y1, x2, y2 = rect if rect is not None else MIDDLE
    ys = slice(max(0, int(y1 * h)), max(1, min(h, int(round(y2 * h)))))
    xs = slice(max(0, int(x1 * w)), max(1, min(w, int(round(x2 * w)))))
    o = STRIDE // 2                          # centred: no bias on a gradient
    d = b[ys, xs, :3][o::STRIDE, o::STRIDE].astype(np.float32)
    if windowed:
        d -= np.float32(WS_B)
        d *= np.float32(_WS_INV_WIDTH)
    else:
        d /= np.float32(65535.0)
    m = getattr(look, "map_input", None) if look is not None else None
    if m is not None:
        d = m(d)
    d = d.reshape(-1, 3)
    keep = np.all((d >= D_LO) & (d <= D_HI), axis=1)
    if keep.sum() < 16:
        return None
    return d[keep].astype(np.float64).mean(axis=0)


def balance_offsets(mean_density, balance):
    """Sum-zero per-channel density offsets for a frame, or None."""
    if mean_density is None or balance is None:
        return None
    t, a = balance
    off = float(a) * (np.asarray(t, np.float64) - colour(mean_density))
    return off - off.mean()


def fit_balance(means, offsets):
    """(T, alpha) from frames' mean densities and the Channel Levels offsets
    that matched the lab (offsets only): least squares of
    colour(offsets) = alpha*T - alpha*colour(mean) over every frame and
    channel (unknowns alpha*T and alpha), alpha kept within
    ALPHA_MIN..ALPHA_MAX and T refitted for a kept alpha."""
    S = colour(np.asarray(means, np.float64))
    Q = colour(np.asarray(offsets, np.float64))
    if S.ndim != 2 or len(S) < 2:
        return None
    rows = []
    for s in S:
        for c in range(3):
            r = np.zeros(4)
            r[c], r[3] = 1.0, -s[c]
            rows.append(r)
    sol = np.linalg.lstsq(np.array(rows), Q.ravel(), rcond=None)[0]
    a = float(min(max(sol[3], ALPHA_MIN), ALPHA_MAX))
    t = (Q + a * S).mean(axis=0) / a            # the best T for that alpha
    return t - t.mean(), a
