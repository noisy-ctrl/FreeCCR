"""Export sits at the right-hand end of the preview toolbar, next to the
histogram, with the other tools left-aligned."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QSizePolicy  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])
_KEEP = []


def test_export_is_pushed_to_the_right_end():
    from ui.main_window import MainWindow
    w = MainWindow()
    _KEEP.append(w)            # closing asks about unsaved work: just keep it
    w.resize(1600, 900)
    w.show()
    _app.processEvents()
    ip = w.image_preview
    tb = ip.toolbar
    export = tb.widgetForAction(ip.export_action)
    fc = tb.widgetForAction(ip.fc_action)
    assert ip.export_push.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    right = tb.width()
    ex = export.geometry()
    zoom = ip.zoom_combo.geometry()
    # Export, then only the zoom box (revision 2026-10-30: zoom far right)
    assert ex.right() < zoom.left() and zoom.right() > right - 40
    assert fc.geometry().right() < ex.left() - 100       # the rest stays on the left


def test_convert_and_un_convert_sit_with_export_behind_a_plain_gap():
    """spec/toolbar-rework.md, revision 2026-10-30: ... False colour ....
    Un-convert, Convert, a gap with no line, Export."""
    from ui.main_window import MainWindow
    from widgets.image_preview import EXPORT_GAP_PX
    w = MainWindow()
    _KEEP.append(w)
    w.resize(1600, 900)
    w.show()
    _app.processEvents()
    ip = w.image_preview
    tb = ip.toolbar
    acts = tb.actions()
    i_un, i_conv, i_exp = (acts.index(ip.unconvert_action), acts.index(ip.convert_action),
                           acts.index(ip.export_action))
    assert i_un < i_conv < i_exp
    between = acts[i_un:i_exp]
    assert not any(a.isSeparator() for a in between)          # no line
    assert acts.index(ip.fc_action) < i_un                    # after the view tools
    un = tb.widgetForAction(ip.unconvert_action).geometry()
    conv = tb.widgetForAction(ip.convert_action).geometry()
    exp = tb.widgetForAction(ip.export_action).geometry()
    fc = tb.widgetForAction(ip.fc_action).geometry()
    assert un.right() < conv.left() and conv.right() < exp.left()
    assert exp.left() - conv.right() >= EXPORT_GAP_PX           # the break
    assert EXPORT_GAP_PX > 18                                 # wider than it was
    assert un.left() - fc.right() > 100                       # pushed right, away from the rest
    assert ip.zoom_combo.geometry().left() > exp.right()      # zoom box last


def test_auto_crop_sits_left_of_un_convert_behind_a_plain_gap(monkeypatch):
    """The user, 2026-11-08: an Auto Crop button left of Un-convert, with a
    space between them; it runs the panel's Auto Crop."""
    from ui.main_window import MainWindow
    from widgets.image_preview import AUTO_CROP_GAP_PX
    w = MainWindow()
    _KEEP.append(w)
    w.resize(1600, 900)
    w.show()
    _app.processEvents()
    ip = w.image_preview
    tb = ip.toolbar
    acts = tb.actions()
    i_ac, i_un = acts.index(ip.auto_crop_action), acts.index(ip.unconvert_action)
    assert i_ac < i_un
    assert not any(a.isSeparator() for a in acts[i_ac:i_un])
    ac = tb.widgetForAction(ip.auto_crop_action).geometry()
    un = tb.widgetForAction(ip.unconvert_action).geometry()
    assert un.left() - ac.right() >= AUTO_CROP_GAP_PX
    calls = []
    monkeypatch.setattr(w.sliders_panel, "_on_auto_crop_current", lambda: calls.append(1))
    monkeypatch.setattr(w.sliders_panel, "_on_auto_crop_all", lambda: calls.append("all"))
    monkeypatch.setattr(w.sliders_panel, "_on_auto_crop_selected",
                        lambda sel: calls.append(("sel", tuple(sel))))
    asked = []
    answer = ["current"]
    monkeypatch.setattr(ip, "_ask_auto_crop_scope",
                        lambda n: asked.append(n) or answer[0])
    ip.current_idx = None
    ip.auto_crop_action.trigger()
    assert calls == [] and asked == []                        # nothing on screen
    ip.current_idx = 0
    ip.auto_crop_action.trigger()
    assert calls == [1]
    # it asks first, like Convert (spec/auto-crop-button-choice.md)
    monkeypatch.setattr(w.thumbnail_list, "selected_indices", lambda: [0, 2, 3])
    answer[0] = "selected"
    ip.auto_crop_action.trigger()
    assert asked[-1] == 3 and calls[-1] == ("sel", (0, 2, 3))
    answer[0] = "all"
    ip.auto_crop_action.trigger()
    assert calls[-1] == "all"
    answer[0] = None
    n = len(calls)
    ip.auto_crop_action.trigger()
    assert len(calls) == n                                    # Cancel


def test_the_auto_crop_question_offers_selected_only_for_a_selection(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow
    w = MainWindow()
    _KEEP.append(w)
    ip = w.image_preview
    seen = []

    def fake_exec(box):
        seen.append(([b.text() for b in box.buttons()], box.defaultButton().text()))
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert ip._ask_auto_crop_scope(1) is None
    assert ip._ask_auto_crop_scope(4) is None
    one, many = seen
    assert "Auto Crop Current" in one[0] and "Auto Crop All" in one[0]
    assert not any(t.startswith("Auto Crop Selected") for t in one[0])
    assert one[1] == "Auto Crop Current"
    assert "Auto Crop Selected (4)" in many[0] and many[1] == "Auto Crop Selected (4)"
