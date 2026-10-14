"""
Pure (Qt-free) geometry + presets backing the Crop panel's aspect-ratio and
straighten features. Kept import-light and side-effect-free so it can be unit
tested without a GUI. See spec/crop-panel.md.

Ratios are width/height in *Landscape* orientation; `oriented_ratio` applies the
portrait/landscape toggle, and `effective_box_ratio` maps an on-screen target
ratio onto the un-rotated image space the crop box lives in (90/270 coarse
rotation swaps width<->height).
"""

from core import film_formats

# (label, key, base_ratio): base_ratio is width/height for Landscape, or None
# for Free, or the sentinel "original" (resolve from the image's own aspect),
# or HEADING for a group title in the list (not selectable). Film formats come
# first (spec/film-formats.md); print and screen ratios follow.
HEADING = "heading"
ASPECT_PRESETS = [
    ("Free", "free", None),
    ("Original", "original", "original"),
    ("Film formats", "#film", HEADING),
] + [(label, key, r) for key, label, r, _cols in film_formats.FILM_FORMATS] + [
    ("Print and screen", "#print", HEADING),
    ("1:1", "1:1", 1.0),
    ("5:4", "5:4", 5.0 / 4.0),
    ("4:3", "4:3", 4.0 / 3.0),
    ("7:5", "7:5", 7.0 / 5.0),
    ("3:2", "3:2", 3.0 / 2.0),
    ("16:9", "16:9", 16.0 / 9.0),
    # Cinematic ratios (width/height in Landscape). Academy 1.375 is the classic
    # 35 mm sound-film frame; the rest are the modern wide theatrical/streaming
    # standards — DCI Flat 1.85, Univisium 2.0, CinemaScope 2.35, DCI Scope 2.39.
    ("Academy (1.37:1)", "academy", 1.375),
    ("1.85:1 (Flat)", "1.85:1", 1.85),
    ("2:1 (Univisium)", "2:1", 2.0),
    ("2.35:1 (CinemaScope)", "2.35:1", 2.35),
    ("2.39:1 (Scope)", "2.39:1", 2.39),
    ("Custom…", "custom", None),
]

# Keys for which the Landscape/Portrait toggle is meaningless.
ORIENTATION_FIXED_KEYS = {"free", "original", "1:1", "6x6", "custom"}
FILM_KEYS = {k for k, _l, _r, _c in film_formats.FILM_FORMATS}


def preset_for_key(key):
    """Return the (label, key, base_ratio) tuple for `key`, or None (also for
    a group heading, which is not a preset)."""
    for entry in ASPECT_PRESETS:
        if entry[1] == key:
            return None if entry[2] == HEADING else entry
    return None


def oriented_ratio(ratio, landscape):
    """Apply the orientation toggle to a base (Landscape) width/height ratio.
    Portrait flips it to height/width. None / non-positive pass through."""
    if ratio is None or ratio <= 0:
        return ratio
    return ratio if landscape else 1.0 / ratio


def effective_box_ratio(display_ratio, rotation):
    """Convert an on-screen target ratio into the box-frame ratio used on the
    pending crop rect (which lives in un-rotated image space). A 90/270 coarse
    rotation swaps width<->height as seen on screen; flips don't change a
    rectangle's aspect."""
    if display_ratio is None or display_ratio <= 0:
        return display_ratio
    if rotation % 180 == 90:
        return 1.0 / display_ratio
    return display_ratio


def enforce_ratio_size(adx, ady, r):
    """Smallest box of ratio r = w/h that COVERS the desired extents
    (|adx|, |ady|). Used while drawing/corner-dragging so the box grows to
    include the pointer without breaking the ratio. r None/<=0 -> unconstrained."""
    adx, ady = abs(adx), abs(ady)
    if r is None or r <= 0:
        return adx, ady
    if adx >= ady * r:
        return adx, adx / r
    return ady * r, ady


def fit_ratio_within(w, h, r):
    """Largest box of ratio r = w/h that FITS inside w x h (centered use).
    r None/<=0 or degenerate bounds -> (w, h) unchanged."""
    if r is None or r <= 0 or w <= 0 or h <= 0:
        return w, h
    if w / h >= r:        # height is the binding dimension
        return h * r, h
    return w, w / r       # width is the binding dimension


def reshape_to_ratio(center, box_wh, img_wh, r):
    """Compute a box of ratio r for a preset change / auto-seed.

    - center: (cx, cy) of the current box, in image px (ignored when box_wh is
      falsy — the new box is centered on the image instead).
    - box_wh: (w, h) of the current pending box, or None to seed from the image.
    - img_wh: (W, H) of the full image.
    - r: target box-frame ratio (w/h), or None for Free.

    Returns (cx, cy, bw, bh), or None for Free / degenerate input. When a box
    exists the result is fit *within* it (never larger than the current
    selection); otherwise it is the largest centered box of ratio r in the image.
    """
    if r is None or r <= 0:
        return None
    W, H = img_wh
    if W <= 0 or H <= 0:
        return None
    if box_wh is not None and box_wh[0] >= 2 and box_wh[1] >= 2:
        cx, cy = center
        bw, bh = fit_ratio_within(box_wh[0], box_wh[1], r)
    else:
        cx, cy = W / 2.0, H / 2.0
        bw, bh = fit_ratio_within(W, H, r)
    return cx, cy, bw, bh


def folded_crop_angle(crop_angle, fine_rotation_angle):
    """The crop-box angle that absorbs an image-level micro-rotation, so the two
    rotations don't stack. `fine_rotation_angle` is in hundredths of a degree
    (CCRImage convention). Equivalence (see spec §5.4): image fine-rotation
    rotates content clockwise for positive values, the crop box de-rotates
    counter-clockwise, hence the sign flip."""
    return (crop_angle or 0.0) - (fine_rotation_angle or 0) / 100.0
