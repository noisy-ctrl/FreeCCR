"""Clear All takes the found dust too, and Dust removal sits at the end of
the toolbar's left-hand pictures (spec/dust-clear-found.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cv2  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from ui import theme
    theme.apply_theme(app)
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from ui.main_window import MainWindow
    rng = np.random.default_rng(1)
    pic = (120 + rng.normal(0, 6, (300, 450, 3))).clip(0, 255).astype(np.uint8)
    p = str(tmp_path / "frame.png")
    cv2.imwrite(p, pic)
    img = CCRImage(p)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "save_catalog", lambda *a, **k: None)
    w = MainWindow()
    w.resize(1600, 1000)
    w.thumbnail_list.load_thumbnails()
    app.processEvents()
    w.toggle_dust_removal(True)
    app.processEvents()
    yield w, img, app
    w.toggle_dust_removal(False)
    w.hide()


def _spot(x=0.5, y=0.5, kind="brush", **extra):
    sp = {"pts": [[x, y]], "r": 0.01, "kind": kind}
    sp.update(extra)
    return sp


def _found(n):
    return [_spot(0.2 + 0.1 * i, 0.3, kind="ir", ir_c=1) for i in range(n)]


def test_the_question_names_what_goes():
    from widgets.dust_panel import clear_question
    assert clear_question(3, 0) == "Take the 3 heals off this frame?"
    assert clear_question(0, 1) == "Take the 1 found spot not painted over yet off this frame?"
    assert clear_question(1, 5) == ("Take the 1 heal and the 5 found spots not painted "
                                    "over yet off this frame?")


def test_clear_all_takes_heals_and_found_spots_in_one_undo(win, monkeypatch):
    w, img, app = win
    panel, prev = w.dust_panel, w.image_preview
    img.dust_spots = [_spot()]
    img.ir_dust_pending = _found(4)
    asked = []
    monkeypatch.setattr(panel, "_confirm_clear", lambda t: asked.append(t) or True)
    panel._on_clear_all()
    assert asked == ["Take the 1 heal and the 4 found spots not painted over yet off this frame?"]
    assert img.dust_spots == [] and not img.ir_dust_pending
    assert panel.status_label.text() == "Cleared."
    assert prev.dust_undo()                        # one step brings both back
    assert len(img.dust_spots) == 1 and len(img.ir_dust_pending) == 4


def test_found_spots_alone_are_cleared_and_said_so(win, monkeypatch):
    w, img, app = win
    panel = w.dust_panel
    img.dust_spots = []
    img.ir_dust_pending = _found(2)
    img.mark_sens = 50                              # it had searched
    monkeypatch.setattr(panel, "_confirm_clear", lambda t: True)
    panel._on_clear_all()
    assert not img.ir_dust_pending
    assert panel.ir_status.text().startswith("Cleared. Find looks again.")


def test_nothing_to_clear_asks_nothing(win, monkeypatch):
    w, img, app = win
    panel = w.dust_panel
    img.dust_spots, img.ir_dust_pending = [], None
    monkeypatch.setattr(panel, "_confirm_clear", lambda t: pytest.fail("asked"))
    panel._on_clear_all()
    assert panel.status_label.text() == "Nothing to clear."


def test_no_clears_nothing(win, monkeypatch):
    w, img, app = win
    panel = w.dust_panel
    img.dust_spots = [_spot()]
    img.ir_dust_pending = _found(1)
    monkeypatch.setattr(panel, "_confirm_clear", lambda t: False)
    panel._on_clear_all()
    assert len(img.dust_spots) == 1 and len(img.ir_dust_pending) == 1


def test_dust_removal_ends_the_left_hand_pictures():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv[:1])
    from ui.main_window import MainWindow
    ip = MainWindow().image_preview
    acts = ip.toolbar.actions()
    i = acts.index(ip.dust_action)
    assert acts[i - 1] is ip.skin_action
    names = [a.text() for a in acts if a.text()]
    assert names[names.index("Dust removal") + 1] == "Film Presets"
