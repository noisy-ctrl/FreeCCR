"""
Field correction (flat-field) profiles — pure numpy/cv2, no Qt.

A field-correction profile is a smooth per-channel GAIN MAP measured from one
photograph of an evenly lit blank surface (ideally the light source the user
scans on, through the same lens at the same aperture). Multiplying a decoded
frame by that map removes everything multiplicative in the imaging chain at
once: lens vignetting, sensor colour shading, and light-source unevenness.

The map is measured in — and applied to — the decode that `read_image` produces,
so measurement space == application space (see spec/flat-field-correction.md).
Profiles are stored as `.ffc` (JSON + base64 float32 grid) in the per-user
library and activated globally, mirroring how `color_management` holds the
active camera profile so `ccr_image` can reach it without importing the backend.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

# Stored grid long side. The measured field's real bandwidth is a few cycles
# across the frame, so 128 samples over-resolves it while keeping the file
# ~180 KB; it is upsampled bicubically at apply time.
GRID_LONG_SIDE = 128
# Working resolution for validation (§5.2) — high enough that scene content is
# unmistakable, low enough that sensor noise is already averaged out.
ANALYSIS_LONG_SIDE = 256

# Gain clamp. +2 EV is past any real camera-scan rig; beyond it the corner is
# mostly read noise and a botched calibration shot (lens cap, half-covered
# panel) could otherwise produce a map that destroys every image.
DEFAULT_MAX_GAIN = 4.0
MIN_GAIN = 0.25
# Smoothed-field floor (normalised units) — stops a black corner from dividing
# by ~0 before the clamp gets a chance to bite.
_FIELD_FLOOR = 1e-4

FORMAT = "freeccr-field-correction"
FORMAT_VERSION = 1
EXTENSION = ".ffc"

_FULL = 65535.0
# Rows per band in the apply loop: bounds the float32 temporary to a few MB
# even on a 6000x4000 export.
_BAND_ROWS = 512


class FieldProfileError(Exception):
    """Raised when a .ffc file is missing, malformed, or from a newer format
    version, or when a calibration frame cannot yield a usable profile. The
    caller surfaces the message to the user."""


# --------------------------------------------------------------------------- #
# 1. Analysis of a calibration frame
# --------------------------------------------------------------------------- #

@dataclass
class FieldAnalysis:
    """What the wizard's review page reports about a calibration frame."""
    level: float                  # robust central level, 0..1 (max over channels)
    clipped_fraction: float       # worst channel's fraction of pixels at the ceiling
    flatness_residual: float      # relative RMS of (frame - smoothed frame)
    warnings: List[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Whether a profile can be built at all (warnings are advisory; this is
        the hard floor — below 1% of full scale the map is pure noise gain)."""
        return self.level >= 0.01


def _to_float(arr_u16: np.ndarray) -> np.ndarray:
    """16-bit RGB -> float32 in [0,1], always 3-channel."""
    if arr_u16 is None:
        raise FieldProfileError("No image data.")
    a = np.asarray(arr_u16)
    if a.ndim == 2:
        a = np.stack([a, a, a], axis=2)
    if a.ndim != 3 or a.shape[2] < 3:
        raise FieldProfileError(
            f"Expected an RGB image, got shape {getattr(a, 'shape', None)}.")
    return a[..., :3].astype(np.float32) / _FULL


def _resize_long_side(img: np.ndarray, long_side: int) -> np.ndarray:
    """Aspect-preserving downsample with INTER_AREA (a box average — already a
    strong low-pass, which is most of the noise/dust rejection)."""
    h, w = img.shape[:2]
    if max(h, w) <= long_side:
        return img.astype(np.float32, copy=True)
    if h >= w:
        nh, nw = long_side, max(1, int(round(w * long_side / h)))
    else:
        nw, nh = long_side, max(1, int(round(h * long_side / w)))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)


def _pad_odd(a: np.ndarray, p: int) -> np.ndarray:
    """Pad by ODD (antisymmetric) reflection: f(-k) = 2*f(0) - f(k).

    Every OpenCV border mode holds the field flat (REPLICATE) or folds it back
    on itself (REFLECT) outside the frame. Both understate a falloff that is
    still dropping at the frame edge, so any filter biases the border *upward* —
    which reads as less vignetting and under-corrects the corners, the exact
    region this feature exists to measure. Odd reflection continues the local
    slope instead (it is exact for a locally linear field), so filtering is
    unbiased at the border. Measured: it removes a ~1.2% corner error."""
    for axis in (0, 1):
        n = a.shape[axis]
        k = min(p, n - 1)
        if k < 1:
            continue
        first = np.take(a, [0], axis=axis)
        lead = 2.0 * first - np.flip(np.take(a, np.arange(1, k + 1), axis=axis),
                                     axis=axis)
        last = np.take(a, [-1], axis=axis)
        trail = 2.0 * last - np.flip(np.take(a, np.arange(-k - 1, -1), axis=axis),
                                     axis=axis)
        if k < p:                       # tiny input: repeat the outermost value
            lead = np.concatenate([np.repeat(np.take(lead, [0], axis=axis),
                                             p - k, axis=axis), lead], axis=axis)
            trail = np.concatenate([trail, np.repeat(np.take(trail, [-1], axis=axis),
                                                     p - k, axis=axis)], axis=axis)
        a = np.concatenate([lead, a, trail], axis=axis)
    return np.ascontiguousarray(a, dtype=np.float32)


def _smooth(grid: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur that does not bias the frame border (see _pad_odd)."""
    p = max(2, int(np.ceil(3.0 * float(sigma))))
    padded = cv2.GaussianBlur(_pad_odd(grid, p), (0, 0), sigmaX=float(sigma),
                              sigmaY=float(sigma), borderType=cv2.BORDER_REPLICATE)
    return padded[p:p + grid.shape[0], p:p + grid.shape[1]]


# Blemish-rejection window, as a fraction of the grid's long side. 0.13 removes
# dust/fibres/smudges up to ~6% of the frame across; measured fidelity cost on a
# clean field is ~0.1%. Bigger windows reject bigger smudges but a blemish that
# large is no longer distinguishable from real shading.
BLEMISH_WINDOW_FRAC = 0.13


def _median_window(grid: np.ndarray, k: int) -> np.ndarray:
    """k x k median over the grid (build-time only, so a strided view is fine).
    Run per channel to keep the temporary small."""
    p = k // 2
    padded = _pad_odd(grid, p)
    out = np.empty_like(grid, dtype=np.float32)
    for c in range(grid.shape[2]):
        win = np.lib.stride_tricks.sliding_window_view(padded[..., c], (k, k))
        out[..., c] = np.median(win, axis=(-2, -1))
    return out


def _reject_blemishes(grid: np.ndarray) -> np.ndarray:
    """Remove dust, fibres and hot pixels from the measured field without
    touching the falloff underneath: a 5x5 median for isolated hot/dead pixels,
    then a LARGE median for everything bigger.

    A median is the right robust estimator here because it is exact on monotone
    data: a genuine shading edge — the fast corner cut of a mechanical vignette —
    passes through untouched, while an isolated blob smaller than the window is
    replaced by the surrounding field. The alternatives were measured and
    rejected: a Gaussian wide enough to hide a smudge flattens the corners it is
    meant to measure, morphological closing only reaches blemishes smaller than
    its own kernel, and iterated clipped smoothing (reject by DEPTH rather than
    size) mistook a sharp vignette knee for a blemish and smeared it — it
    rewrote 54% of the grid on such a field.

    Baking a blemish into a profile would put a permanent BRIGHT spot in every
    corrected scan, which is much worse than leaving one dark speck alone."""
    g = cv2.medianBlur(_pad_odd(grid, 2), 5)[2:2 + grid.shape[0],
                                             2:2 + grid.shape[1]]
    k = max(3, int(round(GRID_LONG_SIDE * BLEMISH_WINDOW_FRAC)) | 1)
    return _median_window(np.ascontiguousarray(g, dtype=np.float32), k)


def _central_level(grid: np.ndarray) -> np.ndarray:
    """Per-channel median over the central window (middle 10% of frame area).
    This is the normalisation reference: gain == 1 here, in every channel, so a
    profile changes neither exposure nor white balance — only spatial variation."""
    h, w = grid.shape[:2]
    # sqrt(0.10) ~= 0.316 of each dimension -> 10% of the area.
    ch, cw = max(1, int(round(h * 0.316))), max(1, int(round(w * 0.316)))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    window = grid[y0:y0 + ch, x0:x0 + cw]
    return np.median(window.reshape(-1, window.shape[2]), axis=0).astype(np.float32)


def analyze_field(arr_u16: np.ndarray) -> FieldAnalysis:
    """Validate a decoded calibration frame: is it bright enough, unclipped, and
    actually a flat field rather than a photograph of a scene? Warnings are
    advisory (the user's rig, the user's call); `usable` is the hard floor."""
    f = _to_float(arr_u16)
    work = _resize_long_side(f, ANALYSIS_LONG_SIDE)
    smooth = _smooth(work, max(1.0, max(work.shape[:2]) / 12.0))

    level = float(np.max(_central_level(smooth)))
    clipped = float(np.max(np.mean(f >= 0.99, axis=(0, 1))))
    # Relative to the global level rather than per-pixel (a per-pixel ratio
    # explodes on a near-black frame and would report noise as structure).
    residual = float(np.sqrt(np.mean((work - smooth) ** 2)) / max(level, 1e-6))

    warnings: List[str] = []
    if level < 0.05:
        warnings.append(
            "The calibration shot is very dark — the corrected corners will be "
            "noisy. Re-shoot it brighter (around half to three-quarters of full "
            "exposure).")
    elif level > 0.95:
        warnings.append(
            "The calibration shot is over-exposed. Re-shoot it darker so no "
            "area reaches pure white.")
    if clipped > 0.001:
        warnings.append(
            f"{clipped * 100:.1f}% of pixels are clipped to white — the falloff "
            "cannot be measured there. Re-shoot with less exposure.")
    if residual > 0.05:
        warnings.append(
            "This doesn't look like an evenly lit blank surface — it has too "
            "much detail. Shoot the bare light source (slightly defocused), not "
            "a scene or a frame of film.")
    return FieldAnalysis(level=level, clipped_fraction=clipped,
                         flatness_residual=residual, warnings=warnings)


# --------------------------------------------------------------------------- #
# 2. Building the profile
# --------------------------------------------------------------------------- #

@dataclass
class FieldProfile:
    """A measured gain map plus the metadata that says what it was measured on."""
    name: str
    gain: np.ndarray                       # (gh, gw, 3) float32
    aspect: float                          # source frame w/h
    meta: Dict[str, Any] = field(default_factory=dict)
    path: Optional[str] = None
    content_id: Optional[str] = None
    # Resized-map memo, keyed by (w, h). A pure function of (gain, w, h), so a
    # race between decode worker threads only ever duplicates work.
    _cache: Dict[tuple, np.ndarray] = field(default_factory=dict, repr=False,
                                            compare=False)

    @property
    def stats(self) -> Dict[str, Any]:
        return self.meta.get("stats", {})

    def summary(self) -> str:
        """One-line description for the Settings library row."""
        st = self.stats
        cs = st.get("corner_stops") or [0.0, 0.0, 0.0]
        bits = [b for b in (camera_name(self), lens_name(self)) if b]
        head = " · ".join(bits)
        tail = (f"max +{max(cs):.1f} EV · colour spread "
                f"{st.get('cast_stops', 0.0):.2f} EV")
        return f"{head} · {tail}" if head else tail


def _lens_label(cam: Dict[str, Any]) -> str:
    """'105 mm f/8' / the lens model, whichever the EXIF actually had."""
    parts = []
    if cam.get("lens_model"):
        parts.append(str(cam["lens_model"]))
    if cam.get("focal_length"):
        parts.append(f"{float(cam['focal_length']):g} mm")
    if cam.get("aperture"):
        parts.append(f"f/{float(cam['aperture']):g}")
    return " ".join(parts)


def camera_name(p) -> str:
    """The camera a flat was shot with (spec/flat-field-info.md §3): the name
    typed for it, else its metadata's make and model, else ""."""
    if p is None:
        return ""
    typed = ((p.meta or {}).get("details") or {}).get("camera")
    if typed:
        return str(typed)
    cam = (p.meta or {}).get("camera") or {}
    make, model = str(cam.get("make") or "").strip(), str(cam.get("model") or "").strip()
    if model and make and model.lower().startswith(make.lower()):
        return model
    return " ".join(b for b in (make, model) if b)


def lens_name(p) -> str:
    """The lens: the name typed for it, else the metadata's lens, else ""
    (an adapted lens records none)."""
    if p is None:
        return ""
    typed = ((p.meta or {}).get("details") or {}).get("lens")
    if typed:
        return str(typed)
    return _lens_label((p.meta or {}).get("camera") or {})


def typed_details(p) -> Dict[str, str]:
    """{"camera", "lens"} as typed for the flat ("" where nothing was)."""
    return _typed((p.meta or {}) if p is not None else {})


def _typed(meta) -> Dict[str, str]:
    d = (meta or {}).get("details") or {}
    return {"camera": str(d.get("camera") or ""), "lens": str(d.get("lens") or "")}


def with_details(meta: Optional[Dict[str, Any]], camera="", lens="") -> Dict[str, Any]:
    """`meta` with the typed camera and lens (blank ones dropped)."""
    meta = dict(meta or {})
    d = {k: str(v).strip() for k, v in (("camera", camera), ("lens", lens))
         if v and str(v).strip()}
    if d:
        meta["details"] = d
    else:
        meta.pop("details", None)
    return meta


def remembered_details() -> Dict[str, str]:
    """The camera and lens last typed for a flat (QSettings flat/camera,
    flat/lens): they fill the next one's fields."""
    try:
        from PySide6.QtCore import QSettings
        st = QSettings("FreeCCR", "FreeCCR")
        return {k: str(st.value(f"flat/{k}", "") or "") for k in ("camera", "lens")}
    except Exception:
        return {"camera": "", "lens": ""}


def remember_details(camera="", lens="") -> None:
    try:
        from PySide6.QtCore import QSettings
        st = QSettings("FreeCCR", "FreeCCR")
        st.setValue("flat/camera", str(camera or "").strip())
        st.setValue("flat/lens", str(lens or "").strip())
    except Exception:
        pass


def set_details(p: "FieldProfile", camera="", lens="") -> bool:
    """Name the flat's camera and lens and save it (when it has a file).
    Its content id is kept, so naming them never flags a frame for
    re-grading. True when anything changed."""
    if p is None:
        return False
    new = with_details(p.meta, camera, lens)
    if _typed(new) == typed_details(p):
        return False
    if p.content_id and not new.get("content_id"):
        new["content_id"] = p.content_id
    p.meta = new
    if p.path:
        save_profile(p, p.path)
    return True


def _corner_stops(gain: np.ndarray) -> List[float]:
    """Per-channel mean gain over the four corner regions (outer 5% x 5%), in
    EV — 'how many stops of falloff this profile lifts out of the corners'."""
    h, w = gain.shape[:2]
    ch, cw = max(2, int(round(h * 0.05))), max(2, int(round(w * 0.05)))
    corners = np.concatenate([
        gain[:ch, :cw].reshape(-1, 3), gain[:ch, -cw:].reshape(-1, 3),
        gain[-ch:, :cw].reshape(-1, 3), gain[-ch:, -cw:].reshape(-1, 3)])
    return [float(np.log2(max(v, 1e-6))) for v in corners.mean(axis=0)]


def build_profile(arr_u16: np.ndarray, name: str, *,
                  meta: Optional[Dict[str, Any]] = None,
                  max_gain: float = DEFAULT_MAX_GAIN) -> FieldProfile:
    """Measure the gain map of a decoded calibration frame.

    INTER_AREA downsample -> 3x3 median (hot pixels, surviving dust specks) ->
    Gaussian blur (the field's true bandwidth) -> divide the central reference
    level by it. See spec/flat-field-correction.md §5.3."""
    f = _to_float(arr_u16)
    src_h, src_w = f.shape[:2]
    analysis = analyze_field(arr_u16)
    if not analysis.usable:
        raise FieldProfileError(
            "The calibration shot is essentially black — nothing to measure. "
            "Shoot the lit surface itself, exposed to about half to "
            "three-quarters of full brightness.")

    # Blemish rejection does the heavy lifting; the Gaussian that follows only
    # de-staircases the median, so it stays MINIMAL. A blur wide enough to hide
    # dust on its own would measurably flatten the corners it is supposed to
    # measure (blurring a curved field biases it by ~sigma^2/2 * laplacian, worst
    # exactly at the corners): at sigma=2 a sharp mechanical-vignette knee was
    # reproduced to 10%, at sigma=1 to 6%.
    grid = _reject_blemishes(_resize_long_side(f, GRID_LONG_SIDE))
    smooth = _smooth(grid, max(1.0, GRID_LONG_SIDE / 128.0))

    ref = _central_level(smooth)                       # (3,) gain == 1 here
    gain = ref[None, None, :] / np.maximum(smooth, _FIELD_FLOOR)
    reached = float(np.max(gain))
    gain = np.clip(gain, MIN_GAIN, float(max_gain)).astype(np.float32)
    gain = np.ascontiguousarray(gain)

    corner = _corner_stops(gain)
    stats = {
        "max_gain": round(float(np.max(gain)), 4),
        "max_gain_uncapped": round(reached, 4),
        "clamped": bool(reached > max_gain + 1e-6),
        "corner_stops": [round(c, 4) for c in corner],
        "cast_stops": round(float(max(corner) - min(corner)), 4),
        "level": round(analysis.level, 4),
        "clipped_fraction": round(analysis.clipped_fraction, 6),
        "flatness_residual": round(analysis.flatness_residual, 6),
    }
    full_meta: Dict[str, Any] = dict(meta or {})
    full_meta["stats"] = stats
    full_meta["max_gain_cap"] = float(max_gain)
    full_meta.setdefault("created", datetime.now().isoformat(timespec="seconds"))
    full_meta.setdefault("warnings", analysis.warnings)
    return FieldProfile(name=name or "Field correction", gain=gain,
                        aspect=float(src_w) / float(max(1, src_h)),
                        meta=full_meta)


# --------------------------------------------------------------------------- #
# 3. Storage (.ffc = JSON + base64 float32 grid)
# --------------------------------------------------------------------------- #

def _profile_dict(p: FieldProfile) -> Dict[str, Any]:
    g = np.ascontiguousarray(p.gain, dtype="<f4")
    gh, gw = g.shape[:2]
    doc = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "name": p.name,
        "aspect": round(float(p.aspect), 6),
        "grid": {"w": int(gw), "h": int(gh), "channels": 3, "dtype": "float32",
                 "data": base64.b64encode(g.tobytes()).decode("ascii")},
    }
    doc.update({k: v for k, v in p.meta.items() if k not in doc})
    return doc


