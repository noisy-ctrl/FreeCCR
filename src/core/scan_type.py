"""What is being scanned, and how Hot Folder Tether groups a trichrome scan
(spec/scan-type.md). Qt-free.

- Scan types: "colour" (one exposure under white light), "trichrome" (red,
  green and blue exposures merged into one frame), "bw" (one exposure of a
  black-and-white negative, under whatever light: white or a single channel).
- `TripletGrouper`: new RAW files in name order, three at a time.
- The light check: each frame of a triplet should be lit by its own colour,
  and all three should show one picture. Read from the RAW mosaic's channel
  means and a small gradient signature of it; a frame that isn't red, green
  or blue where it should be, or shows another shot, stops the triplet from
  merging blindly.
"""
import os
import time

import numpy as np

SCAN_TYPES = ("colour", "trichrome", "bw")
LABELS = {"colour": "Colour · white light",
          "trichrome": "Colour · trichrome (R → G → B)",
          "bw": "Black & white"}
SHORT = {"colour": "colour", "trichrome": "trichrome", "bw": "B&W"}
# The Start window's picture tiles (spec/scan-type-tiles.md): in the order the
# user lists them, with a short name under each picture.
TILE_ORDER = ("trichrome", "colour", "bw")
TILE_NAMES = {"trichrome": "RGB", "colour": "White light", "bw": "B&W"}
TILE_HINTS = {"trichrome": "Red, green and blue exposures of a colour negative, "
                           "merged into one frame.",
              "colour": "One exposure of a colour negative under white light.",
              "bw": "One exposure of a black-and-white negative."}
DEFAULT = "colour"

# A frame lit by one LED colour puts most of its signal in that channel.
# Measured on every RAF the user has sent (2026-11-02, spec/smart-triplets.md
# revision): red frames 0.77-0.85, green 0.54-0.74, blue 0.55-0.70 (dense
# Portra frames are the low ones: a dense dye eats the middle of the LED's
# band); white light through a negative leads at 0.42-0.49 (39 frames, two
# cameras). 0.51 sits halfway. (It was 0.60, from the first 30 RAFs, and
# flagged 7 of 51 dense Portra frames.)
LIGHT_SHARE_MIN = 0.51
# A frame below LIGHT_SHARE_MIN is still taken as the one colour its shot
# lacks when its largest share is that colour, at least this, and its picture
# matches the shot's other two frames (spec/smart-triplets.md, rule 3).
RESCUE_SHARE_MIN = 0.40

# One picture per set (spec/smart-triplets.md, rule 2): a gradient signature
# of the frame's middle, from the same subsampled mosaic the shares come from.
PICTURE_SIZE = 160        # long side of the thumbnail the signature is taken on
PICTURE_INNER = 0.6       # the middle compared (rebates, holes, holder left out)
PICTURE_SAME = 0.35       # same shot 0.45-0.99, different shots <= 0.24 (measured)
PICTURE_RELIABLE = 0.5    # the frame's two halves must agree (measured 0.84-0.997)
STALL_S = 30.0
NAMES = ("red", "green", "blue")


def clean(t):
    return t if t in SCAN_TYPES else DEFAULT


# --------------------------------------------------------------------------- #
# grouping
# --------------------------------------------------------------------------- #
def _key(path):
    return (os.path.basename(path).lower(), os.path.normcase(path))


class TripletGrouper:
    """Collects new RAW files and hands them out three at a time, in name
    order (the order Open's 3-way merge uses). `stalled()` returns what is
    left of a triplet that has waited STALL_S since its last file."""

    def __init__(self, clock=time.monotonic, stall_s=None):
        self.clock = clock
        self.stall_s = STALL_S if stall_s is None else stall_s
        self.pending = []
        self.last_add = None

    def add(self, path):
        if path not in self.pending:
            self.pending.append(path)
            self.pending.sort(key=_key)
            self.last_add = self.clock()

    def take(self):
        out = []
        while len(self.pending) >= 3:
            out.append(tuple(self.pending[:3]))
            del self.pending[:3]
        return out

    def stalled(self):
        if self.pending and self.last_add is not None and \
                self.clock() - self.last_add >= self.stall_s:
            return self.flush()
        return []

    def flush(self):
        out, self.pending = list(self.pending), []
        self.last_add = None
        return out


def group_existing(paths):
    """(triplets, leftover) for files already in the folder at the start."""
    g = TripletGrouper()
    for p in paths:
        g.add(p)
    trips = g.take()
    return trips, g.flush()


# --------------------------------------------------------------------------- #
# the light check
# --------------------------------------------------------------------------- #
def channel_shares(means_rgb):
    m = np.clip(np.asarray(means_rgb, np.float64), 0.0, None)
    s = float(m.sum())
    return tuple(float(v) for v in m / s) if s > 0 else None


def describe(shares):
    if shares is None:
        return "unreadable"
    k = int(np.argmax(shares))
    if shares[k] < LIGHT_SHARE_MIN:
        return "lit by white or mixed light"
    return f"lit {NAMES[k]}"


