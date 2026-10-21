"""HDR gain-map JPEGs: the normal (SDR) picture plus a map that HDR screens
use to brighten the highlights that sat above white.

The file is an "Ultra HDR" JPEG carrying BOTH metadata flavours, so every
current reader finds what it looks for:

- ISO 21496-1 (what Apple's macOS 15+/iOS 18+, Chrome and libultrahdr 1.4
  read first): a version block in the primary image and the binary gain-map
  metadata in the gain-map image, each in an APP2
  ``urn:iso:std:iso:ts:21496:-1`` segment;
- Adobe's ``hdrgm`` XMP (Android 14, older Chrome, Lightroom/ACR): the
  version and a GContainer directory in the primary, the gain-map values in
  the gain map's own XMP.

Both point at the gain map through an MPF (CIPA DC-007) index in the
primary, and the gain map is simply appended after the primary's EOI. The
layout mirrors what libultrahdr writes; tests decode our files with
libultrahdr (via imagecodecs) and check the HDR it rebuilds.

The base rendition is the SDR picture, exactly the pixels a plain JPEG
export would hold. The map is single-channel (the same gain on R, G and B,
so highlights get brighter without changing colour), full resolution, with
zero offsets, gamma 1, min 0 stops and max = the headroom, so a stored value
v in [0, 255] means a gain of 2^(headroom * v / 255) on a display with at
least that much headroom. See spec/hdr-gain-map-export.md.
"""
from __future__ import annotations

import struct
from fractions import Fraction

import cv2
import numpy as np

# The HDR headroom the user picks in the export dialog (stops above SDR white
# the brightest highlight may reach).
HEADROOM_DEFAULT = 2.0
HEADROOM_MIN = 0.5
HEADROOM_MAX = 3.0
HEADROOM_STEP = 0.5

GAINMAP_JPEG_QUALITY = 90

_XMP_NS = b"http://ns.adobe.com/xap/1.0/\x00"
_ISO_NS = b"urn:iso:std:iso:ts:21496:-1\x00"
_MPF_NS = b"MPF\x00"


def clamp_headroom(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return HEADROOM_DEFAULT
    if not np.isfinite(v):
        return HEADROOM_DEFAULT
    return float(min(HEADROOM_MAX, max(HEADROOM_MIN, v)))


def glow_stops(over, headroom) -> np.ndarray:
    """How many stops each pixel is brightened on an HDR screen.

    ``over`` is the pixel's brightest channel BEFORE highlight recovery, in
    display units (white = 1.0), as captured by the working-space stage. At or
    below white nothing changes. Above it the glow is ``log2(over)`` stops,
    rolled smoothly into the headroom ceiling: ``H * tanh(s / H)``, which has
    slope 1 at white (so it starts as a straight continuation of the picture)
    and never quite reaches H."""
    h = clamp_headroom(headroom)
    s = np.log2(np.maximum(np.asarray(over, dtype=np.float32), np.float32(1.0)))
    return (np.float32(h) * np.tanh(s / np.float32(h))).astype(np.float32)


def gainmap_levels(glow, headroom) -> np.ndarray:
    """The 8-bit gain-map samples for a glow in stops: v = 255 * glow / H."""
    h = clamp_headroom(headroom)
    v = np.rint(np.asarray(glow, dtype=np.float32) * np.float32(255.0 / h))
    return np.clip(v, 0, 255).astype(np.uint8)


# --- metadata ----------------------------------------------------------------

def _segment(marker: int, payload: bytes) -> bytes:
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("JPEG marker segment too large")
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _rational(value: float, denominator: int = 1 << 20):
    f = Fraction(value).limit_denominator(denominator)
    return f.numerator, f.denominator


def iso_gainmap_metadata(headroom: float) -> bytes:
    """ISO 21496-1 binary gain-map metadata for a single-channel map: base
    headroom 0, alternate headroom H, gain min 0 / max H stops, gamma 1, zero
    offsets, gain applied in the base image's colour space. Big-endian, with
    a numerator/denominator pair for every value (no common denominator)."""
    h = clamp_headroom(headroom)
    hn, hd = _rational(h)
    out = struct.pack(">HH", 0, 0)            # minimum_version, writer_version
    out += bytes([0x40])                      # flags: use_base_colour_space
    out += struct.pack(">II", 0, 1)           # base_hdr_headroom = 0
    out += struct.pack(">II", hn, hd)         # alternate_hdr_headroom = H
    out += struct.pack(">iI", 0, 1)           # gain_map_min = 0
    out += struct.pack(">iI", hn, hd)         # gain_map_max = H
    out += struct.pack(">II", 1, 1)           # gain_map_gamma = 1
    out += struct.pack(">iI", 0, 1)           # base_offset = 0
    out += struct.pack(">iI", 0, 1)           # alternate_offset = 0
    return out


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def _gainmap_xmp(headroom: float) -> bytes:
    h = _fmt(clamp_headroom(headroom))
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="FreeCCR">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
        'hdrgm:Version="1.0" hdrgm:GainMapMin="0" '
        f'hdrgm:GainMapMax="{h}" hdrgm:Gamma="1" '
        'hdrgm:OffsetSDR="0" hdrgm:OffsetHDR="0" '
        f'hdrgm:HDRCapacityMin="0" hdrgm:HDRCapacityMax="{h}" '
        'hdrgm:BaseRenditionIsHDR="False"/>'
        '</rdf:RDF></x:xmpmeta>')
    return xmp.encode("utf-8")