def save_profile(profile: FieldProfile, path: str) -> str:
    """Write a .ffc. Returns the path actually written."""
    path = os.path.normpath(path)
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    blob = json.dumps(_profile_dict(profile), indent=1, ensure_ascii=False)
    with open(path, "wb") as f:
        f.write(blob.encode("utf-8"))
    profile.path = path
    # a flat whose camera / lens were named keeps the id it had (set_details)
    profile.content_id = (profile.meta or {}).get("content_id") or _content_id(path)
    return path


def _content_id(path: str) -> Optional[str]:
    """Short content hash — makes the grading signature distinguish two profiles
    that share a name."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except OSError:
        return None


def load_profile(path: str) -> FieldProfile:
    """Read and validate a .ffc file. Raises FieldProfileError with a message
    meant for the user."""
    try:
        with open(os.path.normpath(path), "rb") as f:
            raw = f.read()
    except OSError as e:
        raise FieldProfileError(f"Could not read the profile: {e}") from e
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise FieldProfileError(
            f"This is not a valid field-correction profile: {e}") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise FieldProfileError(
            "This file is not a FreeCCR field-correction profile.")
    version = doc.get("version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise FieldProfileError(
            f"This profile was written by a newer version of FreeCCR "
            f"(format {version}). Update FreeCCR to use it.")

    grid = doc.get("grid")
    if not isinstance(grid, dict):
        raise FieldProfileError("The profile has no gain map.")
    try:
        gw, gh = int(grid["w"]), int(grid["h"])
        data = base64.b64decode(grid["data"], validate=True)
        gain = np.frombuffer(data, dtype="<f4")
    except Exception as e:
        raise FieldProfileError(f"The profile's gain map is corrupt: {e}") from e
    if gw < 2 or gh < 2 or gain.size != gw * gh * 3:
        raise FieldProfileError("The profile's gain map is the wrong size.")
    gain = np.ascontiguousarray(gain.reshape(gh, gw, 3).astype(np.float32))
    if not np.all(np.isfinite(gain)) or float(np.min(gain)) <= 0.0:
        raise FieldProfileError("The profile's gain map has invalid values.")

    meta = {k: v for k, v in doc.items()
            if k not in ("format", "version", "name", "aspect", "grid")}
    aspect = doc.get("aspect")
    try:
        aspect = float(aspect)
    except (TypeError, ValueError):
        aspect = float(gw) / float(gh)
    return FieldProfile(
        name=str(doc.get("name") or os.path.splitext(os.path.basename(path))[0]),
        gain=gain, aspect=aspect, meta=meta, path=path,
        content_id=str(meta.get("content_id") or "") or _content_id(path))


# --------------------------------------------------------------------------- #
# 4. Applying a profile
# --------------------------------------------------------------------------- #

def gain_for_size(profile: FieldProfile, w: int, h: int) -> np.ndarray:
    """The gain map resized to (w, h), memoised per size. Bicubic: the map is
    smooth, and bicubic avoids the faint slope-discontinuity facets bilinear can
    leave when a 128-px grid is stretched to 6000 px."""
    key = (int(w), int(h))
    cached = profile._cache.get(key)
    if cached is not None:
        return cached
    g = profile.gain
    out = (g if g.shape[:2] == (h, w) else
           cv2.resize(g, (int(w), int(h)), interpolation=cv2.INTER_CUBIC))
    out = np.ascontiguousarray(out, dtype=np.float32)
    if len(profile._cache) >= 4:                # preview / thumb / zoom / export
        profile._cache.pop(next(iter(profile._cache)), None)
    profile._cache[key] = out
    return out


def _srgb_to_linear(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92,
                    np.power((v + 0.055) / 1.055, 2.4))


def _linear_to_srgb(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.0031308, v * 12.92,
                    1.055 * np.power(np.maximum(v, 0.0), 1.0 / 2.4) - 0.055)


def apply_field(arr_u16: np.ndarray, profile: Optional[FieldProfile], *,
                encoded: bool = False, mono: bool = False) -> np.ndarray:
    """Multiply a decoded 16-bit RGB frame by the profile's gain map.

    `encoded=True` for an sRGB-gamma-encoded decode (Positive mode): linearise,
    multiply, re-encode — a gain measured in linear light and applied straight to
    gamma-encoded data OVER-corrects, by roughly the gamma exponent (multiplying
    an encoded value by g scales the light it stands for by g^2.4).
    `mono=True` for monochrome/grayscale sources: the three channel gains are
    averaged so the frame stays neutral.

    Runs in horizontal bands so a full-resolution export never materialises a
    float32 copy of the whole frame."""
    if profile is None or arr_u16 is None:
        return arr_u16
    a = np.asarray(arr_u16)
    if a.ndim != 3 or a.shape[2] != 3:
        return arr_u16                        # not an RGB frame — leave it alone
    h, w = a.shape[:2]
    gain = gain_for_size(profile, w, h)
    if mono:
        gain = gain.mean(axis=2, keepdims=True)

    out = np.empty_like(a, dtype=np.uint16)
    for y in range(0, h, _BAND_ROWS):
        y1 = min(h, y + _BAND_ROWS)
        band = a[y:y1].astype(np.float32)
        g = gain[y:y1]
        if encoded:
            # Only this path needs normalised units (the sRGB transfer functions).
            band /= _FULL
            band = _linear_to_srgb(_srgb_to_linear(band) * g)
            band *= _FULL
        else:
            # Straight multiply in the frame's own units — no /65535 round trip,
            # which would cost a rounding step for nothing.
            band *= g
        np.clip(band, 0.0, _FULL, out=band)
        out[y:y1] = band.astype(np.uint16)
    return out


# --------------------------------------------------------------------------- #
# 5. Global active profile (mirrors color_management's module globals, so
#    ccr_image can reach it without importing the backend singleton).
# --------------------------------------------------------------------------- #

_active_profile: Optional[FieldProfile] = None


def set_active_profile(profile: Optional[FieldProfile]) -> None:
    global _active_profile
    _active_profile = profile


def get_active_profile() -> Optional[FieldProfile]:
    return _active_profile


def field_profile_active() -> bool:
    return _active_profile is not None


def frame_field_signature(img) -> str:
    """The id of the flat a decode of THIS frame would get now (the frame's
    part of its grading signature, spec/preset-flat-fields.md §5)."""
    p = profile_for(img)
    return "none" if p is None else "ff:" + (p.content_id or p.name or "?")


def active_field_signature() -> str:
    """Stable id of the field correction a decode would get now — composed into
    the per-image grading signature so a change flags the affected thumbnails."""
    lib = library_signature()
    if _active_profile is None:
        return lib or "none"
    base = "ff:" + (_active_profile.content_id or _active_profile.name or "?")
    return f"{base}|{lib}" if lib else base


# --------------------------------------------------------------------------- #
# 6. Capture-side helpers (used by the wizard)
# --------------------------------------------------------------------------- #

def decode_calibration(path: str, sample_max: int = 1024) -> Optional[np.ndarray]:
    """Decode a calibration shot to BARE device RGB — camera-native, raw-linear,
    unbalanced, no camera profile and (critically) no field correction, so
    profiles can never compound. Same decode contract as the IT8 profiling path
    (spec/it8-camera-profile.md §5.1); `apply_input_icc=False` is what pins it.

    Built on a bare CCRImage (__new__) so the heavyweight __init__ decode +
    thumbnail work is skipped."""
    from core.ccr_image import CCRImage
    img = CCRImage.__new__(CCRImage)
    img.source_ops = []
    return img.read_image(path, preview=True, max_long_side=sample_max,
                          positive_override=False, apply_input_icc=False)


def source_metadata(path: str) -> Dict[str, Any]:
    """Camera/lens EXIF for the profile's metadata block (best effort — a
    calibration shot with no readable EXIF still makes a valid profile)."""
    cam: Dict[str, Any] = {}
    try:
        from core.ccr_image import CCRImage
        info = CCRImage.get_camera_and_lens_for_lensfun(path) or {}
        for key in ("camera_make", "camera_model", "lens_make", "lens_model",
                    "focal_length", "aperture"):
            val = info.get(key)
            if val not in (None, ""):
                cam[key.replace("camera_", "")] = val
    except Exception as e:                     # EXIF is a nicety, never a blocker
        logging.debug(f"No EXIF for calibration shot {path}: {e}")
    return {"source": os.path.basename(path), "camera": cam}


def suggested_name(meta: Dict[str, Any]) -> str:
    """Default profile name from what we know about the shot."""
    cam = (meta or {}).get("camera", {}) or {}
    lens = _lens_label(cam)
    body = cam.get("model") or cam.get("make") or ""
    head = " ".join(b for b in (body, lens) if b).strip()
    return f"Field {head} {datetime.now():%Y-%m-%d}".replace("  ", " ").strip() \
        if head else f"Field correction {datetime.now():%Y-%m-%d}"


# --------------------------------------------------------------------------- #
# 7. Flat-field sets: by light and format, tracked by the carrier opening
#    (spec/ir-dust-and-flat-sets.md §5)
# --------------------------------------------------------------------------- #
LIGHTS = ("white", "rgb", "red", "green", "blue", "ir")
LIGHT_LABELS = {"white": "White light", "rgb": "RGB (trichrome)", "red": "Red",
                "green": "Green", "blue": "Blue", "ir": "Infrared"}
OPENING_INSET = 0.015      # the flat is measured this far inside its opening
TRACK_S = (0.85, 1.15)     # a scale outside this is a mis-find: untracked
TRACK_SHIFT = 0.15         # nor a shift over this (of the frame)

_library: List[FieldProfile] = []


def profile_light(p: Optional[FieldProfile]) -> Optional[str]:
    v = (p.meta or {}).get("light") if p is not None else None
    return v if v in LIGHTS else None


def profile_format(p: Optional[FieldProfile]) -> Optional[str]:
    # "film_format": the file's own "format" key names the file type, so a
    # meta "format" never reached the disk (fixed 2026-11-19)
    return ((p.meta or {}).get("film_format") or None) if p is not None else None


def profile_opening(p: Optional[FieldProfile]) -> Optional[Dict[str, float]]:
    o = (p.meta or {}).get("opening") if p is not None else None
    return dict(o) if isinstance(o, dict) and o else None


def set_library(profiles: List[FieldProfile]) -> None:
    """Every profile in the library, for the per-frame choice."""
    global _library
    _library = list(profiles or [])


def library() -> List[FieldProfile]:
    return list(_library)


def _tagged() -> List[FieldProfile]:
    return [p for p in _library if profile_light(p)]


def frame_light(img) -> str:
    """The light a frame was scanned under: a 3-way merge is RGB, a frame
    with a recorded light (`img.scan_light`) that light, otherwise white."""
    if getattr(img, "is_merged", False) or getattr(img, "merge_sources", None):
        return "rgb"
    v = getattr(img, "scan_light", None)
    return v if v in LIGHTS else "white"


def frame_format(img) -> Optional[str]:
    """The frame's film format: set in the Crop panel, else its capture
    guide's."""
    f = getattr(img, "film_format", None)
    if f:
        return f
    g = getattr(img, "capture_guide", None)
    if isinstance(g, dict) and g.get("format"):
        return g["format"]
    return None


