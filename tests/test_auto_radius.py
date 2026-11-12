"""Auto Radius (spec/auto-sharpen-radius.md, revision b: sized to the grain):
measured on synthetic scans whose grain size is known, and the Details
button."""
import os
import sys

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import auto_radius as ar  # noqa: E402


def scene(se, sg, amp=0.02, n=2000, seed=0, white=False, soft_right=None):
    """Hard-edged shapes blurred by a Gaussian of `se` px (the right half by
    `soft_right` px when given, joined over a smooth blend), plus grain made
    of white noise blurred by `sg` px (or white noise), std `amp`."""
    r = np.random.default_rng(seed)
    img = np.full((n, n), 0.3, np.float32)
    for _ in range(110):
        c = (int(r.integers(0, n)), int(r.integers(0, n)))
        v = float(r.uniform(0.1, 0.9))
        if r.random() < 0.5:
            cv2.circle(img, c, int(r.integers(60, 240)), v, -1)
        else:
            cv2.rectangle(img, c, (c[0] + int(r.integers(80, 360)),
                                   c[1] + int(r.integers(80, 360))), v, -1)
    sharp = cv2.GaussianBlur(img, (0, 0), se)
    if soft_right:
        soft = cv2.GaussianBlur(img, (0, 0), soft_right)
        x = np.clip((np.arange(n) - n / 2) / 80.0 + 0.5, 0, 1)[None, :]
        sharp = sharp * (1 - x) + soft * x
    img = sharp.astype(np.float32)
    g = r.normal(0, 1, img.shape).astype(np.float32)
    if not white:
        g = cv2.GaussianBlur(g, (0, 0), sg)
    g *= amp / g.std()
    return np.repeat(np.clip(img + g, 0, 1)[..., None], 3, 2)


def test_radius_div_matches_the_sharpening():
    from core.ccr_processor import SHARPEN_RADIUS_DIV
    assert ar.RADIUS_DIV == SHARPEN_RADIUS_DIV


@pytest.mark.parametrize("sg", [0.6, 1.0, 1.6])
def test_grain_size_is_recovered(sg):
    lum = ar.luma_region(scene(1.0, sg, seed=int(sg * 10) + 1))
    est, noise, tiles = ar.grain_sigma(lum)
    assert est == pytest.approx(sg, rel=0.25) and noise > 0 and tiles >= ar.TILES_MIN // 2


@pytest.mark.parametrize("sg", [0.6, 0.9, 1.2])
def test_radius_is_sized_to_the_grain(sg):
    res = ar.measure(scene(1.0, sg, seed=int(sg * 10) + 2))
    assert res.limited_by == "grain"
    assert res.radius_px == pytest.approx(ar.RADIUS_PER_GRAIN * res.sigma_grain)
    assert res.radius_px == pytest.approx(ar.RADIUS_PER_GRAIN * sg, rel=0.25)
    assert res.slider == int(round(res.radius_px * ar.RADIUS_DIV))
    assert "sized to the grain" in res.reason


def test_edges_do_not_decide_it():
    """Same grain, very different edge blur: the same radius."""
    a = ar.measure(scene(0.6, 0.8, seed=21))
    b = ar.measure(scene(2.5, 0.8, seed=21))
    assert a.slider == pytest.approx(b.slider, abs=3)


def test_sub_pixel_grain_gets_the_smallest_useful_radius():
    res = ar.measure(scene(0.8, 0, seed=13, white=True))
    assert res.limited_by == "fine"
    assert res.slider == int(round(ar.RADIUS_MIN_PX * ar.RADIUS_DIV))
    assert "finer than a pixel" in res.reason


def test_very_coarse_grain_is_capped():
    res = ar.measure(scene(1.0, 2.6, seed=14))
    assert res.limited_by == "cap"
    assert res.slider == int(round(ar.RADIUS_MAX_PX * ar.RADIUS_DIV))


def test_no_smooth_area_changes_nothing():
    rng = np.random.default_rng(2)
    busy = rng.random((300, 300, 3)).astype(np.float32)   # too small for 12 tiles
    res = ar.measure(busy)
    assert res.slider is None and "smooth areas" in res.reason


