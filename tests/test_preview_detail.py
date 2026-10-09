#!/usr/bin/env python3
"""Preview detail: Fast / Balanced / Full (spec/preview-detail.md)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import test_full_res_zoom as fz  # noqa: E402  (its ImagePreview harness)
from core.ccr_backend import ccr_backend  # noqa: E402


@pytest.fixture
def level():
    saved = (ccr_backend.preview_detail, ccr_backend.sharp_fit_preview,
             ccr_backend.full_res_zoom)
    yield ccr_backend.set_preview_detail
    (ccr_backend.preview_detail, ccr_backend.sharp_fit_preview,
     ccr_backend.full_res_zoom) = saved


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(fz.ip_mod, "HiResDetailWorker", fz._FakeWorker)
    yield


def test_levels_set_flags_and_caps(level):
    assert level("full") == "full"
    assert ccr_backend.sharp_fit_preview and ccr_backend.full_res_zoom
    assert ccr_backend.preview_caps() == (None, None)
    level("balanced")
    assert ccr_backend.sharp_fit_preview and ccr_backend.full_res_zoom
    assert ccr_backend.preview_caps() == (2000, 6000)
    level("fast")
    assert not ccr_backend.sharp_fit_preview and not ccr_backend.full_res_zoom
    assert level("nonsense") == "full"


def test_migration_from_the_old_checkboxes():
    f = ccr_backend.preview_detail_from_flags
    assert f(True, True) == "full"
    assert f(False, False) == "fast"
    assert f(True, False) == "balanced" and f(False, True) == "balanced"


def _ip():
    ip = fz._setup()
    if not fz._viewport_ok(ip):
        pytest.skip("offscreen viewport not sized")
    return ip


def test_balanced_caps_the_fitted_view_render(level, worker, monkeypatch):
    level("full")
    ip = _ip()
    ip.zoom_to_fit()
    monkeypatch.setattr(ip, "_dpr", lambda: 3.0)
    full_want = ip._hires_target_long_side()[1]
    assert full_want > 2000
    level("balanced")
    assert ip._hires_target_long_side() == (True, 2000)


def test_balanced_caps_zoom_at_6000(level, worker):
    level("balanced")
    ip = _ip()
    ip.zoom_to_percent(1.0)
    assert ip._hires_target_long_side() == (False, 6000)
    level("full")
    preview, target = ip._hires_target_long_side()
    assert preview is False and target == pytest.approx(8256, abs=8)


def test_fast_is_the_old_both_off_request(level, worker):
    level("fast")
    ip = _ip()
    ip.zoom_to_percent(1.0)
    assert ip._hires_target_long_side() == (True, fz.CAP)
    ip.zoom_to_fit()
    assert not ip._fit_wants_hires()


def test_settings_combo_seeds_and_applies(level):
    from PySide6.QtWidgets import QWidget
    from widgets.settings_dialog import SettingsDialog
    calls = []

    class _MW(QWidget):
        def on_preview_detail_changed(self, lv):
            calls.append(lv)
            ccr_backend.set_preview_detail(lv)
    level("balanced")
    mw = _MW()
    dlg = SettingsDialog(mw)
    fz._HOSTS.extend([mw, dlg])
    combo = dlg._combo_preview_detail
    assert combo.currentData() == "balanced"
    combo.setCurrentIndex(combo.findData("fast"))
    dlg._apply_pending()
    assert calls == ["fast"] and ccr_backend.preview_detail == "fast"
