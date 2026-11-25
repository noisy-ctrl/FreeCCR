"""Compare a frame with a reference picture (spec/reference-compare.md):
read the reference in true colour and line it up with the frame. Qt-free.

``load_reference(path)``  reads a JPEG / TIFF / PNG (8 or 16 bit) into 8-bit
sRGB, converting its embedded colour profile with Little-CMS (imagecodecs) in
float from the file's own bit depth (a matrix-shaper profile is done by hand if
that isn't available). Then rounded to 8 bits: the display is 8-bit sRGB.

``line_up(ref, shown)``  finds where the reference sits on the picture
shown for the frame: SIFT + RANSAC, as itself and mirrored, a homography
held to near-similarity (or a partial affine). The answer is normalised
(reference fractions -> shown fractions) so any size of either reuses it.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

MAX_SIDE = 3000          # the reference is kept at most this big (display only)
MATCH_SIDE = 1200        # both pictures are matched at this long side
N_FEATURES = 4000
RATIO = 0.75
RANSAC_PX = 3.0
MIN_INLIERS = 25
MIN_INLIER_SHARE = 0.25
MAX_ERROR_PX = 2.5
MAX_SCALE_SKEW = 0.25    # the two scales of the found shape may differ this much
MAX_ANGLE_DEV = 10.0     # degrees from square at every corner

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


@dataclass
class Reference:
    rgb: np.ndarray                 # HxWx3 uint8, sRGB
    full_size: Tuple[int, int]      # (w, h) of the file
    profile: Optional[str]          # the embedded profile's name, None = none
    bits: int                       # 8 or 16
    converted: bool = True          # False: a profile was there and couldn't be applied


@dataclass
class LineUp:
    H: np.ndarray                   # 3x3: reference fractions -> shown fractions
    mirrored: bool
    inliers: int
    error_px: float                 # median reprojection error at MATCH_SIDE
    method: str                     # "homography" | "similarity"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def _orient(a: np.ndarray, orientation: int) -> np.ndarray:
    """EXIF/TIFF orientation 1..8 applied (so the picture stands as shown)."""
    o = int(orientation or 1)
    if o == 2:
        return a[:, ::-1]
    if o == 3:
        return a[::-1, ::-1]
    if o == 4:
        return a[::-1, :]
    if o == 5:
        return np.swapaxes(a, 0, 1)
    if o == 6:
        return np.rot90(a, -1)
    if o == 7:
        return np.swapaxes(a, 0, 1)[::-1, ::-1]
    if o == 8:
        return np.rot90(a, 1)
    return a


def _as_float_rgb(arr: np.ndarray) -> Tuple[np.ndarray, int, bool]:
    """(HxWx3 or HxW float32 0..1, bits, grey) from whatever a reader gave."""
    a = np.asarray(arr)
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[2] not in (1, 3, 4):
        a = np.moveaxis(a, 0, -1)                   # planar TIFF
    if a.ndim == 3 and a.shape[2] == 1:
        a = a[:, :, 0]
    if a.ndim == 3 and a.shape[2] >= 4:
        a = a[:, :, :3]                             # alpha (or a 4th sample) dropped
    if a.ndim == 3 and a.shape[2] == 2:
        a = a[:, :, 0]                              # grey + alpha
    if a.dtype == np.uint8:
        f, bits = a.astype(np.float32) / 255.0, 8
    elif a.dtype == np.uint16:
        f, bits = a.astype(np.float32) / 65535.0, 16
    elif a.dtype == bool:
        f, bits = a.astype(np.float32), 8
    else:
        f, bits = np.clip(a.astype(np.float32), 0.0, 1.0), 16
    return f, bits, f.ndim == 2


def _read_tiff(path):
    import tifffile
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        arr = page.asarray()
        icc = page.tags.get(34675)
        icc = bytes(icc.value) if icc is not None else None
        o = page.tags.get(274)
        orient = int(o.value) if o is not None else 1
        photometric = getattr(page, "photometric", None)
    if photometric is not None and int(photometric) == 0:     # MINISWHITE
        arr = (np.iinfo(arr.dtype).max - arr) if arr.dtype.kind == "u" else 1.0 - arr
    return _orient(arr, orient), icc


def jpeg_icc(data: bytes) -> Optional[bytes]:
    """The ICC profile carried in a JPEG's APP2 segments (in order), or None."""
    if data[:2] != b"\xff\xd8":
        return None
    pos, parts = 2, {}
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker in (0xD9, 0xDA):              # end, or the picture starts
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        size = int.from_bytes(data[pos + 2:pos + 4], "big")
        seg = data[pos + 4:pos + 2 + size]
        if marker == 0xE2 and seg[:12] == b"ICC_PROFILE\x00" and len(seg) > 14:
            parts[seg[12]] = seg[14:]
        pos += 2 + size
    if not parts:
        return None
    return b"".join(parts[k] for k in sorted(parts))


