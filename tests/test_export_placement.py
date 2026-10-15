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
