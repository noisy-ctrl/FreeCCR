"""JPEG encoder settings shared by every JPEG the app writes for export (the
plain JPEG, the HDR gain-map JPEG's picture, and the size estimate).

At quality FULL_CHROMA_MIN_QUALITY and above, colour is stored at full
resolution (4:4:4) instead of libjpeg's default half resolution (4:2:0). At
that quality the user is asking for fidelity, and 4:2:0 visibly softens small
saturated details (coloured edges bleed into their neighbours) for a saving of
only some 10-20 % in file size. Below it the default stays, since those
exports are about size. See spec/jpeg-chroma-444.md.
"""
import cv2

FULL_CHROMA_MIN_QUALITY = 90


def jpeg_params(quality) -> list:
    """cv2.imencode parameters for an export JPEG at ``quality``."""
    q = int(quality)
    params = [cv2.IMWRITE_JPEG_QUALITY, q]
    if (q >= FULL_CHROMA_MIN_QUALITY
            and hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR")
            and hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444")):
        params += [cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                   cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
    return params