def _primary_xmp(gainmap_length: int) -> bytes:
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="FreeCCR">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
        'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
        'xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
        'hdrgm:Version="1.0">'
        '<Container:Directory><rdf:Seq>'
        '<rdf:li rdf:parseType="Resource">'
        '<Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/>'
        '</rdf:li>'
        '<rdf:li rdf:parseType="Resource">'
        '<Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg" '
        f'Item:Length="{int(gainmap_length)}"/>'
        '</rdf:li>'
        '</rdf:Seq></Container:Directory>'
        '</rdf:Description></rdf:RDF></x:xmpmeta>')
    return xmp.encode("utf-8")


_MPF_PAYLOAD_LEN = 4 + 8 + 2 + 3 * 12 + 4 + 2 * 16   # 86 bytes


def _mpf_payload(primary_length: int, gainmap_length: int,
                 gainmap_offset: int) -> bytes:
    """CIPA DC-007 Multi-Picture index: two images, the primary (offset 0)
    and the gain map at ``gainmap_offset`` bytes from the start of this
    segment's TIFF header."""
    entries_offset = 8 + 2 + 3 * 12 + 4
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8)
    tiff += struct.pack(">H", 3)
    tiff += struct.pack(">HHI", 0xB000, 7, 4) + b"0100"              # MPFVersion
    tiff += struct.pack(">HHII", 0xB001, 4, 1, 2)                     # NumberOfImages
    tiff += struct.pack(">HHII", 0xB002, 7, 32, entries_offset)       # MPEntry
    tiff += struct.pack(">I", 0)                                      # no next IFD
    tiff += struct.pack(">IIIHH", 0x00030000, primary_length, 0, 0, 0)
    tiff += struct.pack(">IIIHH", 0x00000000, gainmap_length, gainmap_offset, 0, 0)
    payload = _MPF_NS + tiff
    assert len(payload) == _MPF_PAYLOAD_LEN
    return payload


def _split_soi(jpeg: bytes):
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG stream")
    return jpeg[:2], jpeg[2:]


def _after_app0(body: bytes) -> int:
    """Index just past a leading APP0 (JFIF) segment in a post-SOI body, so
    our segments follow it as JFIF asks; 0 when there is none."""
    if body[:2] == b"\xff\xe0":
        return 2 + struct.unpack(">H", body[2:4])[0]
    return 0


def encode_gainmap_jpeg(sdr_rgb8: np.ndarray, glow: np.ndarray, headroom: float,
                        quality: int = 92, icc: bytes = b"",
                        gainmap_quality: int = GAINMAP_JPEG_QUALITY,
                        metadata=("iso", "xmp")) -> bytes:
    """Encode an SDR picture (HxWx3 uint8, RGB) and its glow (HxW stops) as
    one gain-map JPEG. ``icc`` (the export colour space's profile) is
    embedded in the primary. Returns the file bytes.

    ``metadata`` exists for the tests, which check that each flavour decodes
    on its own; the app always writes both."""
    sdr = np.ascontiguousarray(sdr_rgb8)
    if sdr.ndim != 3 or sdr.shape[2] != 3 or sdr.dtype != np.uint8:
        raise ValueError("sdr_rgb8 must be an HxWx3 uint8 RGB array")
    glow = np.asarray(glow, dtype=np.float32)
    if glow.shape != sdr.shape[:2]:
        raise ValueError("glow must match the picture's height and width")
    h = clamp_headroom(headroom)

    ok, prim = cv2.imencode(".jpg", cv2.cvtColor(sdr, cv2.COLOR_RGB2BGR),
                            [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise IOError("failed to encode the SDR picture")
    ok, gm = cv2.imencode(".jpg", gainmap_levels(glow, h),
                          [cv2.IMWRITE_JPEG_QUALITY, int(gainmap_quality)])
    if not ok:
        raise IOError("failed to encode the gain map")

    # Gain-map image: SOI, [APP0], XMP (hdrgm values), ISO metadata, rest.
    soi, body = _split_soi(gm.tobytes())
    cut = _after_app0(body)
    want_xmp, want_iso = "xmp" in metadata, "iso" in metadata
    if not (want_xmp or want_iso):
        raise ValueError("at least one metadata flavour is required")
    gainmap = (soi + body[:cut]
               + (_segment(0xE1, _XMP_NS + _gainmap_xmp(h)) if want_xmp else b"")
               + (_segment(0xE2, _ISO_NS + iso_gainmap_metadata(h)) if want_iso else b"")
               + body[cut:])

    # Primary: SOI, [APP0], XMP (container), ISO version, MPF, ICC, rest.
    soi, body = _split_soi(prim.tobytes())
    cut = _after_app0(body)
    head = (soi + body[:cut]
            + (_segment(0xE1, _XMP_NS + _primary_xmp(len(gainmap))) if want_xmp else b"")
            + (_segment(0xE2, _ISO_NS + struct.pack(">HH", 0, 0)) if want_iso else b""))
    mpf_tiff_pos = len(head) + 4 + len(_MPF_NS)      # marker+length, then "MPF\0"
    tail = b""
    if icc:
        from core.color_management import inject_jpeg_icc
        tail = inject_jpeg_icc(b"\xff\xd8", icc)[2:]   # just the APP2 segments
    tail += body[cut:]
    primary_length = len(head) + 4 + _MPF_PAYLOAD_LEN + len(tail)
    mpf = _segment(0xE2, _mpf_payload(primary_length, len(gainmap),
                                      primary_length - mpf_tiff_pos))
    primary = head + mpf + tail
    assert len(primary) == primary_length
    return primary + gainmap
