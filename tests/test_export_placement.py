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
    zoom = ip.zoom_combo
    assert ip.export_push.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    right = tb.width()
    ex = export.geometry()
    assert ex.right() > right - 40                       # at the end, a small margin
    assert zoom.geometry().right() < ex.left() - 100     # the rest stays on the left


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
    assert exp.right() > tb.width() - 40