def rescuable(shares, colour_index):
    """Rule 3's own part: largest in `colour_index` and at least
    RESCUE_SHARE_MIN (the picture part is the caller's)."""
    return (shares is not None and int(np.argmax(shares)) == colour_index
            and shares[colour_index] >= RESCUE_SHARE_MIN)


def judge_triplet(shares_list, names=None, pictures=None):
    """(ok, index of the first out-of-place frame or None, reason). A frame
    whose shares are None (a monochrome sensor, an unreadable file) is not
    judged: there is nothing to tell its light by. With `pictures` (one
    `Picture` or None per frame), no two frames may be known to show
    different pictures, and a weak frame whose picture matches the other two
    is taken as its slot's colour (spec/smart-triplets.md, rules 2 and 3)."""
    pictures = list(pictures) if pictures is not None else [None] * len(shares_list)
    who = (lambda k: f" ({names[k]})") if names else (lambda k: "")
    for k, sh in enumerate(shares_list):
        if sh is None:
            continue
        if int(np.argmax(sh)) == k and sh[k] >= LIGHT_SHARE_MIN:
            continue
        others = [j for j in range(len(shares_list)) if j != k]
        if rescuable(sh, k) and all(same_picture(pictures[k], pictures[j]) is True
                                     for j in others):
            continue
        return False, k, (f"frame {k + 1}{who(k)} should be lit {NAMES[k]} "
                          f"but looks {describe(sh)}")
    n = len(shares_list)
    same = {(i, j): same_picture(pictures[i], pictures[j])
            for i in range(n) for j in range(i + 1, n)}
    if any(v is False for v in same.values()):
        for k in range(n):
            mine = [v for (i, j), v in same.items() if k in (i, j)]
            rest = [v for (i, j), v in same.items() if k not in (i, j)]
            if all(v is False for v in mine) and not any(v is False for v in rest):
                others = " and ".join(str(j + 1) for j in range(n) if j != k)
                return False, k, (f"frame {k + 1}{who(k)} shows a different picture "
                                  f"from frames {others}")
        i, j = next(key for key, v in same.items() if v is False)
        return False, j, (f"frames {i + 1} and {j + 1} show different pictures "
                          "(not one shot)")
    return True, None, ""


# --------------------------------------------------------------------------- #
# the picture: is it one shot?
# --------------------------------------------------------------------------- #
class Picture:
    """A frame's picture signature: `sig` (zero-mean, unit-norm gradient
    energy of the middle) and `reliability` (its two halves' agreement)."""

    __slots__ = ("sig", "reliability")

    def __init__(self, sig, reliability):
        self.sig = sig
        self.reliability = float(reliability)


def _signature(mono, shape=None):
    import cv2
    m = np.maximum(np.asarray(mono, np.float32), np.float32(1e-4))
    h, w = m.shape
    if shape is None:
        s = PICTURE_SIZE / max(h, w)
        shape = (max(8, round(w * s)), max(8, round(h * s)))
    m = cv2.resize(m, shape, interpolation=cv2.INTER_AREA)
    lg = np.log(m)
    h, w = lg.shape
    dy, dx = int(round(h * (1 - PICTURE_INNER) / 2)), int(round(w * (1 - PICTURE_INNER) / 2))
    lg = lg[dy:h - dy, dx:w - dx]
    gx = cv2.Sobel(lg, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lg, cv2.CV_32F, 0, 1, ksize=3)
    g = np.sqrt(gx * gx + gy * gy)
    g -= g.mean()
    n = float(np.sqrt((g * g).sum()))
    return (g / n if n > 1e-12 else None), shape


def _mono(v, colors):
    """Each photosite over its colour's mean: under one LED the mosaic is
    then a single picture, whatever the colour."""
    out = np.empty(v.shape, np.float32)
    for c in np.unique(colors):
        sel = colors == c
        mean = float(v[sel].mean())
        if mean <= 0:
            return None
        out[sel] = v[sel] / mean
    return out


def picture_from_mosaic(mosaic, colors, black=None):
    """The `Picture` of a (subsampled) mosaic, or None when it can't be made
    (too small, a colour with no signal)."""
    try:
        v = np.asarray(mosaic, np.float32)
        colors = np.asarray(colors)
        if v.ndim != 2 or min(v.shape) < 16:
            return None
        if black:
            bl = np.zeros(int(colors.max()) + 1, np.float32)
            for i in range(min(len(black), bl.size)):
                bl[i] = float(black[i])
            v = v - bl[colors]
        full = _mono(v, colors)
        a, b = _mono(v[0::2], colors[0::2]), _mono(v[1::2], colors[1::2])
        if full is None or a is None or b is None:
            return None
        sig, shape = _signature(full)
        sa, _ = _signature(a, shape)
        sb, _ = _signature(b, shape)
        if sig is None or sa is None or sb is None:
            return Picture(sig, 0.0) if sig is not None else None
        return Picture(sig, float((sa * sb).sum()))
    except Exception:
        return None


