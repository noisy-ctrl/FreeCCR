"""Anchored Temperature and Tint (spec/anchored-white-balance.md).

On a frame converted with levels from the picture, Temperature and Tint are a
per-channel gamma on the display value with black and white pinned - the
reference converter's white balance, measured on the user's street frame, unit
for unit. The move is largest in the mid-tones and the ends never take on a
colour, which is what reads as "corrective".

gammas(temp, tint)          -> (3,) per-channel gamma (R, G, B)
apply(d, temp, tint)        -> d with the gammas applied in place (float32 ..., 3)
temp_density / tint_density -> the pair as the colour difference at mid grey
temp_slider / tint_slider   -> the inverses (the AWB roll guard's units)
"""

import numpy as np

# ln(gamma) per slider unit, per channel (R, G, B): measured at Temperature
# 13.24 and at Temperature 11.47 / Tint -2.65 (spec §1).
TEMP_LN = np.array([-0.001526, 0.000377, 0.013394])
TINT_LN = np.array([-0.008862, 0.011186, -0.001596])
EPS = 0.01                 # the offset that bounds the slope at black
_MID = float(np.log10(0.5))


def gammas(temp, tint):
    """Per-channel gamma (R, G, B) for the sliders."""
    return np.exp(float(temp or 0.0) * TEMP_LN + float(tint or 0.0) * TINT_LN)


def curve(v, gamma):
    """g(v): ((v + EPS)^gamma - EPS^gamma) / ((1 + EPS)^gamma - EPS^gamma)
    for v >= 0, a straight line at g'(0) below 0. g(0) = 0, g(1) = 1."""
    v = np.asarray(v, np.float32)
    g = float(gamma)
    e0 = EPS ** g
    norm = (1.0 + EPS) ** g - e0
    slope0 = g * EPS ** (g - 1.0) / norm
    out = np.power(np.maximum(v, 0.0) + np.float32(EPS), np.float32(g))
    out -= np.float32(e0)
    out *= np.float32(1.0 / norm)
    neg = v < 0
    if np.any(neg):
        out = np.where(neg, v * np.float32(slope0), out)
    return out.astype(np.float32, copy=False)


def apply(d, temp, tint):
    """The anchored white balance on display values `d` (..., 3), RGB, in
    place where it can. Returns `d`. (0, 0) leaves it untouched."""
    if not (temp or tint):
        return d
    gs = gammas(temp, tint)
    for c in range(3):
        if abs(gs[c] - 1.0) < 1e-9:
            continue
        d[..., c] = curve(d[..., c], gs[c])
    return d


# --- The AWB roll guard's units (spec §6) ---------------------------------

def temp_density(temp):
    """Temperature as R - B in log10 at mid grey, tint 0 (warm positive)."""
    g = gammas(temp, 0.0)
    return _MID * float(g[0] - g[2])


def tint_density(tint):
    """Tint as (R + B)/2 - G in log10 at mid grey, temperature 0 (magenta
    positive)."""
    g = gammas(0.0, tint)
    return _MID * float((g[0] + g[2]) / 2.0 - g[1])


_GRID = np.linspace(-100.0, 100.0, 2001)
_T_TAB = np.array([temp_density(t) for t in _GRID])
_N_TAB = np.array([tint_density(n) for n in _GRID])


def temp_slider(dens):
    """The Temperature whose temp_density is `dens` (clamped to the range)."""
    return float(np.interp(dens, _T_TAB, _GRID))


def tint_slider(dens):
    """The Tint whose tint_density is `dens` (clamped to the range)."""
    return float(np.interp(dens, _N_TAB, _GRID))
