"""What a frame was shot at, and whether its RAWs clip in the picture
(spec/thumbnail-hover-card.md). Qt-free.

The frame list's hover card shows, per file of a frame (the three colour shots
of a merge, or the one file, and an IR shot paired with it): its shutter speed
and ISO from the file's EXIF, and whether a colour of the RAW piles up on its
top value INSIDE THE PICTURE: the frame's crop, else Capture's frame guide,
else the sensor's middle 60 % — never the sprocket holes or the open light
round the carrier, which clip without harming the picture.
"""
import math
import os
import threading
from typing import Dict, List, Optional, Tuple

RAW_EXT = (".raf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef",
           ".srw", ".raw", ".3fr", ".iiq", ".rwl", ".x3f", ".erf", ".mef", ".mos",
           ".nrw", ".sr2", ".srf", ".kdc", ".dcr")
SLOT_NAMES = {"red": "Red", "green": "Green", "blue": "Blue", "white": "Shot",
              "ir": "IR"}
COLOUR_WORDS = {"R": "red", "G": "green", "B": "blue"}

_EXPOSURE_CACHE: Dict[tuple, Tuple[Optional[float], Optional[int]]] = {}
_CLIP_CACHE: Dict[tuple, dict] = {}
_LOCK = threading.Lock()


def is_raw(path) -> bool:
    return os.path.splitext(str(path))[1].lower() in RAW_EXT


def _key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return os.path.abspath(path), st.st_size, st.st_mtime_ns


# --------------------------------------------------------------------------- #
# the files of a frame
# --------------------------------------------------------------------------- #
def frame_files(img) -> List[Tuple[str, str]]:
    """[(slot, path)] of a frame: red / green / blue for a merge (in its
    sources' order), else "white" for its one file; "ir" last when an IR shot
    is paired with it."""
    out = []
    srcs = list(getattr(img, "merge_sources", None) or []) if getattr(img, "is_merged", False) \
        else []
    if len(srcs) == 3:
        out += list(zip(("red", "green", "blue"), srcs))
    elif getattr(img, "file_path", None):
        out.append(("white", img.file_path))
    ir = getattr(img, "ir_source", None)
    if ir:
        out.append(("ir", ir))
    return out


def picture_area(img) -> Tuple[Optional[dict], str]:
    """(area in sensor fractions as frame_meter reads it — cx, cy, w, h, angle —
    or None for the sensor's middle, and what it is: "crop", "frame guide" or
    "middle"). A sliced frame's crop is of the slice, not the sensor: middle."""
    if getattr(img, "source_ops", None):
        return None, "middle"
    rect = getattr(img, "crop_rect", None)
    if rect:
        try:
            x1, y1, x2, y2 = (float(v) for v in rect)
            if x2 - x1 > 0.02 and y2 - y1 > 0.02:
                return ({"cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2, "w": x2 - x1,
                         "h": y2 - y1, "angle": float(getattr(img, "crop_angle", 0.0) or 0.0)},
                        "crop")
        except (TypeError, ValueError):
            pass
    g = getattr(img, "capture_guide", None)
    if isinstance(g, dict) and all(k in g for k in ("cx", "cy", "w", "h")):
        return ({k: float(g[k]) for k in ("cx", "cy", "w", "h")}
                | {"angle": float(g.get("angle", 0.0) or 0.0)}, "frame guide")
    return None, "middle"


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def exposure(path) -> Tuple[Optional[float], Optional[int]]:
    """(shutter seconds, ISO) from the file's EXIF, cached; (None, None)."""
    k = _key(path)
    if k is None:
        return None, None
    with _LOCK:
        if k in _EXPOSURE_CACHE:
            return _EXPOSURE_CACHE[k]
    try:
        from core.it8_profile import read_shot_exposure
        val = read_shot_exposure(path)
    except Exception:
        val = (None, None)
    with _LOCK:
        if len(_EXPOSURE_CACHE) > 2048:
            _EXPOSURE_CACHE.clear()
        _EXPOSURE_CACHE[k] = val
    return val


def cached_exposure(path):
    """The exposure already read for this file, or None (not read yet)."""
    k = _key(path)
    if k is None:
        return (None, None)
    with _LOCK:
        return _EXPOSURE_CACHE.get(k)


def _area_key(area):
    return None if area is None else tuple(round(float(area[k]), 4)
                                           for k in ("cx", "cy", "w", "h", "angle"))


def cached_clipping(path, area):
    """The clipping already read for this file and area, or None."""
    k = _key(path)
    if k is None:
        return None
    with _LOCK:
        return _CLIP_CACHE.get((k, _area_key(area)))


