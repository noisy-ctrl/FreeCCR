"""
Output-size estimation for the export dialog: cheap original-dimension probing
and JPEG/TIFF size approximation from the in-memory converted preview.

No Qt imports here; the dialog drives this module.
"""
import logging
import os

import cv2
import numpy as np

RAW_EXTS = {".cr3", ".cr2", ".nef", ".arw", ".dng", ".rw2", ".orf", ".raf", ".srw", ".pef", ".3fr"}

# Typical deflate compression ratio observed on 16-bit photographic TIFFs
TIFF_DEFLATE_FACTOR = 0.6


def get_original_dims(ccr_img):
    """
    Return (height, width) of the image's full-resolution source.

    Uses the cached CCRImage.original_full_size (captured during read_image);
    falls back to a cheap header probe and caches the result back onto the
    instance. Returns None when nothing works.
    """
    dims = getattr(ccr_img, "original_full_size", None)
    if dims:
        return dims

    # Merged (trichrome) images have no single backing file — file_path is one
    # source RAW whose dimensions don't match the merged image (a Bayer merge is
    # half-sensor; a monochrome merge is full-sensor but still derived from 3
    # files). Never probe it; fall back to the in-memory preview size.
    # See spec/three-way-rgb-merge.md.
    if getattr(ccr_img, "is_merged", False):
        if ccr_img.resized_raw is not None:
            dims = (ccr_img.resized_raw.shape[0], ccr_img.resized_raw.shape[1])
            ccr_img.original_full_size = dims
        return dims

    file_path = ccr_img.file_path
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".fff":
        ext = ".tiff"
    try:
        if ext in RAW_EXTS:
            import rawpy
            with rawpy.imread(file_path) as raw:
                dims = (raw.sizes.height, raw.sizes.width)
        elif ext in (".tif", ".tiff"):
            import tifffile
            with tifffile.TiffFile(file_path) as tif:
                shape = tif.pages[0].shape
                dims = (shape[0], shape[1])
        else:
            img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
            if img is not None:
                dims = (img.shape[0], img.shape[1])
    except Exception as e:
        logging.warning(f"Could not probe dimensions of {file_path}: {e}")
        dims = None

    if dims is None and ccr_img.resized_raw is not None:
        # Last resort: the preview's dims (underestimates large originals)
        dims = (ccr_img.resized_raw.shape[0], ccr_img.resized_raw.shape[1])

    if dims:
        ccr_img.original_full_size = dims
    return dims


def effective_pixels(h: int, w: int, max_long_side=None) -> int:
    """Pixel count after an optional long-edge cap, preserving aspect ratio."""
    if max_long_side and max(h, w) > max_long_side:
        scale = max_long_side / max(h, w)
        h, w = int(h * scale), int(w * scale)
    return h * w


def jpeg_bytes_per_pixel(sample_rgb16: np.ndarray, quality: int):
    """
    Encode a 16-bit RGB sample (the converted preview) as JPEG at the given
    quality and return the resulting bytes-per-pixel, or None on failure.
    """
    try:
        img8 = (np.clip(sample_rgb16, 0, 65535) / 257).astype(np.uint8)
        img8 = cv2.cvtColor(img8, cv2.COLOR_RGB2BGR)
        from core.jpeg_params import jpeg_params   # match what export writes
        ok, buffer = cv2.imencode(".jpg", img8, jpeg_params(quality))
        if not ok:
            return None
        pixels = img8.shape[0] * img8.shape[1]
        return len(buffer) / pixels if pixels else None
    except Exception as e:
        logging.warning(f"JPEG size sampling failed: {e}")
        return None


def tiff_bytes(h: int, w: int) -> int:
    """Approximate size of a 16-bit RGB deflate-compressed TIFF."""
    return int(h * w * 3 * 2 * TIFF_DEFLATE_FACTOR)


def estimate_total_bytes(dims_list, fmt: str, *, bpp=None, max_long_side=None) -> int:
    """
    Estimate the total output size for a batch.

    Args:
        dims_list: iterable of (height, width) per image (None entries skipped)
        fmt: "jpeg" or "tiff"
        bpp: bytes-per-pixel sample from jpeg_bytes_per_pixel (jpeg only)
        max_long_side: optional resize cap applied on export
    """
    total = 0
    for dims in dims_list:
        if not dims:
            continue
        h, w = dims
        if fmt == "jpeg":
            if bpp is None:
                continue
            total += int(effective_pixels(h, w, max_long_side) * bpp)
        else:
            if max_long_side and max(h, w) > max_long_side:
                scale = max_long_side / max(h, w)
                h, w = int(h * scale), int(w * scale)
            total += tiff_bytes(h, w)
    return total


def format_size(num_bytes: float) -> str:
    """Human-readable size with a leading ~ (estimates are approximate)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"~{int(size)} {unit}"
            return f"~{size:.1f} {unit}"
        size /= 1024
    return f"~{size:.1f} GB"