def test_a_smaller_decode_is_rescaled_to_full_resolution():
    img = scene(1.0, 0.7, seed=17)
    a = ar.measure(img)
    b = ar.measure(img, full_long=2 * img.shape[0])
    assert b.sigma_grain == pytest.approx(2 * a.sigma_grain)
    assert b.radius_px == pytest.approx(min(2 * a.radius_px, ar.RADIUS_MAX_PX))


# --- the Details button ------------------------------------------------------
@pytest.fixture
def panel(tmp_path):
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
    QApplication.instance() or QApplication(sys.argv[:1])
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from widgets.sliders_panel import SlidersPanel

    path = str(tmp_path / "scan.png")
    cv2.imwrite(path, np.full((40, 60, 3), 20000, np.uint16))
    img = CCRImage(path)
    img.converted = True
    img.adjustment_settings = {}
    img.resized_raw = np.full((40, 60, 3), 30000, np.uint16)
    img.update_thumbnail_and_preview = lambda: None

    class _PreviewStub:
        def update_preview(self, idx):
            pass

    class _Host(QWidget):
        def __init__(self):
            super().__init__()
            self.image_preview = _PreviewStub()
            self.mid = QWidget(self)

    host = _Host()
    QVBoxLayout(host.mid).addWidget(SlidersPanel(host.mid))
    p = host.mid.layout().itemAt(0).widget()
    p._auto_radius_threaded = False
    saved = (ccr_backend.images, ccr_backend.file_paths)
    ccr_backend.images, ccr_backend.file_paths = [img], [img.file_path]
    p.current_idx = 0
    yield p, img
    ccr_backend.images, ccr_backend.file_paths = saved


def _slider(p, key):
    return p.sliders[p.adjustment_keys.index(key)]


def test_auto_sets_radius_only_as_one_undo_step(panel, monkeypatch):
    p, img = panel
    amount = _slider(p, "sharpen_amount").value()
    res = ar.RadiusResult(1.64, 41, 0.82, 40, "grain", "Auto Radius 41 (1.6 px): sized to the grain (0.82 px).")
    monkeypatch.setattr(type(p), "_measure_radius", staticmethod(lambda _img: res))
    pushes = []
    monkeypatch.setattr(img, "push_undo_state", lambda *a, **k: pushes.append(1))
    p._on_auto_radius()
    assert _slider(p, "sharpen_radius").value() == 41
    assert img.adjustment_settings["sharpen_radius"] == 41
    assert _slider(p, "sharpen_amount").value() == amount
    assert len(pushes) == 1
    assert p.auto_radius_btn.isEnabled() and p._auto_radius_worker is None


def test_a_failed_measure_changes_nothing(panel, monkeypatch):
    p, img = panel
    before = _slider(p, "sharpen_radius").value()
    monkeypatch.setattr(type(p), "_measure_radius",
                        staticmethod(lambda _img: ar.RadiusResult(None, None, reason="Not enough smooth areas to measure the grain in.")))
    p._on_auto_radius()
    assert _slider(p, "sharpen_radius").value() == before
    assert "sharpen_radius" not in img.adjustment_settings
    assert p.auto_radius_btn.isEnabled()


def test_a_crash_in_measuring_is_reported_not_raised(panel, monkeypatch):
    p, _img = panel

    def boom(_img):
        raise RuntimeError("decode failed")
    monkeypatch.setattr(type(p), "_measure_radius", staticmethod(boom))
    p._on_auto_radius()
    assert p.auto_radius_btn.isEnabled()


def test_measure_image_reads_a_copy_at_full_resolution(tmp_path):
    from core.ccr_image import CCRImage
    path = str(tmp_path / "s.tif")
    import tifffile
    tifffile.imwrite(path, (scene(0.8, 0.7, n=900, seed=20) * 65535).astype(np.uint16),
                     photometric="rgb")
    img = CCRImage(path)
    before = dict(img.__dict__)
    res = ar.measure_image(img)
    assert res.tiles > 0
    assert img.__dict__.keys() == before.keys()