def png_icc(data: bytes) -> Optional[bytes]:
    """The ICC profile in a PNG's iCCP chunk, or None."""
    import zlib
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    while pos + 8 <= len(data):
        n = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if kind == b"iCCP":
            name_end = body.find(b"\x00")
            try:
                return zlib.decompress(body[name_end + 2:])
            except Exception:
                return None
        if kind in (b"IDAT", b"IEND"):
            return None
        pos += 12 + n
    return None


def _read_other(path):
    """JPEG / PNG / anything OpenCV reads; through a buffer so any file name
    works on Windows. A JPEG's EXIF orientation is applied by OpenCV."""
    with open(path, "rb") as fh:
        data = fh.read()
    buf = np.frombuffer(data, np.uint8)
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    flag = cv2.IMREAD_UNCHANGED if is_png else cv2.IMREAD_COLOR
    a = cv2.imdecode(buf, flag)
    if a is None:
        raise ValueError("not a picture this can read")
    if a.ndim == 3 and a.shape[2] == 4:
        a = cv2.cvtColor(a, cv2.COLOR_BGRA2RGB)
    elif a.ndim == 3 and a.shape[2] == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
    icc = png_icc(data) if is_png else jpeg_icc(data)
    return a, icc


def _matrix_shaper(icc: bytes):
    """(3x3 device-linear RGB -> XYZ D50, three 65536-entry TRC LUTs) of a plain
    matrix-shaper RGB profile, else None (a LUT profile goes to littleCMS)."""
    from core import color_management as cm
    try:
        tags = cm._read_tag_table(icc)
    except Exception:
        return None
    if any(t in tags for t in (b"A2B0", b"A2B1", b"A2B2")):
        return None
    need = (b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC")
    if not all(t in tags for t in need):
        return None
    try:
        m = np.column_stack([cm._parse_xyz(icc, tags[t][0]) for t in need[:3]])
        luts = [cm._parse_trc_to_lut(icc, tags[t][0]) for t in need[3:]]
    except Exception:
        return None
    return m, luts


def _grey_shaper(icc: bytes):
    from core import color_management as cm
    try:
        tags = cm._read_tag_table(icc)
        if b"kTRC" in tags and b"rXYZ" not in tags:
            return cm._parse_trc_to_lut(icc, tags[b"kTRC"][0])
    except Exception:
        return None
    return None


def profile_name(icc: Optional[bytes]) -> Optional[str]:
    if not icc:
        return None
    from core import color_management as cm
    try:
        name = cm.InputProfile._read_desc(icc, cm._read_tag_table(icc))
        return str(name).strip() or "embedded profile"
    except Exception:
        return "embedded profile"


def _is_srgb(name: Optional[str]) -> bool:
    return bool(name) and "srgb" in name.lower().replace(" ", "")


def _lut(lut: np.ndarray, x: np.ndarray) -> np.ndarray:
    """A 65536-entry TRC read at float x (interpolated, so 16-bit data keeps
    its precision through the reduction's in-between values)."""
    return np.interp(x, np.linspace(0.0, 1.0, lut.size), lut).astype(np.float32)


def _srgb_matrix_inv() -> np.ndarray:
    from core import color_management as cm
    return np.linalg.inv(np.column_stack([cm._SR, cm._SG, cm._SB])).astype(np.float32)


def _icc_space(icc: bytes) -> bytes:
    return icc[16:20] if icc and len(icc) >= 20 else b""


def _lcms(f: np.ndarray, icc: bytes, grey: bool) -> Optional[np.ndarray]:
    """Little-CMS (imagecodecs) in float32, unbounded, relative colorimetric:
    the file's profile -> sRGB. None when it isn't there or refuses."""
    try:
        import imagecodecs
        if not imagecodecs.CMS.available:
            return None
        out = imagecodecs.cms_transform(
            np.ascontiguousarray(f, np.float32), icc, imagecodecs.cms_profile("srgb"),
            colorspace="gray" if grey else "rgb", outcolorspace="rgb",
            outdtype=np.float32, intent=1)
        out = np.asarray(out, np.float32)
        return out if out.ndim == 3 and out.shape[2] == 3 and np.all(np.isfinite(out)) else None
    except Exception:
        return None


def to_srgb(f: np.ndarray, icc: Optional[bytes], grey: bool):
    """Float 0..1 picture in its file's colours -> (uint8 sRGB HxWx3,
    converted?). Converted is False only when a profile was there and could
    not be applied (then the values are shown as sRGB)."""
    from core import color_management as cm
    name = profile_name(icc)
    space = _icc_space(icc)
    if grey and icc and space != b"GRAY":
        icc, name = None, None                      # an RGB profile on a grey file
    if not grey and icc and space not in (b"RGB ",):
        icc, name = None, None
    u8 = lambda x: np.rint(np.clip(x, 0.0, 1.0) * 255.0).astype(np.uint8)
    if not icc or _is_srgb(name):
        return u8(np.repeat(f[:, :, None], 3, axis=2) if grey else f), True
    out = _lcms(f, icc, grey)
    if out is not None:
        return u8(out), True
    if grey:
        lut = _grey_shaper(icc)
        if lut is None:
            return u8(np.repeat(f[:, :, None], 3, axis=2)), False
        g = cm.srgb_encode(_lut(lut, f))
        return u8(np.repeat(g[:, :, None], 3, axis=2)), True
    ms = _matrix_shaper(icc)
    if ms is None:
        return u8(f), False
    m, luts = ms
    lin = np.stack([_lut(luts[c], f[:, :, c]) for c in range(3)], axis=-1)
    srgb_lin = (lin @ m.astype(np.float32).T) @ _srgb_matrix_inv().T
    return u8(cm.srgb_encode(np.clip(srgb_lin, 0.0, 1.0))), True


def load_reference(path: str, max_side: int = MAX_SIDE) -> Reference:
    """Read a reference picture as uint8 sRGB at most ``max_side`` on its long
    side. Raises OSError / ValueError when the file can't be read."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tif", ".tiff"):
        try:
            arr, icc = _read_tiff(path)
        except Exception:
            arr, icc = _read_other(path)
    else:
        arr, icc = _read_other(path)
    f, bits, grey = _as_float_rgb(arr)
    h, w = f.shape[:2]
    if h < 2 or w < 2:
        raise ValueError("the picture is empty")
    s = min(1.0, max_side / float(max(h, w)))
    if s < 1.0:
        f = cv2.resize(f, (max(1, round(w * s)), max(1, round(h * s))),
                       interpolation=cv2.INTER_AREA)
    rgb, converted = to_srgb(np.ascontiguousarray(f), icc, grey)
    return Reference(rgb=np.ascontiguousarray(rgb), full_size=(w, h),
                     profile=profile_name(icc), bits=bits, converted=converted)


# --------------------------------------------------------------------------- #
# Lining up
# --------------------------------------------------------------------------- #

_sift = None


def _features(g: np.ndarray):
    global _sift
    if _sift is None:
        _sift = cv2.SIFT_create(nfeatures=N_FEATURES)
    return _sift.detectAndCompute(g, None)


def _prepare(rgb: np.ndarray, invert: bool = False):
    """Luma at MATCH_SIDE, CLAHE'd; returns (grey u8, scale full -> small)."""
    a = rgb.astype(np.float32)
    g = a @ LUMA if a.ndim == 3 else a
    if invert:
        g = 255.0 - g
    h, w = g.shape
    s = MATCH_SIDE / float(max(h, w))
    if s < 1.0:
        g = cv2.resize(g, (max(1, round(w * s)), max(1, round(h * s))),
                       interpolation=cv2.INTER_AREA)
    else:
        s = 1.0
    g8 = np.clip(np.rint(g), 0, 255).astype(np.uint8)
    g8 = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g8)
    return g8, s


