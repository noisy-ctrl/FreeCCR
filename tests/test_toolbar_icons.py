#!/usr/bin/env python3
"""The canvas toolbar's pictures (spec/toolbar-icons.md)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtGui import QColor, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from ui.main_window import MainWindow  # noqa: E402


def test_every_left_hand_button_is_a_picture_in_order():
    ip = MainWindow().image_preview
    names = [a.text() for a in ip.toolbar.actions() if a.text()]
    left = names[:names.index("Capture")]
    assert left == ["Rotate Left", "Rotate Right", "Mirror Vertical", "Mirror Horizontal",
                    "Crop", "Sprocket Holes White", "Area", "Gradient", "Dust removal",
                    "Flat field map", "HDR", "False colour", "Sample circle", "Skin", "Collage",
                    "Film Presets"]
    by = {a.text(): a for a in ip.toolbar.actions()}
    for n in left + ["Capture"]:
        assert not by[n].icon().isNull(), n
    for n in ("Area", "Gradient", "Dust removal", "Flat field map", "HDR",
              "False colour", "Crop", "Sample circle", "Skin", "Collage", "Film Presets"):
        assert by[n].toolTip().startswith(n + ":"), n      # no words on the button
    for n in ("Auto Crop", "Un-convert", "Convert", "Export…"):
        assert by[n].icon().isNull(), n                     # these stay words


def test_false_colour_is_coloured_and_greys_when_off():
    ip = MainWindow().image_preview
    icon = ip.fc_action.icon()

    def saturation(mode):
        img = icon.pixmap(48, 48, mode, QIcon.Off).toImage()
        best = 0
        for y in range(0, img.height(), 2):
            for x in range(0, img.width(), 2):
                c = QColor(img.pixelColor(x, y))
                if c.alpha() > 200:
                    best = max(best, c.hsvSaturation())
        return best
    assert saturation(QIcon.Normal) > 150
    assert saturation(QIcon.Disabled) < 20


def test_crop_follows_the_panel_crop_button():
    w = MainWindow()
    ip = w.image_preview
    assert not ip.crop_action.isEnabled()              # nothing loaded
    called = []
    w.sliders_panel._on_crop_clicked = lambda: called.append(1)
    w.sliders_panel.crop_btn.setEnabled(False)
    assert ip.open_crop() is False and called == []
    w.sliders_panel.crop_btn.setEnabled(True)
    assert ip.open_crop() is True and called == [1]


def test_the_canister_opens_film_presets():
    """Its menu ends in Edit Film Presets… (spec/film-presets-menu.md)."""
    w = MainWindow()
    pages = []
    w.open_settings = lambda page=None: pages.append(page)
    menu = w.image_preview.film_menu()
    menu.actions()[-1].trigger()
    assert pages == ["Film Presets"]
