#!/usr/bin/env python3
"""A flat is of one format: never borrowed by another, and a frame whose
format isn't known gets the flat whose carrier opening has its shape
(spec/flat-format-match.md)."""
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core import flat_field as ff  # noqa: E402

H, W = 600, 900
# a carrier stood up and one lying down (shares of the frame; the opening
# finder needs the mask's bands under ~25 % of the view)
UPRIGHT = {"left": 0.22, "right": 0.78, "top": 0.06, "bottom": 0.94}
LYING = {"left": 0.08, "right": 0.92, "top": 0.12, "bottom": 0.88}


def _shot(opening, fall=0.35, level=26000, s=1.0, dx=0.0):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    fx = 0.5 + ((xx + 0.5) / W - 0.5 - dx) / s
    fy = 0.5 + ((yy + 0.5) / H - 0.5) / s
    light = 1.0 + 0.5 * np.exp(-((fx - 0.55) ** 2 + (fy - 0.47) ** 2) / (2 * 0.15 ** 2))
    light *= 1.0 - fall * ((fx - 0.5) ** 2 + (fy - 0.5) ** 2) / 0.5
    inside = ((fx > opening["left"]) & (fx < opening["right"])
              & (fy > opening["top"]) & (fy < opening["bottom"]))
    img = np.where(inside, light * level, 300.0).astype(np.float32)
    img = cv2.GaussianBlur(img, (0, 0), 1.0)
    return np.repeat(np.clip(img, 0, 65535)[..., None], 3, 2).astype(np.uint16)


def _flat(name, opening, fmt, light="white", **kw):
    return ff.build_tagged_profile(_shot(opening, **kw), name, light=light, fmt=fmt)


@pytest.fixture(autouse=True)
def _clean():
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})
    yield
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})


def _frame(fmt=None, opening=None):
    return types.SimpleNamespace(film_format=fmt, merge_sources=None, is_merged=False,
                                 flat_opening=opening, capture_guide=None)


def test_an_opening_fits_only_its_own_shape():
    p645 = _flat("645", UPRIGHT, "645")
    assert ff.opening_fits(p645, ff.opening_of(_shot(UPRIGHT, s=1.05, dx=0.02))) is True
    assert ff.opening_fits(p645, ff.opening_of(_shot(LYING))) is False
    assert ff.opening_fits(p645, None) is None
    assert ff.opening_fits(p645, {"left": 0.1}) is None          # one side: can't tell


def test_a_flat_is_never_borrowed_by_another_format():
    p645, p67 = _flat("645", UPRIGHT, "645"), _flat("6x7", LYING, "6x7")
    free_upright = _flat("free", UPRIGHT, None)
    ff.set_library([p645])
    assert ff.profile_for(_frame("6x7")) is None                 # nothing of its own: none
    ff.set_library([p645, p67])
    assert ff.profile_for(_frame("6x7")) is p67 and ff.profile_for(_frame("645")) is p645
    # a format-less flat stands in, but only when its opening has the shape
    ff.set_library([p645, free_upright])
    lying = ff.opening_of(_shot(LYING))
    assert ff.profile_for(_frame("6x7", lying)) is None
    assert ff.profile_for(_frame("6x7", None)) is free_upright   # can't tell: it's used
    assert ff.profile_for(_frame("6x6", ff.opening_of(_shot(UPRIGHT)))) is free_upright


def test_an_unknown_format_gets_the_flat_of_its_openings_shape():
    p645, p67 = _flat("645", UPRIGHT, "645"), _flat("6x7", LYING, "6x7")
    ff.set_library([p645, p67])
    assert ff.profile_for(_frame(None, ff.opening_of(_shot(LYING, dx=0.03)))) is p67
    assert ff.profile_for(_frame(None, ff.opening_of(_shot(UPRIGHT, s=0.95)))) is p645
    other = {"left": 0.2, "right": 0.8, "top": 0.2, "bottom": 0.8}   # a third shape
    assert ff.profile_for(_frame(None, ff.opening_of(_shot(other)))) is None
    assert ff.profile_for(_frame(None, None)) is None            # two formats, no opening
    ff.set_library([p645])
    assert ff.profile_for(_frame(None, None)) is p645            # one format: used
    assert ff.profile_for(_frame(None, ff.opening_of(_shot(LYING)))) is None


def test_the_decode_reads_the_opening_first(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv[:1])
    from core.ccr_image import CCRImage
    p645, p67 = _flat("645", UPRIGHT, "645"), _flat("6x7", LYING, "6x7")
    ff.set_library([p645, p67])
    arr = _shot(LYING, dx=0.02)
    img = CCRImage.__new__(CCRImage)
    img.file_path = str(tmp_path / "a.raf")
    img.merge_sources = None
    img.film_format = None
    out = img._apply_field_correction(arr)
    yy, xx = np.mgrid[0:H, 0:W]
    inside = ((xx / W > 0.15) & (xx / W < 0.85) & (yy / H > 0.18) & (yy / H < 0.82))
    v = out[..., 1].astype(np.float32)[inside]
    q1, q3 = np.percentile(v, [25, 75])
    assert (q3 - q1) / np.median(v) < 0.03                       # the 6x7 flat, tracked
    assert ff.profile_for(img) is p67                            # its signature agrees


def test_a_light_too_small_for_the_opening_is_warned():
    small = _flat("xpan", LYING, "xpan", fall=1.6)               # corners far darker
    assert ff.coverage(small) < ff.COVER_MIN
    assert any("is it big enough for this format" in w for w in small.meta["warnings"])
    even = _flat("ok", LYING, "6x7")
    assert ff.coverage(even) > 0.5
    assert not any("big enough" in w for w in even.meta.get("warnings") or [])


def test_a_preset_pin_of_another_shape_is_left_for_the_automatic_choice():
    p645, p67 = _flat("645", UPRIGHT, "645"), _flat("6x7", LYING, "6x7")
    ff.set_library([p645, p67])
    p645.path = "/lib/645.ffc"
    ff.set_preset_flats({"P": {"colour": "645.ffc"}})
    f = _frame(None, ff.opening_of(_shot(LYING)))
    f.film_preset = "P"
    assert ff.pinned(f)[0] == "format" and ff.profile_for(f) is p67
    f.flat_opening = ff.opening_of(_shot(UPRIGHT))
    assert ff.profile_for(f) is p645