# --------------------------------------------------------------------------- #
# A film preset's flats (spec/preset-flat-fields.md): a preset points at a
# library flat per scan type, by file name; the main window pushes the pins.
# --------------------------------------------------------------------------- #
SCANS = ("trichrome", "colour", "bw")
SCAN_LIGHTS = {"trichrome": ("rgb",), "colour": ("white",),
               "bw": ("white", "red", "green", "blue")}
NO_FLAT = "none"
NOTE_LIMIT = 2000

_preset_flats: Dict[str, Dict[str, str]] = {}
_capture_notes: "Dict[str, tuple]" = {}


def set_preset_flats(mapping) -> None:
    """{preset id: {scan: file name | "none"}} (`film_presets.flats_map`)."""
    global _preset_flats
    _preset_flats = {str(k): dict(v) for k, v in (mapping or {}).items()
                     if isinstance(v, dict) and v}


def preset_flats() -> Dict[str, Dict[str, str]]:
    return {k: dict(v) for k, v in _preset_flats.items()}


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def note_capture(paths, preset_id, scan) -> None:
    """A capture's files, noted with the roll's preset and scan type before
    the worker decodes them (the frame is marked with its preset only after)."""
    if not preset_id:
        return
    for p in paths or ():
        if p:
            _capture_notes.pop(_norm(p), None)
            _capture_notes[_norm(p)] = (str(preset_id), scan if scan in SCANS else None)
    while len(_capture_notes) > NOTE_LIMIT:
        _capture_notes.pop(next(iter(_capture_notes)))


