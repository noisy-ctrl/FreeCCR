"""The Dust panel's Brush | Eraser, ↶, B / E, the cursor's tag, Paint Over N
Found and the More fold (spec/dust-brush-eraser.md)."""
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
    assert w.image_preview.dust_mode
    yield w, img, app
    w.toggle_dust_removal(False)
    w.hide()


def _spot(x=0.5, y=0.5, kind="brush", **extra):
    sp = {"pts": [[x, y]], "r": 0.01, "kind": kind}
    sp.update(extra)
    return sp


def test_brush_and_eraser_are_one_control(win):
    w, img, app = win
    panel, prev = w.dust_panel, w.image_preview
    assert panel.brush_btn.isChecked() and not panel.erase_btn.isChecked()
    assert prev._dust_tool == "brush"
    panel.erase_btn.setChecked(True)
    assert not panel.brush_btn.isChecked() and prev._dust_tool == "erase"
    panel.erase_btn.setChecked(False)                 # unticking the one in use
    assert panel.brush_btn.isChecked() and prev._dust_tool == "brush"
    panel.brush_btn.setChecked(False)
    assert panel.erase_btn.isChecked() and prev._dust_tool == "erase"
    panel.set_tool("brush")
    assert panel.tool() == "brush" and prev._dust_tool == "brush"
    panel.set_tool("erase")
    panel.bind_image()                                # opening starts on Brush
    assert panel.tool() == "brush" and prev._dust_tool == "brush"


def test_b_and_e_switch_only_in_dust_mode(win):
    w, img, app = win
    b, e = w._dust_tool_shortcuts
    assert b.key().toString() == "B" and e.key().toString() == "E"
    assert b.isEnabled() and e.isEnabled()
    e.activated.emit()
    assert w.dust_panel.tool() == "erase"
    b.activated.emit()
    assert w.dust_panel.tool() == "brush"
    w.toggle_dust_removal(False)
    assert not b.isEnabled() and not e.isEnabled()    # the letters are free again
    w.toggle_dust_removal(True)
    assert b.isEnabled() and e.isEnabled()


def test_undo_takes_back_one_dust_step_whole(win):
    w, img, app = win
    prev, panel = w.image_preview, w.dust_panel
    # a brush spot
    img.push_undo_state()
    img.dust_spots = [_spot()]
    prev._commit_dust_change(img)
    # a paint over of three found spots: one step
    img.push_undo_state()
    img.dust_spots = img.dust_spots + [_spot(0.2 + 0.1 * i, 0.3, kind="ir", ir_c=i)
                                       for i in range(3)]
    prev._commit_dust_change(img)
    assert len(img.dust_spots) == 4
    zoom_calls = []
    prev._reset_zoom = lambda *a, **k: zoom_calls.append(1)
    panel.undo_btn.click()
    assert len(img.dust_spots) == 1                   # all three at once
    w.undo_last_action()                              # Ctrl+Z in dust mode: the same
    assert img.dust_spots == []
    assert zoom_calls == []                           # the view kept
    panel.undo_btn.click()
    assert "Nothing to undo here" in panel.status_label.text()


def test_undo_wont_take_back_a_slider_change_in_dust_mode(win):
    w, img, app = win
    prev, panel = w.image_preview, w.dust_panel
    img.push_undo_state()
    img.dust_spots = [_spot()]
    prev._commit_dust_change(img)
    img.push_undo_state()
    img.adjustment_settings["brightness"] = img.adjustment_settings.get("brightness", 0) + 7
    assert not prev.dust_undo()
    assert img.dust_spots and len(img.undo_stack) == 2      # left for the general undo


def test_an_erase_is_one_undo_step(win):
    w, img, app = win
    prev = w.image_preview
    img.dust_spots = [_spot(0.3, 0.3), _spot(0.7, 0.7)]
    prev._commit_dust_change(img)
    prev.set_dust_tool("erase")
    prev._dust_erase(img, [[0.3, 0.3]])
    assert len(img.dust_spots) == 1
    assert prev.dust_undo()
    assert len(img.dust_spots) == 2


