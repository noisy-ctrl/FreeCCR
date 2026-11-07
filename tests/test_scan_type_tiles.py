#!/usr/bin/env python3
"""The Start window's scan type as three picture tiles
(spec/scan-type-tiles.md): RGB, White light, B&W."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from ui import theme  # noqa: E402
from widgets.scan_type_tiles import ScanTypeTiles  # noqa: E402


def _rgb(widget, scale=2):
    """The widget as painted, H x W x 3 uint8 RGB, at `scale`."""
    widget.resize(widget.sizeHint())
    img = QImage(widget.size() * scale, QImage.Format_RGB32)
    img.setDevicePixelRatio(scale)
    img.fill(0)
    widget.render(img)
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    a = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())
    return a[:, :w * 3].reshape(h, w, 3).astype(int)


def _count(a, test):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return int(np.count_nonzero(test(r, g, b)))


RED = lambda r, g, b: (r > 180) & (g < 110) & (b < 110)          # noqa: E731
GREEN = lambda r, g, b: (g > 150) & (r < 120) & (b < 120)        # noqa: E731
BLUE = lambda r, g, b: (b > 180) & (r < 120) & (g < 150)         # noqa: E731
WHITE = lambda r, g, b: (r > 235) & (g > 235) & (b > 230)        # noqa: E731
ORANGE = lambda r, g, b: (r > 190) & (g > 110) & (g < 160) & (b < 90)   # noqa: E731


def test_three_tiles_in_the_users_order_with_short_names():
    t = ScanTypeTiles("colour")
    assert list(t.tiles) == ["trichrome", "colour", "bw"]
    assert [x.text() for x in t.tiles.values()] == ["RGB", "White light", "B&W"]
    assert t.currentData() == "colour"
    assert [x.isChecked() for x in t.tiles.values()] == [False, True, False]
    assert ScanTypeTiles("trichrome").currentData() == "trichrome"
    assert ScanTypeTiles("nonsense").currentData() == "colour"      # the default
    tip = t.tiles["trichrome"].toolTip()
    assert "trichrome" in tip and "merged" in tip
    assert t.tiles["bw"].accessibleName() == "B&W"
    assert t.accessibleName() == "Scanning"


def test_a_click_picks_one_tile_and_only_that_one():
    t = ScanTypeTiles("colour")
    seen = []
    t.changed.connect(seen.append)
    t.tiles["bw"].click()
    assert t.currentData() == "bw" and seen == ["bw"]
    assert [x.isChecked() for x in t.tiles.values()] == [False, False, True]
    t.tiles["bw"].click()                       # the picked one again: kept
    assert t.currentData() == "bw" and t.tiles["bw"].isChecked() and seen == ["bw"]
    t.setCurrentData("trichrome")               # from code: no signal
    assert t.currentData() == "trichrome" and seen == ["bw"]


def test_arrow_keys_step_and_stop_at_the_ends():
    t = ScanTypeTiles("trichrome")
    seen = []
    t.changed.connect(seen.append)
    QTest.keyClick(t, Qt.Key_Left)              # already first
    assert t.currentData() == "trichrome" and seen == []
    QTest.keyClick(t, Qt.Key_Right)
    QTest.keyClick(t, Qt.Key_Down)
    assert t.currentData() == "bw" and seen == ["colour", "bw"]
    QTest.keyClick(t, Qt.Key_Right)             # already last: no wrap
    assert t.currentData() == "bw"
    QTest.keyClick(t, Qt.Key_Up)
    assert t.currentData() == "colour"


def test_the_row_is_the_tab_stop_not_the_tiles():
    t = ScanTypeTiles()
    assert t.focusPolicy() == Qt.StrongFocus
    assert all(x.focusPolicy() == Qt.NoFocus for x in t.tiles.values())


def test_the_pictures_show_their_light():
    t = ScanTypeTiles("colour")
    rgb, white, bw = (_rgb(t.tiles[k]) for k in ("trichrome", "colour", "bw"))
    # RGB: red, green and blue light under an orange negative
    assert min(_count(rgb, RED), _count(rgb, GREEN), _count(rgb, BLUE)) > 100
    assert _count(rgb, ORANGE) > 1000
    # its sprocket holes are white: no coloured light across the film's top
    holes = rgb[28:38, 36:188]                          # the top row of holes at 2x
    assert _count(holes, WHITE) > 50
    assert max(_count(holes, RED), _count(holes, GREEN), _count(holes, BLUE)) == 0
    # White light: white light, the same negative, no green or blue light
    assert _count(white, WHITE) > 500 and _count(white, ORANGE) > 1000
    assert _count(white, GREEN) == 0 and _count(white, BLUE) == 0
    # B&W: no colour anywhere
    chroma = bw.max(axis=2) - bw.min(axis=2)
    assert chroma.max() < 16 and _count(bw, WHITE) > 500


def test_the_light_look_repaints_the_tiles():
    from widgets.tether_start_dialog import TetherStartDialog
    dlg = TetherStartDialog(folder="", scan_type="trichrome")
    tile = dlg.scan_tiles.tiles["bw"]                 # not picked
    dark = _rgb(tile, 1)[30, 4]
    assert tuple(dark) == tuple(int(theme.SURFACE[i:i + 2], 16) for i in (1, 3, 5))
    dlg.restyle = theme.light_qss_text                # what Capture's light_dialog does
    light = _rgb(tile, 1)[30, 4]
    surf = theme.LIGHT_TOKENS["surface"]
    assert tuple(light) == tuple(int(surf[i:i + 2], 16) for i in (1, 3, 5))
    assert dlg.restyle("#2a2a2a") == theme.LIGHT_TOKENS["panel"]


def test_the_window_answers_with_the_picked_tile(tmp_path):
    from widgets.tether_start_dialog import TetherStartDialog
    dlg = TetherStartDialog(folder=str(tmp_path), scan_type="colour")
    assert dlg.options(True)["scan_type"] == "colour"
    dlg.scan_tiles.tiles["trichrome"].click()
    assert dlg.options(True)["scan_type"] == "trichrome"
    assert dlg.scan_tiles.hasFocus() or not dlg.isVisible()