def _note_of(img):
    paths = list(getattr(img, "merge_sources", None) or []) + [getattr(img, "file_path", None)]
    for p in paths:
        if p and _norm(p) in _capture_notes:
            return _capture_notes[_norm(p)]
    return None


def frame_preset(img) -> Optional[str]:
    pid = getattr(img, "film_preset", None)
    if pid:
        return pid
    note = _note_of(img)
    return note[0] if note else None


def frame_scan(img) -> str:
    """How the frame was scanned: trichrome / colour / bw."""
    if getattr(img, "is_merged", False) or getattr(img, "merge_sources", None):
        return "trichrome"
    ct = getattr(img, "capture_type", None)
    if ct in SCANS:
        return ct
    note = _note_of(img)
    if note and note[1]:
        return note[1]
    return "bw" if getattr(img, "color_profile", None) == "bw" else "colour"


def flat_ref(p: Optional[FieldProfile]) -> Optional[str]:
    """How a preset names a library flat: its file's name."""
    return os.path.basename(p.path) if p is not None and p.path else None


def flat_by_ref(ref) -> Optional[FieldProfile]:
    if not ref or ref == NO_FLAT:
        return None
    for p in _library:
        if flat_ref(p) == ref:
            return p
    return None


def flats_for_scan(scan) -> List[FieldProfile]:
    """The library's flats a preset may pin for a scan type: those lit by
    one of its lights, and untagged ones."""
    lights = SCAN_LIGHTS.get(scan, ())
    return [p for p in _library
            if not profile_light(p) or profile_light(p) in lights]