def test_the_cursor_says_which_tool(win):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QGraphicsItem, QGraphicsLineItem
    from widgets.image_preview import _ToolTag
    w, img, app = win
    prev, panel = w.image_preview, w.dust_panel
    c = prev.pixmap_item.sceneBoundingRect().center()
    prev._update_dust_cursor(QPointF(c))
    kids = prev._dust_cursor_item.childItems()
    tags = [k for k in kids if isinstance(k, _ToolTag)]
    assert [t.text for t in tags] == ["Brush"]
    assert tags[0].flags() & QGraphicsItem.ItemIgnoresTransformations
    assert not any(isinstance(k, QGraphicsLineItem) for k in kids)
    panel.set_tool("erase")                           # redrawn where it is
    kids = prev._dust_cursor_item.childItems()
    assert [t.text for t in kids if isinstance(t, _ToolTag)] == ["Eraser"]
    assert any(isinstance(k, QGraphicsLineItem) for k in kids)   # struck through


def test_found_counts_count_finds_not_spots():
    from widgets.dust_panel import found_counts
    pend = [
        {"kind": "ir", "ir_c": 4, "pts": [[0, 0], [1, 1]]},      # a fibre: three strokes
        {"kind": "ir", "ir_c": 4, "pts": [[1, 1], [2, 2]]},
        {"kind": "ir", "ir_c": 4, "pts": [[2, 2], [3, 3]]},
        {"kind": "ir", "ir_c": 9, "pts": [[5, 5]]},
        {"kind": "ir", "ir_bridge": [4, 9], "pts": [[3, 3], [5, 5]]},   # joins, not a find
        {"kind": "mark", "mk_c": 1, "mk_f": [1.0, 2.0], "pts": [[7, 7]]},
        {"kind": "mark", "mk_c": 1, "mk_f": [1.0, 2.0], "pts": [[7, 8]]},
        {"kind": "mark", "mk_c": 2, "mk_f": [3.0, 2.0], "pts": [[9, 9]]},
    ]
    assert found_counts(pend) == (2, 2)
    assert found_counts([]) == (0, 0) and found_counts(None) == (0, 0)


def test_paint_over_n_found_and_the_wrong_ones_hint(win):
    w, img, app = win
    panel = w.dust_panel
    assert not panel.ir_heal_btn.isEnabled()
    assert panel.ir_heal_btn.text() == "Paint Over Found"
    assert panel.ir_wrong_hint.isHidden()
    img.ir_dust_pending = [{"kind": "ir", "ir_c": 1, "pts": [[0.4, 0.4]], "r": 0.01},
                           {"kind": "ir", "ir_c": 1, "pts": [[0.41, 0.4]], "r": 0.01},
                           {"kind": "mark", "mk_c": 3, "mk_f": [1.0], "pts": [[0.6, 0.6]],
                            "r": 0.01}]
    panel.refresh_ir_section()
    assert panel.ir_heal_btn.isEnabled()
    assert panel.ir_heal_btn.text() == "Paint Over 2 Found"
    assert not panel.ir_wrong_hint.isHidden()
    assert "Found 2: 1 dust (amber), 1 mark (blue)." in panel.ir_status.text()
    assert panel.ir_status.text().startswith("No IR shot.")


def test_the_more_fold(win, monkeypatch):
    from PySide6.QtCore import QSettings
    w, img, app = win
    panel = w.dust_panel
    for name in ("ir_attach_btn", "show_sources_cb", "clear_btn", "download_btn",
                 "detect_btn", "detect_all_btn"):
        wd = getattr(panel, name)
        assert panel.more_box.isAncestorOf(wd), name
    for name in ("brush_btn", "erase_btn", "undo_btn", "ir_find_btn", "ir_before_btn",
                 "ir_heal_btn", "ir_sens_slider", "mark_sens_slider"):
        assert not panel.more_box.isAncestorOf(getattr(panel, name)), name
    panel.more_btn.setChecked(False)
    assert panel.more_box.isHidden() and panel.more_btn.text().startswith("▸ More:")
    panel.more_btn.setChecked(True)
    assert not panel.more_box.isHidden() and panel.more_btn.text().startswith("▾ More:")
    assert QSettings("FreeCCR", "FreeCCR").value("dust/more_open", False, type=bool)
    panel.more_btn.setChecked(False)
    assert not QSettings("FreeCCR", "FreeCCR").value("dust/more_open", True, type=bool)


def test_the_more_fold_starts_folded(win):
    from PySide6.QtCore import QSettings
    from widgets.dust_panel import DustRemovalPanel
    w, img, app = win
    QSettings("FreeCCR", "FreeCCR").remove("dust/more_open")
    panel = DustRemovalPanel(w, w.image_preview)
    assert panel.more_box.isHidden() and not panel.more_btn.isChecked()
    panel.deleteLater()
