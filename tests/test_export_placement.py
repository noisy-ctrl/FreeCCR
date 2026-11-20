"""Export sits at the right-hand end of the preview toolbar, next to the
histogram, with the other tools left-aligned; Film Presets + Capture dead
centre, the conversion words midway, Export boxed (spec/toolbar-centred.md)."""
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


def _shown(width):
    from ui.main_window import MainWindow
    w = MainWindow()
    _KEEP.append(w)
    w.resize(width, 900)
    w.show()
    for _ in range(4):
        _app.processEvents()
    return w, w.image_preview, w.image_preview.toolbar


def _geo(tb, action):
    return tb.widgetForAction(action).geometry()


def test_film_and_capture_sit_dead_centre_at_any_width():
    """The user, 2026-11-20: 'keeping film and capture buttons dead centre'."""
    for width in (1600, 2400, 1900):
        w, ip, tb = _shown(width)
        film, cap = _geo(tb, ip.film_presets_action), _geo(tb, ip.capture_action)
        mid = (film.left() + cap.right() + 1) / 2.0
        assert abs(mid - tb.width() / 2.0) <= 1.0, (width, mid, tb.width())
        assert ip._balance_toolbar() == 0                         # settled
        # the pictures and the words differ in width: the balance pads one side
        assert ip._toolbar_balance != 0


def test_the_conversion_words_sit_midway_between_capture_and_export():
    w, ip, tb = _shown(1800)
    cap, ac = _geo(tb, ip.capture_action), _geo(tb, ip.auto_crop_action)
    conv, exp = _geo(tb, ip.convert_action), _geo(tb, ip.export_action)
    before, after = ac.left() - cap.right(), exp.left() - conv.right()
    assert before > 60 and abs(before - after) <= 2, (before, after)


def test_export_is_boxed_like_the_panels_export():
    from ui import theme
    from widgets.image_preview import EXPORT_BOX_PAD
    w, ip, tb = _shown(1600)
    btn = tb.widgetForAction(ip.export_action)
    qss = btn.styleSheet()
    assert "border: 1px solid " + theme.BORDER_STRONG in qss
    assert "background-color: " + theme.SURFACE_ACTIVE in qss and "font-weight: bold" in qss
    assert btn.height() == ip.zoom_combo.height()
    from PySide6.QtGui import QFont, QFontMetrics
    f = QFont(btn.font())
    f.setBold(True)
    assert btn.width() == QFontMetrics(f).horizontalAdvance("Export…") + EXPORT_BOX_PAD
    # the panel's Export… carries the same look
    assert theme.SURFACE_ACTIVE in w.sliders_panel.export_button.styleSheet()


def test_the_balance_gives_way_at_the_narrowest_canvas():
    """No room to spare: the balance shrinks rather than push Export and the
    zoom box into the overflow arrow."""
    w, ip, tb = _shown(2000)
    learned = ip._toolbar_balance
    assert learned != 0
    w.resize(1100, 900)
    for _ in range(4):
        _app.processEvents()
    assert tb.width() == ip.minimumWidth()
    assert ip._toolbar_balance == learned            # kept, only not spent
    assert tb.sizeHint().width() <= tb.width()
    assert tb.widgetForAction(ip.export_action).isVisible() and ip.zoom_combo.isVisible()
    spent = (ip._balance_left.width() + ip._balance_right_a.width()
             + ip._balance_right_b.width())
    assert spent < abs(ip._toolbar_balance)
    # wide again: the whole balance comes back and it is centred again
    w.resize(2000, 900)
    for _ in range(4):
        _app.processEvents()
    film, cap = _geo(tb, ip.film_presets_action), _geo(tb, ip.capture_action)
    assert abs((film.left() + cap.right() + 1) / 2.0 - tb.width() / 2.0) <= 1.0