def pinned(img, opening=None):
    """("auto" | "none" | "flat" | "missing" | "format", profile or None).
    "format": the pinned flat is of another format than the frame, or its
    opening has another shape, so the automatic choice is used
    (spec/flat-format-match.md)."""
    pins = _preset_flats.get(frame_preset(img) or "")
    ref = (pins or {}).get(frame_scan(img)) if pins else None
    if not ref:
        return "auto", None
    if ref == NO_FLAT:
        return "none", None
    p = flat_by_ref(ref)
    if p is None:
        return "missing", None
    fmt, pf = frame_format(img), profile_format(p)
    op = opening if opening is not None else getattr(img, "flat_opening", None)
    if (fmt and pf and pf != fmt) or opening_fits(p, op) is False:
        return "format", p
    return "flat", p


ASPECT_TOL = 0.08          # two openings whose axes scale this differently: another shape


def opening_fits(p: Optional[FieldProfile], frame_sides) -> Optional[bool]:
    """Whether a flat's carrier opening has the frame's shape and size (a move
    the rail could make): True / False, None when either isn't known. A 6x4.5
    carrier's opening stood up doesn't fit a 6x7 one lying down."""
    ref = profile_opening(p) if p is not None else None
    if not ref or not frame_sides:
        return None
    scales = []
    for a, b in (("left", "right"), ("top", "bottom")):
        if all(k in ref and k in frame_sides for k in (a, b)):
            span_ref = ref[b] - ref[a]
            span = frame_sides[b] - frame_sides[a]
            if span_ref > 0.02 and span > 0.02:
                scales.append(span / span_ref)
    if not scales:
        return None
    if any(not (TRACK_S[0] <= x <= TRACK_S[1]) for x in scales):
        return False
    if len(scales) == 2 and abs(scales[0] / scales[1] - 1) > ASPECT_TOL:
        return False
    return True