def picture_match(a, b):
    """Correlation of two pictures' signatures, or None when they can't be
    compared (missing, unreliable, made at different sizes)."""
    if a is None or b is None or a.sig is None or b.sig is None:
        return None
    if min(a.reliability, b.reliability) < PICTURE_RELIABLE or a.sig.shape != b.sig.shape:
        return None
    return float((a.sig * b.sig).sum())


def same_picture(a, b):
    """True / False when the two frames are known to show the same picture
    or not; None when it can't be told."""
    c = picture_match(a, b)
    return None if c is None else c >= PICTURE_SAME


class LightReading:
    """What one read of a RAW tells: its light `shares` and its `picture`
    (either may be None)."""

    __slots__ = ("shares", "picture")

    def __init__(self, shares, picture=None):
        self.shares = shares
        self.picture = picture


def read_light(path):
    """`LightReading` of a RAW: the shares of the mosaic's black-subtracted
    channel means (None for a monochrome sensor or a file that can't be
    read) and its picture signature, from one read. Every fifth photosite
    each way (5 is coprime with the 2x2 and 6x6 patterns, so every colour is
    sampled)."""
    try:
        import rawpy
        from core.ccr_merge import is_monochrome_sensor
        with rawpy.imread(path) as raw:
            if is_monochrome_sensor(raw.num_colors, raw.color_desc,
                                    getattr(raw, "raw_pattern", None)):
                return LightReading(None, None)
            # copies: these are views into LibRaw's buffer, which is freed
            # when the file closes (reading a view after that segfaults)
            mosaic = np.array(raw.raw_image_visible[::5, ::5], copy=True)
            colors = np.array(raw.raw_colors_visible[::5, ::5], copy=True)
            black = list(raw.black_level_per_channel)
            desc = bytes(raw.color_desc).decode("ascii", "ignore").upper()
        return LightReading(shares_from_mosaic(mosaic, colors, desc, black),
                            picture_from_mosaic(mosaic, colors, black))
    except Exception:
        return LightReading(None, None)


def capture_time(path):
    """When a RAW was shot: (EXIF DateTimeOriginal, SubSecTimeOriginal padded
    to 6 digits) as sortable text, or None. A Fujifilm RAF keeps its EXIF in
    the embedded preview JPEG, which is read directly (exifread doesn't find
    it in the RAF container); other RAWs are TIFF-based and read whole."""
    try:
        import io
        import struct
        import exifread
        with open(path, "rb") as f:
            head = f.read(100)
            tags = {}
            if head[:16] == b"FUJIFILMCCD-RAW ":
                off, ln = struct.unpack(">II", head[84:92])
                f.seek(off)
                tags = exifread.process_file(io.BytesIO(f.read(ln)), details=False)
            if "EXIF DateTimeOriginal" not in tags:
                f.seek(0)
                tags = exifread.process_file(f, details=False)
        when = str(tags.get("EXIF DateTimeOriginal", "")).strip()
        if not when or when.startswith("0000"):
            return None
        sub = str(tags.get("EXIF SubSecTimeOriginal", "")).strip()
        return (when, sub[:6].ljust(6, "0") if sub.isdigit() else "000000")
    except Exception:
        return None


def light_shares(path):
    """(R, G, B) shares of the RAW mosaic (see `read_light`), or None."""
    return read_light(path).shares


def shares_from_mosaic(mosaic, colors, desc, black=None):
    """The pure part of `light_shares` (testable without RAW files)."""
    n = max(4, int(np.max(colors)) + 1)
    flat_c = np.asarray(colors).ravel().astype(np.int64)
    sums = np.bincount(flat_c, weights=np.asarray(mosaic, np.float64).ravel(), minlength=n)
    counts = np.bincount(flat_c, minlength=n)
    means = sums / np.maximum(counts, 1)
    if black:
        for i in range(min(len(black), n)):
            means[i] -= float(black[i])
    rgb = []
    for ch in "RGB":
        idx = [i for i, c in enumerate(desc[:n]) if c == ch and counts[i] > 0]
        if not idx:
            return None
        rgb.append(float(np.mean([means[i] for i in idx])))
    return channel_shares(rgb)


def thumb_bgr(path, height=44):
    """The RAW's embedded preview, small, for an attention tile; or None."""
    try:
        import cv2
        import rawpy
        with rawpy.imread(path) as raw:
            t = raw.extract_thumb()
        if t.format == rawpy.ThumbFormat.JPEG:
            arr = cv2.imdecode(np.frombuffer(t.data, np.uint8), cv2.IMREAD_COLOR)
        else:
            arr = cv2.cvtColor(np.asarray(t.data), cv2.COLOR_RGB2BGR)
        if arr is None:
            return None
        h, w = arr.shape[:2]
        return cv2.resize(arr, (max(1, round(w * height / h)), height), interpolation=cv2.INTER_AREA)
    except Exception:
        return None
