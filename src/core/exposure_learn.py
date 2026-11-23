"""The per-shot exposure estimate learns from each shot it made
(spec/frame-exposure.md, revision 2026-11-23). Qt-free.

A shot's error is how far its frame's thinnest part landed from where the
live estimate predicted, in stops, the largest over the shot's colours. The
last few errors per setup (camera + scan type) give a correction applied to
the next estimates."""
import json
import math
import statistics

KEEP = 12              # errors kept per setup
MAX_BIAS = 1.5         # the correction is clamped to this many stops
MAX_ERR = 3.0          # an error bigger than this is another frame / a moved guide
LEVEL_MIN = 0.03       # a measured level under this share of the range says nothing


def setup_key(camera, scan, light=None):
    """The setup a correction belongs to: the camera's model and the scan
    type (and, in B&W, its light)."""
    parts = [str(camera or "camera"), str(scan or "colour")]
    if scan == "bw" and light:
        parts.append(str(light))
    return "|".join(parts)


def predicted(base_level, ratio, e_used, e_base):
    """The level the estimate predicted for a file: the film base's reading
    × the live ratio × the exposure used over the film base's."""
    try:
        if not (base_level and ratio and e_used and e_base) or e_base <= 0:
            return None
        return float(base_level) * float(ratio) * float(e_used) / float(e_base)
    except (TypeError, ValueError):
        return None


def colour_error(measured, pred):
    """log2(measured / predicted), or None when it says nothing."""
    if measured is None or pred is None or pred <= 0 or measured < LEVEL_MIN:
        return None
    err = math.log2(float(measured) / float(pred))
    return err if abs(err) <= MAX_ERR else None


def shot_error(errors, colours):
    """The shot's error: the LARGEST of its colours' (so no colour passes the
    target), once every colour in `colours` has one; else None."""
    if not colours or any(errors.get(k) is None for k in colours):
        return None
    return max(float(errors[k]) for k in colours)


def correction(errs):
    """median × n / (n + 1), clamped: one shot moves it half way."""
    errs = [float(e) for e in (errs or []) if e is not None]
    if not errs:
        return 0.0
    n = len(errs)
    b = statistics.median(errs) * n / (n + 1.0)
    return max(-MAX_BIAS, min(MAX_BIAS, b))


class Store:
    """The errors per setup, kept as JSON through `get()` / `put(text)`."""

    def __init__(self, get, put):
        self._get, self._put = get, put

    def _load(self):
        try:
            d = json.loads(self._get() or "{}")
        except (TypeError, ValueError):
            d = {}
        return d if isinstance(d, dict) else {}

    def errors(self, key):
        v = self._load().get(key)
        return [float(e) for e in v] if isinstance(v, list) else []

    def add(self, key, err):
        d = self._load()
        errs = [float(e) for e in (d.get(key) or []) if isinstance(e, (int, float))]
        errs.append(round(float(err), 4))
        d[key] = errs[-KEEP:]
        self._put(json.dumps(d))
        return d[key]

    def forget(self, key=None):
        d = self._load()
        if key is None:
            d = {}
        else:
            d.pop(key, None)
        self._put(json.dumps(d))

    def bias(self, key):
        """(correction in stops, shots it is from)."""
        errs = self.errors(key)
        return correction(errs), len(errs)