def _newest(pool):
    return max(pool, key=lambda p: str((p.meta or {}).get("created", "")))


def needs_opening() -> bool:
    """Whether choosing a frame's flat looks at its carrier opening."""
    return any(profile_opening(p) for p in _library)


def profile_for(img, opening=None) -> Optional[FieldProfile]:
    """The flat for a frame (spec/flat-format-match.md): its preset's flat for
    its scan type when the preset names one of the frame's format
    (spec/preset-flat-fields.md), else a library flat of its light and
    format, else a format-less one whose opening fits; with the format
    unknown, the flat whose carrier opening has the frame's shape. Never
    another format's flat: a carrier stands one format up and another lying
    down. Nothing that fits: the active profile (with no tagged flat in the
    library: always the active profile, as before). `opening`: the frame's
    carrier opening (else the one kept on it)."""
    if img is not None and _preset_flats:
        state, p = pinned(img, opening)
        if state == "flat":
            return p
        if state == "none":
            return None
    tagged = _tagged()
    if not tagged or img is None:
        return _active_profile
    light = frame_light(img)
    cands = [p for p in tagged if profile_light(p) == light]
    if not cands:
        return _active_profile
    op = opening if opening is not None else getattr(img, "flat_opening", None)
    fmt = frame_format(img)
    free = [p for p in cands if not profile_format(p) and opening_fits(p, op) is not False]
    if fmt:
        exact = [p for p in cands if profile_format(p) == fmt]
        if exact:
            return _newest([p for p in exact if opening_fits(p, op) is not False] or exact)
        return _newest(free) if free else _active_profile
    # the format unknown: the flat whose opening has the frame's shape
    fit = [p for p in cands if opening_fits(p, op) is True]
    if fit:
        return _newest(fit)
    if free:
        return _newest(free)
    if (len({profile_format(p) for p in cands}) == 1
            and all(opening_fits(p, op) is not False for p in cands)):
        return _newest(cands)            # one format, nothing says otherwise
    return _active_profile


