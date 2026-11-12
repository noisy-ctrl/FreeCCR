"""Auto Radius (spec/auto-sharpen-radius.md): measured on synthetic scans
whose edge blur and grain size are known, and the Details button."""
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


@pytest.mark.parametrize("se", [0.6, 0.9, 1.3])
def test_edge_blur_is_recovered(se):
    res = ar.measure(scene(se, 0.7, seed=int(se * 10)))
    assert res.sigma_edge == pytest.approx(se, rel=0.15)
    assert res.edges >= ar.EDGES_MIN


@pytest.mark.parametrize("sg", [0.6, 1.0, 1.6])
def test_grain_size_is_recovered(sg):
    lum = ar.luma_region(scene(1.0, sg, seed=int(sg * 10) + 1))
    est, noise = ar.grain_sigma(lum)
    assert est == pytest.approx(sg, rel=0.25) and noise > 0


def test_fine_edges_set_the_radius():
    res = ar.measure(scene(0.7, 0.8, seed=11))
    assert res.limited_by == "edges"
    assert res.radius_px == pytest.approx(2 * res.sigma_edge)
    assert res.slider == int(round(res.radius_px * ar.RADIUS_DIV))


def test_coarse_grain_holds_the_radius_back():
    res = ar.measure(scene(1.2, 0.55, seed=12))
    assert res.limited_by == "grain"
    assert res.radius_px == pytest.approx(3 * res.sigma_grain, rel=1e-6)
    assert res.radius_px < 2 * res.sigma_edge
    assert "held back by the grain" in res.reason


def test_sub_pixel_noise_sets_no_grain_limit():
    res = ar.measure(scene(0.8, 0, seed=13, white=True))
    assert res.sigma_grain is None and res.limited_by == "edges"


def test_soft_frame_is_capped():
    res = ar.measure(scene(3.5, 1.6, seed=14))
    assert res.limited_by == "cap" and res.slider == int(ar.RADIUS_MAX_PX * ar.RADIUS_DIV)


def test_out_of_focus_half_does_not_pull_it_up():
    mixed = scene(0.8, 0.7, seed=15, soft_right=3.0)
    assert ar.measure(mixed).sigma_edge == pytest.approx(0.8, rel=0.15)


def test_flat_frame_reports_too_few_edges():
    rng = np.random.default_rng(1)
    flat = np.full((1200, 1200, 3), 0.4, np.float32) + rng.normal(0, 0.01, (1200, 1200, 3)).astype(np.float32)
    res = ar.measure(flat)
    assert res.slider is None and "Not enough" in res.reason


def test_a_smaller_decode_is_rescaled_to_full_resolution():
    img = scene(0.8, 0.7, seed=17)
    a = ar.measure(img)
    b = ar.measure(img, full_long=2 * img.shape[0])
    assert b.radius_px == pytest.approx(min(2 * a.radius_px, 2 * ar.RADIUS_MAX_PX))


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
    res = ar.RadiusResult(1.64, 41, 0.82, 0.65, 300, "edges", "Auto Radius 41 (1.6 px): …")
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
                        staticmethod(lambda _img: ar.RadiusResult(None, None, reason="Not enough clean edges to measure (3).")))
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
    assert res.edges > 0
    assert img.__dict__.keys() == before.keys()