def clipping(path, area, reader=None) -> dict:
    """{colour letter: share of its photosites} that clip inside `area` (see
    frame_meter.clipping_in_guide), read once per file and area."""
    k = _key(path)
    if k is None:
        return {}
    ck = (k, _area_key(area))
    with _LOCK:
        if ck in _CLIP_CACHE:
            return _CLIP_CACHE[ck]
    if reader is None:
        from core import frame_meter
        reader = frame_meter.read_clipping
    try:
        val = dict(reader(path, area) or {})
    except Exception:
        val = {"error": True}
    with _LOCK:
        if len(_CLIP_CACHE) > 2048:
            _CLIP_CACHE.clear()
        _CLIP_CACHE[ck] = val
    return val


def to_read(img) -> List[tuple]:
    """[(path, area key, area, clip?)] still to read for the card: every file's
    exposure, and the clipping of the colour shots / the one file (RAW only;
    never the IR shot's)."""
    area, _what = picture_area(img)
    out = []
    for slot, p in frame_files(img):
        clip = slot != "ir" and is_raw(p) and cached_clipping(p, area) is None
        if clip or cached_exposure(p) is None:
            out.append((p, _area_key(area), area, clip))
    return out


# --------------------------------------------------------------------------- #
# the card
# --------------------------------------------------------------------------- #
def shutter_text(sec) -> str:
    if sec is None or not sec > 0:
        return "?"
    if sec >= 0.95:
        return f"{sec:g} s" if sec < 10 else f"{round(sec):d} s"
    den = 1.0 / sec
    return f"1/{den:.1f} s".replace(".0 s", " s") if den < 4 else f"1/{round(den):d} s"


def _esc(t) -> str:
    import html
    return html.escape(str(t))


def card_html(img, name, warn="#e5484d") -> str:
    """The hover card as rich text: the full name, then a row per file —
    what it is, shutter · ISO, and its clipping inside the picture ("…"
    while not read yet)."""
    area, what = picture_area(img)
    rows = []
    for slot, path in frame_files(img):
        got = cached_exposure(path)
        if got is None:
            exp = "…"
        else:
            sec, iso = got
            exp = shutter_text(sec) + (f" · ISO {iso}" if iso else "")
        if slot == "ir":
            clip = ""
        elif not is_raw(path):
            clip = "not a RAW"
        else:
            c = cached_clipping(path, area)
            if c is None:
                clip = "checking…"
            elif c.get("error"):
                clip = "couldn't be read"
            elif not c:
                clip = "not clipped"
            else:
                parts = [f"{COLOUR_WORDS.get(ch, ch)} {share * 100:.2g} %"
                         for ch, share in sorted(c.items(), key=lambda kv: "RGB".find(kv[0]))]
                clip = (f"<b style='color:{warn}'>clips: " + _esc(", ".join(parts)) + "</b>")
        label = SLOT_NAMES.get(slot, slot)
        if slot == "white" and len(frame_files(img)) <= 2:
            label = "Shot"
        rows.append(f"<tr><td>{_esc(label)}</td><td>&nbsp;&nbsp;{_esc(exp)}</td>"
                    f"<td>&nbsp;&nbsp;&nbsp;{clip if clip.startswith('<b') else _esc(clip)}</td></tr>")
    where = {"crop": "inside the crop", "frame guide": "inside Capture's frame guide",
             "middle": "in the middle of the sensor"}[what]
    foot = f"Clipping is read on the RAW, {where}: sprocket holes and the light round the film don't count."
    return (f"<b>{_esc(name)}</b><table cellspacing='0' cellpadding='0'>{''.join(rows)}</table>"
            f"<span style='font-size:11px'>{_esc(foot)}</span>")


# --------------------------------------------------------------------------- #
# reading in the background, one file at a time, newest asked first
# --------------------------------------------------------------------------- #
class ClipReader:
    """Reads clipping on one daemon thread; `done(path)` is called (on that
    thread) after each file. Newest requests first; a file already queued or
    read is not read again."""

    def __init__(self, done=None, reader=None):
        self._done = done
        self._reader = reader
        self._queue: List[Tuple[str, dict]] = []
        self._cv = threading.Condition()
        self._thread = None

    def ask(self, items):
        """items: to_read()'s (path, area key, area, clip?) tuples."""
        with self._cv:
            for it in items:
                key = it[:2] + (it[3],)
                self._queue = [q for q in self._queue if q[:2] + (q[3],) != key]
                self._queue.append(it)
            self._cv.notify()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="frame-card-clip")
                self._thread.start()

    def pending(self) -> int:
        with self._cv:
            return len(self._queue)

    def _run(self):
        while True:
            with self._cv:
                if not self._queue:
                    self._cv.wait(timeout=5.0)
                    if not self._queue:
                        return
                path, _ak, area, clip = self._queue.pop()      # newest first
            if cached_exposure(path) is not None and (
                    not clip or cached_clipping(path, area) is not None):
                continue                                       # read meanwhile
            exposure(path)
            if clip:
                clipping(path, area, self._reader)
            if self._done is not None:
                try:
                    self._done(path)
                except Exception:
                    pass