def _to_view8(arr_u16: np.ndarray) -> np.ndarray:
    """A linear decode as an 8-bit grey picture the opening finder reads."""
    a = np.asarray(arr_u16)
    small = _resize_long_side(a, 640) if max(a.shape[:2]) > 640 else a
    g = small.astype(np.float32)
    if g.ndim == 3:
        g = g.mean(axis=2)
    top = float(np.percentile(g, 99.5)) or 1.0
    return (np.clip(g / top, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def opening_of(arr_u16: np.ndarray) -> Optional[Dict[str, float]]:
    """The carrier opening's sides (fractions of the frame) in a decode, or
    None when no mask is in view."""
    from core import guide_tracker
    try:
        op = guide_tracker.find_opening(_to_view8(arr_u16))
    except Exception:
        return None
    if op is None or not op.sides:
        return None
    return {k: float(v) for k, v in op.sides.items()}


def opening_mask(sides: Dict[str, float], w: int, h: int,
                 inset: float = OPENING_INSET) -> np.ndarray:
    """Inside the opening (missing sides = the frame's edge), inset a little."""
    x0 = (sides.get("left", 0.0) + inset) * w
    x1 = (sides.get("right", 1.0) - inset) * w
    y0 = (sides.get("top", 0.0) + inset) * h
    y1 = (sides.get("bottom", 1.0) - inset) * h
    m = np.zeros((h, w), bool)
    m[max(0, int(np.ceil(y0))):min(h, int(y1)), max(0, int(np.ceil(x0))):min(w, int(x1))] = True
    return m


def fill_outside(arr_u16: np.ndarray, sides: Dict[str, float]) -> np.ndarray:
    """A flat shot through the empty carrier: outside the opening is the
    black mask. Fill it from the inside (each pixel takes the nearest inside
    value, smoothed), so the measurement sees only the opening and the map
    carries on smoothly past it."""
    a = np.asarray(arr_u16)
    h, w = a.shape[:2]
    m = opening_mask(sides, w, h)
    if m.all() or not m.any():
        return a
    ys, xs = np.nonzero(m)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    inner = a[y0:y1, x0:x1]
    out = cv2.copyMakeBorder(inner, y0, h - y1, x0, w - x1, cv2.BORDER_REPLICATE)
    return out.astype(a.dtype)


def build_tagged_profile(arr_u16: np.ndarray, name: str, *, light: str,
                         fmt: Optional[str] = None,
                         meta: Optional[Dict[str, Any]] = None,
                         max_gain: float = DEFAULT_MAX_GAIN) -> FieldProfile:
    """`build_profile` for a flat of a set: finds the carrier opening (if one
    is in view), measures only inside it, and records light, format and the
    opening in the profile."""
    if light not in LIGHTS:
        raise FieldProfileError(f"Unknown light: {light}")
    sides = opening_of(arr_u16)
    src = fill_outside(arr_u16, sides) if sides else arr_u16
    meta = dict(meta or {})
    meta.update({"light": light, "film_format": fmt or None, "opening": sides or None})
    prof = build_profile(src, name, meta=meta, max_gain=max_gain)
    share = coverage(prof)
    if share is not None and share < COVER_MIN:
        prof.meta.setdefault("warnings", [])
        prof.meta["warnings"] = list(prof.meta["warnings"]) + [
            f"The light falls to {share * 100:.0f} % of its brightest at the edge of "
            "the carrier's opening: is it big enough for this format? Where it is "
            "darker than the flat can lift, the frame stays dark."]
    return prof


COVER_MIN = 0.30           # light at the opening's edge under this share of its peak: warned


def coverage(p: FieldProfile) -> Optional[float]:
    """The dimmest light inside the flat's opening as a share of its
    brightest (each channel; the lowest), or None with no opening."""
    sides = profile_opening(p)
    if not sides or p.gain is None:
        return None
    g = np.asarray(p.gain, np.float32)
    h, w = g.shape[:2]
    m = opening_mask(sides, w, h)
    if not m.any():
        return None
    light = 1.0 / np.maximum(g.reshape(h, w, -1), 1e-6)
    inside = light[m]
    return float(np.min(inside.min(axis=0) / np.maximum(inside.max(axis=0), 1e-6)))


def track(profile: Optional[FieldProfile], frame_sides: Optional[Dict[str, float]]):
    """(s, dx, dy) taking the flat's opening to the frame's (scale about the
    centre, shift in fractions), or None to apply the flat untracked: no
    opening on either side, or an answer too big to be the rail moving."""
    return track_state(profile, frame_sides)[0]


def track_state(profile: Optional[FieldProfile], frame_sides: Optional[Dict[str, float]]):
    """(move, state): `track`'s move and what happened (spec/flat-field-info.md
    §1): "moved" (followed the carrier), "same" (already lined up), "not
    found" (the frame's opening wasn't found, or the move was refused: the
    flat applied as shot), None (the flat has no opening of its own)."""
    ref = profile_opening(profile)
    if not ref:
        return None, None
    if not frame_sides:
        return None, "not found"
    from core import guide_tracker
    sol = guide_tracker.solve(ref, frame_sides)
    if sol is None:
        return None, "not found"
    s, dx, dy, _n = sol
    if not (TRACK_S[0] <= s <= TRACK_S[1]) or max(abs(dx), abs(dy)) > TRACK_SHIFT:
        return None, "not found"
    if abs(s - 1) < 1e-4 and max(abs(dx), abs(dy)) < 1e-4:
        return None, "same"
    return (float(s), float(dx), float(dy)), "moved"


def flat_label(p: Optional[FieldProfile]) -> str:
    """'White light flat · 6x4.5' for a flat of a set, else its name."""
    if p is None:
        return ""
    light = profile_light(p)
    if not light:
        return p.name or "Field correction"
    from core import film_formats
    text = LIGHT_LABELS.get(light, light).split(" (")[0] + " flat"
    fmt = profile_format(p)
    return text + (" · " + film_formats.short_label(fmt) if fmt else "")


def flat_id(p: Optional[FieldProfile]) -> Optional[str]:
    """The flat's id as the grading signature knows it (None for no flat)."""
    return None if p is None else (p.content_id or p.name or "?")


def applied_record(p: Optional[FieldProfile], carrier=None) -> Optional[Dict[str, Any]]:
    """What a decode applied, kept on the frame as `flat_applied`."""
    if p is None:
        return None
    return {"name": p.name, "id": flat_id(p), "label": flat_label(p),
            "light": profile_light(p), "format": profile_format(p),
            "camera": camera_name(p), "lens": lens_name(p), "carrier": carrier}


def warp_gain(gain: np.ndarray, move) -> np.ndarray:
    """The gain map moved as the opening moved: a point at fraction x of the
    flat lands at 0.5 + s(x - 0.5) + dx of the frame."""
    if move is None:
        return gain
    s, dx, dy = move
    h, w = gain.shape[:2]
    m = np.float32([[s, 0, 0.5 * w * (1 - s) + dx * w],
                    [0, s, 0.5 * h * (1 - s) + dy * h]])
    out = cv2.warpAffine(gain, m, (w, h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    return out.reshape(gain.shape).astype(np.float32)


def tracked_profile(profile: FieldProfile, move) -> FieldProfile:
    """The profile with its map moved (a copy; the library's is untouched)."""
    if move is None:
        return profile
    key = ("_tracked",) + tuple(round(v, 5) for v in move)
    hit = profile._cache.get(key)
    if hit is None:
        hit = FieldProfile(name=profile.name, gain=warp_gain(profile.gain, move),
                           aspect=profile.aspect, meta=profile.meta,
                           path=profile.path, content_id=profile.content_id)
        if len(profile._cache) >= 8:
            profile._cache.pop(next(iter(profile._cache)), None)
        profile._cache[key] = hit
    return hit


def library_signature() -> str:
    """Stable id of the tagged library (a change flags the frames)."""
    tagged = _tagged()
    if not tagged:
        return ""
    ids = sorted(p.content_id or p.name or "?" for p in tagged)
    return "set:" + hashlib.sha1("|".join(ids).encode()).hexdigest()[:10]