def _shape_ok(H: np.ndarray, w: float, h: float) -> bool:
    """The reference's corners under H: a convex quad near a turned, scaled
    rectangle (the two scales within MAX_SCALE_SKEW, corners square to
    MAX_ANGLE_DEV)."""
    c = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
    q = cv2.perspectiveTransform(c[None], H)[0]
    if not np.all(np.isfinite(q)):
        return False
    d = np.roll(q, -1, axis=0) - q
    cross = d[:, 0] * np.roll(d, -1, axis=0)[:, 1] - d[:, 1] * np.roll(d, -1, axis=0)[:, 0]
    if not (np.all(cross > 0) or np.all(cross < 0)):
        return False
    lens = np.linalg.norm(d, axis=1)
    if np.any(lens < 1e-6):
        return False
    sx = (lens[0] + lens[2]) / (2 * w)
    sy = (lens[1] + lens[3]) / (2 * h)
    if max(sx, sy) / min(sx, sy) > 1.0 + MAX_SCALE_SKEW:
        return False
    for i in range(4):
        a, b = -d[i - 1], d[i]
        cosang = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        if abs(np.degrees(np.arccos(np.clip(cosang, -1, 1))) - 90.0) > MAX_ANGLE_DEV:
            return False
    return True


def _match_one(fr, fs, size_r):
    (kr, dr), (ks, ds) = fr, fs
    if dr is None or ds is None or len(kr) < MIN_INLIERS or len(ks) < MIN_INLIERS:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(dr, ds, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
    if len(good) < MIN_INLIERS:
        return None
    # continuous coordinates: a pixel's centre at index + 0.5
    pr = np.float32([kr[m.queryIdx].pt for m in good]) + 0.5
    ps = np.float32([ks[m.trainIdx].pt for m in good]) + 0.5
    w, h = size_r
    best = None
    H, inl = cv2.findHomography(pr, ps, cv2.RANSAC, RANSAC_PX, maxIters=5000, confidence=0.999)
    if H is not None and inl is not None and _shape_ok(H, w, h):
        best = (H, inl.ravel().astype(bool), "homography")
    if best is None:
        A, inl = cv2.estimateAffinePartial2D(pr, ps, method=cv2.RANSAC,
                                             ransacReprojThreshold=RANSAC_PX,
                                             maxIters=5000, confidence=0.999)
        if A is None or inl is None:
            return None
        H = np.vstack([A, [0.0, 0.0, 1.0]])
        best = (H, inl.ravel().astype(bool), "similarity")
    H, inl, method = best
    n = int(inl.sum())
    if n < MIN_INLIERS or n < MIN_INLIER_SHARE * len(good):
        return None
    err = np.linalg.norm(cv2.perspectiveTransform(pr[inl][None].astype(np.float64), H)[0]
                         - ps[inl], axis=1)
    med = float(np.median(err))
    if med > MAX_ERROR_PX or not _shape_ok(H, w, h):
        return None
    return H, n, med, method


def line_up(ref_rgb: np.ndarray, shown_rgb: np.ndarray,
            invert_shown: bool = False) -> Optional[LineUp]:
    """Where the reference sits on the shown picture, or None. ``invert_shown``
    for a frame still shown as a negative."""
    gr, sr = _prepare(ref_rgb)
    gs, ss = _prepare(shown_rgb, invert=invert_shown)
    fs = _features(gs)
    hr, wr = gr.shape
    hs, ws = gs.shape
    found = None
    for mirrored in (False, True):
        g = np.ascontiguousarray(gr[:, ::-1]) if mirrored else gr
        res = _match_one(_features(g), fs, (wr, hr))
        if res is not None and (found is None or res[1] > found[1][1]):
            found = (mirrored, res)
    if found is None:
        return None
    mirrored, (H, n, err, method) = found
    # small continuous coords -> fractions on both sides; the mirror folded in
    to_frac_s = np.diag([1.0 / ws, 1.0 / hs, 1.0])
    from_frac_r = np.diag([float(wr), float(hr), 1.0])
    M = np.array([[-1.0, 0.0, 1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]) if mirrored else np.eye(3)
    Hn = to_frac_s @ H @ from_frac_r @ M
    Hn = Hn / Hn[2, 2]
    return LineUp(H=Hn, mirrored=mirrored, inliers=n, error_px=err, method=method)


def pixel_matrix(H_norm: np.ndarray, ref_size, shown_size) -> np.ndarray:
    """3x3 mapping reference PIXEL coordinates (continuous, the picture spans
    [0, w]) to shown pixel coordinates, for pictures of these (w, h)."""
    rw, rh = ref_size
    sw, sh = shown_size
    return np.diag([float(sw), float(sh), 1.0]) @ H_norm @ np.diag([1.0 / rw, 1.0 / rh, 1.0])


def qt_args(M: np.ndarray) -> tuple:
    """QTransform's nine arguments for a column-vector 3x3 ``M`` (Qt's matrix
    is its transpose: x' = m11 x + m21 y + m31, ...)."""
    M = np.asarray(M, np.float64)
    return (M[0, 0], M[1, 0], M[2, 0], M[0, 1], M[1, 1], M[2, 1], M[0, 2], M[1, 2], M[2, 2])


def fitted_rect(ref_size, box) -> Tuple[float, float, float, float]:
    """(x, y, w, h) of the reference fitted inside ``box`` = (x, y, w, h),
    aspect kept, centred."""
    rw, rh = ref_size
    bx, by, bw, bh = box
    s = min(bw / float(rw), bh / float(rh))
    w, h = rw * s, rh * s
    return bx + (bw - w) / 2.0, by + (bh - h) / 2.0, w, h
