#!/usr/bin/env python3
"""A dust click fits itself to the speck under it (spec/dust-fit-spot.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import cv2  # noqa: E402
from core import dust_fit as df  # noqa: E402


def _grain(h=400, w=400, seed=1):
    rng = np.random.default_rng(seed)
    g = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.2)
    return 56.0 + 5.0 * g / g.std()


def _speck(g, cx, cy, core=3.0, halo=8.0, sign=1.0):
    yy, xx = np.mgrid[:g.shape[0], :g.shape[1]]
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    return g + sign * (100 * np.exp(-d2 / (2 * core ** 2)) + 30 * np.exp(-d2 / (2 * halo ** 2)))


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_a_dab_grows_over_the_specks_halo(sign):
    g = _speck(_grain(), 200.3, 199.6, sign=sign)
    r = df.fit_radius(g, 200.3, 199.6, 6.0)
    assert 13 <= r <= 18                    # the halo fades out by ~2 sigma


def test_clean_grain_and_limits():
    g = _grain()
    assert df.fit_radius(g, 200, 200, 9.0) == 9.0           # nothing distinct
    big = _speck(_grain(), 200, 200, core=10, halo=25)
    assert df.fit_radius(big, 200, 200, 4.0) == pytest.approx(12.0)  # 3x cap
    small = _speck(_grain(), 200, 200, core=1.5, halo=2.5)
    assert df.fit_radius(small, 200, 200, 20.0) == 20.0      # never shrinks
    assert df.fit_radius(g, 2, 2, 6.0) >= 6.0                # at the edge
    assert df.fit_radius(None, 1, 1, 5.0) == 5.0


def test_the_fitted_dab_heals_the_speck_cleanly():
    """The measured case: a 6 px dab over the speck left ~16 levels; fitted,
    the heal leaves only grain."""
    from core.ccr_processor import apply_dust_removal
    H = W = 600
    g = _speck(_grain(H, W, seed=4), 300.4, 299.7)
    img = (np.clip(np.repeat(g[..., None], 3, 2) / 255, 0, 1) * 65535).astype(np.uint16)

    def left(r):
        out = apply_dust_removal(img, [{"kind": "brush", "pts": [[300.4 / W, 299.7 / H]],
                                        "r": r / W}], feather=0.25)
        gg = out[..., 1].astype(np.float32) / 257
        yy, xx = np.ogrid[:H, :W]
        d = np.hypot(xx - 300.4, yy - 299.7)
        return gg[d <= 8].mean() - gg[(d > 40) & (d < 80)].mean()
    fitted = df.fit_radius(g, 300.4, 299.7, 6.0)
    assert left(6.0) > 6.0
    assert abs(left(fitted)) < 3.0


# --------------------------------------------------------------------------- #
# the brush
# --------------------------------------------------------------------------- #
def test_a_released_click_takes_the_fitted_radius_and_a_stroke_does_not():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage, QPixmap
    QApplication.instance() or QApplication([])
    from test_dust_crop_display import _converted_preview
    import tempfile
    import pathlib
    ip, img = _converted_preview(pathlib.Path(tempfile.mkdtemp()), crop_rect=None)
    ip.enter_dust_mode()
    ip._maybe_request_hires = lambda: None
    h, w = 400, 600
    g = np.clip(_speck(_grain(h, w), 300, 200), 0, 255).astype(np.uint8)
    rgb = np.ascontiguousarray(np.repeat(g[..., None], 3, 2))
    qi = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    ip.current_pixmap = QPixmap.fromImage(qi)
    ip._crop_display_transform = None
    r0 = 5.0 / w
    pts = [[300 / w, 200 / h]]
    assert ip._dust_fitted_r(pts, r0) * w > 10
    assert ip._dust_fitted_r([[300 / w, 200 / h], [330 / w, 200 / h]], r0) == r0
    ip.dust_fit_spot = False
    assert ip._dust_fitted_r(pts, r0) == r0
