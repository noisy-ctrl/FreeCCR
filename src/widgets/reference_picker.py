"""Choose an Open Frame… for the Reference compare view
(spec/reference-compare.md): the session's other frames as thumbnails with
their names; double-click or OK picks one."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
                               QVBoxLayout, QLabel)

from ui import theme

ICON = 150


def _icon(img):
    th = getattr(img, "thumbnail", None)
    if th is None:
        th = getattr(img, "resized_preview", None)
    if th is None:
        return QIcon()
    pm = th if isinstance(th, QPixmap) else QPixmap.fromImage(th)
    return QIcon(pm.scaled(ICON, ICON, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class ReferencePicker(QDialog):
    def __init__(self, parent, images):
        super().__init__(parent)
        self.setWindowTitle("Choose an Open Frame")
        self.images = list(images)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Compare the frame on screen with:"))
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(ICON, ICON))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(6)
        self.list.setWordWrap(True)
        for i, img in enumerate(self.images):
            it = QListWidgetItem(_icon(img), getattr(img, "display_name", f"Frame {i + 1}"))
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
        if self.images:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        lay.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(theme.DIALOG_W_MD if hasattr(theme, "DIALOG_W_MD") else 640, 460)

    def chosen(self):
        it = self.list.currentItem()
        return self.images[it.data(Qt.UserRole)] if it is not None else None


def pick_frame(parent, images):
    """The frame picked, or None."""
    if not images:
        return None
    dlg = ReferencePicker(parent, images)
    return dlg.chosen() if dlg.exec() == QDialog.Accepted else None
