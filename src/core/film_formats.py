"""Film formats (spec/film-formats.md): one table for the Crop panel's
presets, the contact sheet's labels and strip widths, and auto crop's format
names. Qt-free.

Ratios are the image area's long side / short side (the gate, not the
nominal name: 6x4.5 is 56 x 41.5 mm).
"""

import math

# key, label, long/short ratio, frames per contact-sheet row (a cut strip)
FILM_FORMATS = [
    ("35mm", "35mm: 36x24", 36 / 24, 6),
    ("half", "35mm: half frame", 24 / 18, 6),
    ("xpan", "35mm: XPan", 65 / 24, 3),
    ("645", "120: 6x4.5", 56 / 41.5, 4),
    ("6x6", "120: 6x6", 1.0, 3),
    ("6x7", "120: 6x7", 69 / 56, 3),
    ("6x8", "120: 6x8", 76 / 56, 3),
    ("6x9", "120: 6x9", 84 / 56, 2),
    ("6x12", "120: 6x12", 112 / 56, 2),
    ("6x17", "120: 6x17", 168 / 56, 1),
    ("4x5", "Sheet: 4x5", 5 / 4, 3),
    ("8x10", "Sheet: 8x10", 10 / 8, 3),
]
_BY_KEY = {k: (label, ratio, cols) for k, label, ratio, cols in FILM_FORMATS}

# auto crop's format names (core.auto_crop.FORMATS) -> film keys
DETECTOR_TO_FILM = {"35mm 3:2": "35mm", "645": "645", "6x6": "6x6", "6x7": "6x7",
                    "6x9": "6x9", "half-frame": "half", "xpan": "xpan"}

# What a crop's proportions alone are read as. 3:2 is 35mm (6x9 is the same
# shape and far rarer), ~1.35 is 6x4.5 (half frame and 6x8 are close and
# rarer): pick a format in the Crop panel to say otherwise.
_INFER = ("35mm", "645", "6x6", "6x7", "xpan", "6x12", "6x17")
INFER_TOL = 0.025


def film_key(name):
    """A film key from a film key or one of auto crop's format names."""
    if not name:
        return None
    if name in _BY_KEY:
        return name
    return DETECTOR_TO_FILM.get(name)


def label(name):
    """'120: 6x4.5' for a film key or an auto crop format name; '' if unknown."""
    k = film_key(name)
    return _BY_KEY[k][0] if k else ""


def ratio(name):
    k = film_key(name)
    return _BY_KEY[k][1] if k else None


def strip_length(name):
    """Frames per row on a contact sheet: how the film is cut into strips."""
    k = film_key(name)
    return _BY_KEY[k][2] if k else None


def from_ratio(w, h, tol=INFER_TOL):
    """The film format a box of w x h most likely is, or None."""
    if w <= 0 or h <= 0:
        return None
    ar = max(w, h) / min(w, h)
    best = None
    for k in _INFER:
        d = abs(math.log(ar / _BY_KEY[k][1]))
        if d <= tol and (best is None or d < best[0]):
            best = (d, k)
    return best[1] if best else None
