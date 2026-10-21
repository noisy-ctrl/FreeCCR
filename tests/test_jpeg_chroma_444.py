#!/usr/bin/env python3
"""Full-resolution colour (4:4:4) in JPEG exports at quality 90+.
See spec/jpeg-chroma-444.md."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import ccr_processor as cp  # noqa: E402
from core import hdr_gainmap as hg  # noqa: E402
from core.jpeg_params import FULL_CHROMA_MIN_QUALITY, jpeg_params  # noqa: E402

pytestmark = pytest.mark.skipif(
    not hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444"),
    reason="OpenCV without JPEG sampling-factor control")


def _sampling(jpeg: bytes):
    """(h, v) sampling factors per component from the first SOF0 marker."""
    i = jpeg.find(b"\xff\xc0")
    n = jpeg[i + 9]
    comps = jpeg[i + 10:i + 10 + 3 * n]
    return [(comps[k + 1] >> 4, comps[k + 1] & 15) for k in range(0, 3 * n, 3)]


class _Img:
    def __init__(self, mono=False):
        self.color_profile = "bw" if mono else "color"
        self.decoded_mono = False

    def resize_image_to_max_pixel(self, img, n):
        return img


def _rgb16():
    rng = np.random.default_rng(1)
    return (rng.random((64, 96, 3)) * 65535).astype(np.uint16)


def test_params():
    assert FULL_CHROMA_MIN_QUALITY == 90
    assert jpeg_params(89) == [cv2.IMWRITE_JPEG_QUALITY, 89]
    for q in (90, 98):
        assert jpeg_params(q) == [cv2.IMWRITE_JPEG_QUALITY, q,
                                  cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                                  cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]


def test_plain_export_is_444_at_90_and_420_below(tmp_path):
    for q, want in ((92, [(1, 1)] * 3), (80, [(2, 2), (1, 1), (1, 1)])):
        path = str(tmp_path / f"q{q}.jpg")
        cp.write_export_image(_Img(), _rgb16(), path, True, q, None)
        assert _sampling(open(path, "rb").read()) == want, q


def test_hdr_picture_is_444_and_still_the_plain_jpeg(tmp_path):
    rgb = _rgb16()
    plain = str(tmp_path / "plain.jpg")
    cp.write_export_image(_Img(), rgb, plain, True, 92, None)
    hdr = str(tmp_path / "hdr.jpg")
    cp.write_export_image(_Img(), rgb, hdr, True, 92, None, hdr_headroom=2.0)
    data = open(hdr, "rb").read()
    assert _sampling(data) == [(1, 1)] * 3
    a = cv2.imread(plain)
    b = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(a, b)


def test_monochrome_jpeg_still_one_component(tmp_path):
    path = str(tmp_path / "m.jpg")
    grey = np.repeat(_rgb16()[..., :1], 3, axis=2)
    cp.write_export_image(_Img(mono=True), grey, path, True, 95, None)
    assert len(_sampling(open(path, "rb").read())) == 1


def test_estimate_uses_the_same_settings():
    from core import export_estimator
    rgb = _rgb16()
    bpp = export_estimator.jpeg_bytes_per_pixel(rgb, 95)
    img8 = cv2.cvtColor((rgb / 257).astype(np.uint8), cv2.COLOR_RGB2BGR)
    ok, b = cv2.imencode(".jpg", img8, jpeg_params(95))
    assert abs(bpp - len(b) / (64 * 96)) < 1e-9
