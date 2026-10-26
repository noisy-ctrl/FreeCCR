"""What is being scanned, and how Hot Folder Tether groups a trichrome scan
(spec/scan-type.md). Qt-free.

- Scan types: "colour" (one exposure under white light), "trichrome" (red,
  green and blue exposures merged into one frame), "bw" (one exposure of a
  black-and-white negative, under whatever light: white or a single channel).
- `TripletGrouper`: new RAW files in name order, three at a time.
- The light check: each frame of a triplet should be lit by its own colour.
  Read from the RAW mosaic's channel means; a frame that isn't red, green or
  blue where it should be stops the triplet from merging blindly.
"""
import os
import time

import numpy as np

SCAN_TYPES = ("colour", "trichrome", "bw")
LABELS = {"colour": "Colour · white light",
          "trichrome": "Colour · trichrome (R → G → B)",
          "bw": "Black & white"}
SHORT = {"colour": "colour", "trichrome": "trichrome", "bw": "B&W"}
DEFAULT = "colour"

# A frame lit by one LED colour puts most of its signal in that channel: about
# 0.7-0.9 of the R+G+B sum (the other photosites see only the LED's skirt).
# White light through an orange negative peaks in red at ~0.45-0.5, so 0.6
# separates them. A reasoned estimate, to be confirmed on real triplets.
LIGHT_SHARE_MIN = 0.60
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


def judge_triplet(shares_list, names=None):
    """(ok, index of the first out-of-place frame or None, reason). A frame
    whose shares are None (a monochrome sensor, an unreadable file) is not
    judged: there is nothing to tell its light by."""
    for k, sh in enumerate(shares_list):
        if sh is None:
            continue
        if int(np.argmax(sh)) != k or sh[k] < LIGHT_SHARE_MIN:
            who = f" ({names[k]})" if names else ""
            return False, k, (f"frame {k + 1}{who} should be lit {NAMES[k]} "
                              f"but looks {describe(sh)}")
    return True, None, ""


def light_shares(path):
    """(R, G, B) shares of the RAW mosaic's black-subtracted channel means,
    or None for a monochrome sensor or a file that can't be read. Every
    fifth photosite each way (5 is coprime with the 2x2 and 6x6 patterns,
    so every colour is sampled)."""
    try:
        import rawpy
        from core.ccr_merge import is_monochrome_sensor
        with rawpy.imread(path) as raw:
            if is_monochrome_sensor(raw.num_colors, raw.color_desc,
                                    getattr(raw, "raw_pattern", None)):
                return None
            mosaic = np.asarray(raw.raw_image_visible)[::5, ::5]
            colors = np.asarray(raw.raw_colors_visible)[::5, ::5]
            black = list(raw.black_level_per_channel)
            desc = bytes(raw.color_desc).decode("ascii", "ignore").upper()
        return shares_from_mosaic(mosaic, colors, desc, black)
    except Exception:
        return None


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
