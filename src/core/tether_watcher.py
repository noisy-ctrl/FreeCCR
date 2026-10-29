"""Watch-folder tethering: detect new capture files and import them as CCRImages.

See spec/camera-tethering.md. There is NO camera SDK here — the user's own
tethering app (Canon EOS Utility, Nikon NX Tether, Sony Imaging Edge, Fujifilm
X Acquire, …) or an SD auto-offload writes captures into a watched folder.
FreeCCR polls the folder (`FolderScanner`), and a long-lived `TetherWatchWorker`
decodes each new file and auto-converts it with the saved B/W point.

This module is deliberately Qt-light: `FolderScanner` and the encode/decode
helpers are pure (unit-tested without a display); only `TetherWatchWorker` uses
Qt signals so it can run on its own thread.
"""
import json
import os

from PySide6.QtCore import QObject, Signal, Slot

from core.ccr_backend import ccr_backend

# Still formats FreeCCR can decode (superset of the Open-Files filter plus the
# broader Open-Folder set). Lower-case, with the leading dot.
SUPPORTED_EXTS = {
    ".dng", ".tif", ".tiff", ".arw", ".nef", ".cr2", ".cr3", ".raf",
    ".png", ".jpg", ".jpeg", ".rw2", ".3fr", ".fff", ".sr2", ".orf",
    ".pef", ".heic", ".heif", ".dcr", ".kdc", ".x3f", ".srw", ".erf",
    ".nrw", ".ptx", ".r3d",
}
# RAW formats are preferred over a same-basename JPEG/PNG/TIFF sibling when both
# become ready in one poll tick (a camera shooting RAW+JPEG).
RAW_EXTS = {
    ".dng", ".arw", ".nef", ".cr2", ".cr3", ".raf", ".rw2", ".3fr", ".fff",
    ".sr2", ".orf", ".pef", ".dcr", ".kdc", ".x3f", ".srw", ".erf", ".nrw",
    ".ptx", ".r3d",
}


def _ext(name):
    return os.path.splitext(name)[1].lower()


def is_supported(name):
    return _ext(name) in SUPPORTED_EXTS


def is_raw(name):
    return _ext(name) in RAW_EXTS


def encode_bwpoint(bgr):
    """Serialize a (B,G,R) point to a QSettings-friendly JSON string, or None."""
    if bgr is None:
        return None
    try:
        return json.dumps([float(x) for x in bgr])
    except (TypeError, ValueError):
        return None


def decode_bwpoint(raw):
    """Parse a stored B/W point back to a (B,G,R) float tuple, tolerating
    malformed/legacy values (returns None)."""
    if not raw:
        return None
    try:
        vals = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(vals, (list, tuple)) and len(vals) == 3:
        try:
            return tuple(float(v) for v in vals)
        except (TypeError, ValueError):
            return None
    return None


class FolderScanner:
    """Pure, Qt-free new-file detector for a watched folder.

    `feed()` is given the current directory listing as (name, size) pairs and
    returns the names that are newly READY this tick: a supported extension,
    size > 0, and size unchanged since the previous tick (so a still-downloading
    file is held until it stops growing). Within one tick a RAW is preferred over
    a same-basename JPEG/PNG/TIFF sibling; cross-tick siblings both import (a
    documented v1 limitation, spec §9.1.4). Every candidate seen this tick — kept
    or dropped — is recorded so it never re-imports.
    """

    def __init__(self, initial_names=()):
        self.seen = set(initial_names)   # names already handled / pre-seeded
        self.sizes = {}                  # name -> last-seen size (stability gate)

    def feed(self, entries):
        # Coalesce duplicate names in one listing (possible on some network /
        # symlinked filesystems), keeping the largest reported size.
        listing = {}
        for name, size in entries:
            if name not in listing or size > listing[name]:
                listing[name] = size
        present = set(listing)

        # Forget files that have left the folder, so a delete-then-recreate of
        # the same filename (e.g. retaking a bad shot to the same name) imports
        # the new file rather than being blocked forever by the seen-set.
        self.seen &= present
        self.sizes = {n: s for n, s in self.sizes.items() if n in present}

        candidates = []
        for name, size in listing.items():
            if name in self.seen or not is_supported(name):
                continue
            if size <= 0:
                self.sizes[name] = size
                continue
            if self.sizes.get(name) == size:
                candidates.append(name)
            else:
                self.sizes[name] = size  # new/changed — wait one more tick

        # Within-tick basename dedupe, preferring RAW over non-RAW siblings.
        chosen = {}
        for name in candidates:
            base = os.path.splitext(name)[0].lower()
            cur = chosen.get(base)
            if cur is None or (is_raw(name) and not is_raw(cur)):
                chosen[base] = name
        chosen_set = set(chosen.values())

        ready = []
        for name in candidates:
            self.seen.add(name)          # handled whether chosen or dropped
            if name in chosen_set:
                ready.append(name)
        return ready


