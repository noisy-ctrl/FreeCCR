"""Auto crop cuts each frame to the format's proportions
(spec/auto-crop-ratio.md): Capture's format first, else the Crop panel's
locked ratio, else as found."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.auto_crop import FrameCrop, fit_ratio  # noqa: E402
from core import crop_aspect  # noqa: E402

W, H = 1080, 720


def _fc(x1, y1, x2, y2, angle=0.0):
    return FrameCrop((x1 / W, y1 / H, x2 / W, y2 / H), angle=angle,
                     confidence="high", reason="found")


def _shape(fc):
    x1, y1, x2, y2 = fc.rect
    return (x2 - x1) * W, (y2 - y1) * H


@pytest.mark.parametrize("box", [(40, 50, 1030, 700), (40, 60, 1040, 690),
                                 (45, 40, 1035, 700), (30, 45, 1050, 705)])
def test_frames_come_out_exactly_3_2(box):
    out = fit_ratio(_fc(*box), 1.5, W, H, name="3:2")
    bw, bh = _shape(out)
    assert abs(bw / bh - 1.5) < 1e-9
    ow, oh = _shape(_fc(*box))
    assert bw <= ow + 1e-9 and bh <= oh + 1e-9          # only ever trims
    # centred where it was found, at the same angle
    assert abs(sum(out.rect[::2]) - sum(_fc(*box).rect[::2])) < 1e-9
    if (ow, oh) != (bw, bh):
        assert "cut to 3:2" in out.reason
    else:
        assert out is not None and out.reason == "found"   # already 3:2


def test_portrait_frame_keeps_its_orientation():
    out = fit_ratio(_fc(300, 20, 760, 700), 1.5, W, H)
    bw, bh = _shape(out)
    assert bh > bw and abs(bh / bw - 1.5) < 1e-9


def test_angle_kept_and_wrong_format_left_alone():
    out = fit_ratio(_fc(40, 50, 1030, 700, angle=0.7), 1.5, W, H)
    assert out.angle == 0.7
    sq = _fc(200, 40, 880, 700)                         # a 6x6 frame
    left = fit_ratio(sq, 1.5, W, H, name="3:2")
    assert left.rect == sq.rect and "left as found" in left.reason


def test_unusable_or_no_ratio_unchanged():
    none = FrameCrop(None)
    assert fit_ratio(none, 1.5, W, H) is none
    fc = _fc(40, 50, 1030, 700)
    assert fit_ratio(fc, None, W, H) is fc


def test_saved_ratio():
    assert crop_aspect.saved_ratio("free") is None
    assert crop_aspect.saved_ratio("original") is None
    assert crop_aspect.saved_ratio("#film") is None
    assert crop_aspect.saved_ratio("35mm") == pytest.approx(1.5)
    assert crop_aspect.saved_ratio("6x7") == pytest.approx(69 / 56)
    assert crop_aspect.saved_ratio("custom", 4, 5) == pytest.approx(1.25)
    assert crop_aspect.saved_ratio("custom", 4, 0) is None


def _be(crop_from_guide=True):
    from core.ccr_backend import CCRBackend
    be = CCRBackend.__new__(CCRBackend)
    be.crop_from_guide = crop_from_guide
    return be


def _panel(key):
    from PySide6.QtCore import QSettings
    st = QSettings("FreeCCR", "FreeCCR")
    old = st.value("crop/aspect_key", None)
    st.setValue("crop/aspect_key", key)
    return st, old


def _restore(st, old):
    if old is None:
        st.remove("crop/aspect_key")
    else:
        st.setValue("crop/aspect_key", old)


def test_capture_format_comes_first():
    st, old = _panel("1:1")
    try:
        img = SimpleNamespace(capture_guide={"format": "6x7", "cx": 0.5})
        r, name = _be().crop_ratio_for(img)
        assert r == pytest.approx(69 / 56) and "Capture" in name
        # "Crop to Capture's frame guide" off: Capture is ignored, the
        # Crop panel's ratio applies
        r, name = _be(False).crop_ratio_for(img)
        assert r == pytest.approx(1.0) and "Crop panel" in name
    finally:
        _restore(st, old)


def test_crop_panel_ratio_used_without_capture():
    st, old = _panel("35mm")
    try:
        r, name = _be().crop_ratio_for(SimpleNamespace())
        assert r == pytest.approx(1.5) and "Crop panel" in name
        st.setValue("crop/aspect_key", "free")
        assert _be().crop_ratio_for(SimpleNamespace()) == (None, "")
    finally:
        _restore(st, old)
