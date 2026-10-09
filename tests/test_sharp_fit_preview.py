#!/usr/bin/env python3
"""Sharp preview at the fitted view (spec/sharp-fit-preview.md): the preview
item scales smoothly (never nearest-neighbour)."""

import os
import sys

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])
_LIVE = []


class _Stub:
    def __getattr__(self, name):
        return lambda *a, **k: None


def test_preview_item_uses_smooth_scaling(tmp_path):
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from widgets.image_preview import ImagePreview
    path = str(tmp_path / "scan.png")
    g = np.linspace(40, 220, 120, dtype=np.uint8)
    cv2.imwrite(path, np.dstack([np.tile(g, (90, 1))] * 3))
    prev = list(ccr_backend.images)
    try:
        ccr_backend.images = [CCRImage(path)]
        grand = QWidget()
        for attr in ("sliders_panel", "thumbnail_list", "scopes_panel"):
            setattr(grand, attr, _Stub())
        mid = QWidget(grand)
        ip = ImagePreview(mid)
        _LIVE.extend([grand, mid, ip])
        ip.update_preview(0)
        assert ip.pixmap_item is not None
        assert ip.pixmap_item.transformationMode() == Qt.SmoothTransformation
    finally:
        ccr_backend.images = prev