class TetherWatchWorker(QObject):
    """Long-lived worker living on its own QThread (with an event loop). The GUI
    delivers file paths via the queued `process()` slot; each is decoded into a
    CCRImage and, when a B/W point is set and Positive mode is off, converted to a
    positive. Results are emitted back to the GUI thread via `captured`.

    All ccr_backend list mutation and any thumbnail/canvas work happen in the
    GUI-thread slots that receive these signals — NOT here.
    """
    captured = Signal(object)          # CCRImage
    captureError = Signal(str, str)    # (path, message)
    # A trichrome triplet that failed the light check, or files left over:
    # {"paths": [...], "reason": str, "thumbs": [BGR array or None, ...]}
    # (spec/scan-type.md §5). The GUI shows it greyed out under the frames.
    needsAttention = Signal(object)

    def __init__(self):
        super().__init__()
        self._cancelled = False
        self._cat_mtime = None   # mtime of the cached catalog file
        self._cat_data = None    # cached parsed catalog (spec §9.2)

    def cancel(self):
        self._cancelled = True

    def _catalog(self):
        """Reuse the parsed catalog across captures, re-reading only when the
        file's mtime changes — avoids re-parsing JSON for every capture during
        a burst (spec §9.2)."""
        from core.catalog import default_catalog_path, load_catalog
        try:
            mtime = os.path.getmtime(default_catalog_path())
        except OSError:
            mtime = None
        if self._cat_data is None or mtime != self._cat_mtime:
            try:
                self._cat_data = load_catalog()
            except Exception:
                self._cat_data = None
            self._cat_mtime = mtime
        return self._cat_data

    @Slot(str)
    def process(self, path):
        if self._cancelled:
            return
        try:
            from core.catalog import create_images_for_path
            imgs = create_images_for_path(path, catalog_data=self._catalog())
            if not imgs:
                self.captureError.emit(path, "no image produced")
                return
            self._finish(imgs)
        except Exception as e:
            # Terse form: the GUI shows this inline in the hint strip, so a
            # tethered capture FreeCCR cannot decode still says why.
            from core.load_errors import describe_load_failure
            self.captureError.emit(
                path, describe_load_failure(path, e, short=True))

    @Slot(object)
    def process_triplet(self, job):
        """One trichrome shot: {"paths": (R, G, B), "check": bool}. Checked
        first (each frame lit by its own colour) unless the user chose Merge
        Anyway; then merged exactly as Open's 3-way merge does, and converted
        like a single tethered frame. spec/scan-type.md §5."""
        if self._cancelled:
            return
        paths = list(job.get("paths") or [])
        if len(paths) != 3:
            return
        from core import scan_type as st
        if job.get("check", True):
            shares = [st.light_shares(p) for p in paths]
            ok, _k, reason = st.judge_triplet(
                shares, [os.path.basename(p) for p in paths])
            if not ok:
                self.needsAttention.emit({
                    "paths": paths, "reason": reason,
                    "thumbs": [st.thumb_bgr(p) for p in paths]})
                return
        try:
            from core.catalog import create_images_for_merge
            imgs = create_images_for_merge(
                paths, merge_demosaic=ccr_backend.rgb_merge_demosaic,
                catalog_data=self._catalog(),
                merge_mono=getattr(ccr_backend, "rgb_merge_mono", False))
            if not imgs:
                self.captureError.emit(paths[0], "no image produced")
                return
            self._finish(imgs)
        except Exception as e:
            self.needsAttention.emit({
                "paths": paths, "reason": f"the merge failed: {e}",
                "thumbs": [st.thumb_bgr(p) for p in paths]})

    @Slot(object)
    def describe_leftover(self, job):
        """Files that can't make a triplet (a missed shot, a stop part-way):
        shown greyed out with their thumbnails, nothing guessed."""
        if self._cancelled:
            return
        from core import scan_type as st
        paths = list(job.get("paths") or [])
        self.needsAttention.emit({
            "paths": paths, "reason": job.get("reason", ""),
            "thumbs": [st.thumb_bgr(p) for p in paths]})

    def _finish(self, imgs):
        """Convert (when the session has a black point) and hand over."""
        # Snapshot the conversion inputs once per file so a mid-decode flip
        # (Positive mode / B/W point change) can't make one file half-convert.
        black = ccr_backend.black_point_bgr
        white = ccr_backend.white_point_bgr
        positive = ccr_backend.positive_mode
        # white may be None → default-slope conversion (black point only),
        # where a selected film-stock preset supplies per-channel slopes.
        slopes = ccr_backend.film_stock_slopes if white is None else None
        convert = (not positive and black is not None)
        from core import lead_frame
        for img in imgs:
            if self._cancelled:
                return
            # Lit enough to show film base? Measured on the unconverted data,
            # for the roll black point prompt (spec/tether-black-point.md).
            try:
                img.lit_level = None if img.converted else lead_frame.lit_level(img)
            except Exception:
                img.lit_level = None
            if convert and not img.converted:
                self._convert(img, black, white, slopes)
            self.captured.emit(img)

    @staticmethod
    def _convert(img, black, white, slopes=None):
        """Bake the saved B/W-point conversion into the image (preview + the
        conversion_inputs snapshot the export/catalog paths replay). Mirrors
        CCRBackend.apply_bwpoint_to_all_images. On failure the image is left
        unconverted but still importable."""
        from core.ccr_processor import ccr_normalize_with_bwpoint
        from core.ccr_backend import ccr_backend
        try:
            density = bool(ccr_backend.density_bwpoint)
            processed = ccr_normalize_with_bwpoint(img, black, white, density=density,
                                                   slopes_bgr=slopes)
            if processed is not None:
                img.resized_raw = processed
            img.converted = True
            img.conversion_inputs = {
                "mode": "bw",
                "bw": (tuple(black), tuple(white) if white is not None else None),
                "fine_rot": img.fine_rotation_angle,
                "density": density,
                "slopes": tuple(slopes) if slopes is not None else None,
            }
            img.update_thumbnail_and_preview()
        except Exception as e:
            print(f"Tether auto-convert failed for {img.file_path}: {e}")
