#!/usr/bin/env python3
"""RAF EXIF lives in the embedded preview JPEG: read it from there instead of
handing exifread the RAF container ("File format not recognized")."""

import io
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

PIL = pytest.importorskip("PIL")


def _jpeg_with_exif(make="FUJIFILM", model="GFX50S II"):
    from PIL import Image
    im = Image.new("RGB", (16, 16), (128, 128, 128))
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0110] = model
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _fake_raf(path, jpeg):
    head = bytearray(b"FUJIFILMCCD-RAW " + b"\0" * 84)
    off = 160
    head[84:92] = struct.pack(">II", off, len(jpeg))
    with open(path, "wb") as f:
        f.write(bytes(head) + b"\0" * (off - len(head)) + jpeg + b"\0" * 64)


def test_raf_reads_camera_from_embedded_jpeg(tmp_path, caplog):
    from core.ccr_image import CCRImage
    p = str(tmp_path / "x.RAF")
    _fake_raf(p, _jpeg_with_exif())
    with caplog.at_level("WARNING"):
        info = CCRImage.get_camera_and_lens_for_lensfun(p)
    assert info["camera_make"] == "FUJIFILM" and info["camera_model"] == "GFX50S II"
    assert not [r for r in caplog.records if "not recognized" in r.getMessage()]


def test_other_files_are_read_as_before(tmp_path):
    from core.ccr_image import CCRImage
    p = str(tmp_path / "x.jpg")
    with open(p, "wb") as f:
        f.write(_jpeg_with_exif(make="ACME", model="One"))
    info = CCRImage.get_camera_and_lens_for_lensfun(p)
    assert info["camera_make"] == "ACME" and info["camera_model"] == "One"


class _Ratio:
    def __init__(self, num, den):
        self.num, self.den = num, den


class _Tag:
    def __init__(self, v):
        self.values = [v]

    def __str__(self):
        v = self.values[0]
        return f"{v.num}/{v.den}" if isinstance(v, _Ratio) else str(v)


def test_unknown_lens_numbers_are_none_not_errors():
    from core.ccr_image import _exif_number
    assert _exif_number(_Tag(_Ratio(0, 0))) is None      # manual lens aperture
    assert _exif_number(_Tag(_Ratio(0, 1))) is None      # focal length 0
    assert _exif_number(_Tag(_Ratio(56, 10))) == pytest.approx(5.6)
    assert _exif_number(_Tag(80)) == 80.0
    assert _exif_number(None) is None
