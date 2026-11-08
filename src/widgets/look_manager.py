"""Manage Film Looks (spec/look-manager.md): the looks library in one window
— see each look, import, delete (to the Trash), and open its folder.

Opened from the Film Look lists' "Manage…" entry. `in_use(name)` tells the
window how many loaded frames use a look, so deleting one can say so.
"""
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout)

from ui import theme


def _display(name):
    return name[:-5] if name.lower().endswith(".cube") else name


class LookManager(QDialog):
    def __init__(self, parent=None, in_use=None, confirm=None, opener=None,
                 chooser=None):
        super().__init__(parent)
        from core import look_lut
        self._ll = look_lut
        self._in_use = in_use or (lambda name: 0)
        self._confirm = confirm or self._ask_delete
        self._opener = opener or (lambda path: QDesktopServices.openUrl(
            QUrl.fromLocalFile(path)))
        self._chooser = chooser or (lambda: QFileDialog.getOpenFileNames(
            self, "Import film looks", "", "3D LUT (*.cube)")[0])
        self.changed = False                 # the library changed: lists rebuild
        self.setWindowTitle("Film Looks")
        lay = QVBoxLayout(self)
        intro = QLabel("The looks in FreeCCR's library. Deleting one moves its "
                       "file to the Trash.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        lay.addWidget(intro)
        self.list = QListWidget()
        self.list.setMinimumSize(360, 220)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._refresh_buttons)
        lay.addWidget(self.list)
        self.folder_label = QLabel("")
        self.folder_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.folder_label.setWordWrap(True)
        lay.addWidget(self.folder_label)
        row = theme.apply_button_row(QHBoxLayout())
        self.import_btn = QPushButton("Import…")
        self.delete_btn = QPushButton("Delete…")
        self.folder_btn = QPushButton("Show Folder")
        theme.style_button(self.delete_btn, "danger")
        for b in (self.import_btn, self.delete_btn, self.folder_btn):
            b.setFixedHeight(theme.CONTROL_H)
            row.addWidget(b)
        self.import_btn.clicked.connect(self._import)
        self.delete_btn.clicked.connect(self._delete)
        self.folder_btn.clicked.connect(self.show_folder)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
        self.refresh()

    # ------------------------------------------------------------------ #
    def refresh(self):
        ll = self._ll
        self.list.clear()
        for name in ll.list_looks():
            bits = []
            look = ll.resolve_look(name)
            if look is None:
                bits.append("can't be read")
            elif getattr(look, "balance", None) is not None:
                bits.append("balance data")
            n = int(self._in_use(name) or 0)
            if n:
                bits.append(f"used by {n} open frame{'' if n == 1 else 's'}")
            text = _display(name) + (f"   ·   {', '.join(bits)}" if bits else "")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, name)
            self.list.addItem(item)
        if self.list.count() == 0:
            item = QListWidgetItem("No looks yet: Import… adds a .cube file.")
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        self.folder_label.setText(ll.looks_dir())
        self._refresh_buttons()

    def selected(self):
        return [it.data(Qt.UserRole) for it in self.list.selectedItems()
                if it.data(Qt.UserRole)]

    def _refresh_buttons(self):
        self.delete_btn.setEnabled(bool(self.selected()))

    def show_folder(self):
        self._opener(self._ll.looks_dir())

    def _import(self):
        paths = self._chooser() or []
        bad = []
        for p in paths:
            try:
                self._ll.import_look(p)
                self.changed = True
            except (self._ll.CubeError, OSError) as e:
                bad.append(f"{os.path.basename(p)}: {e}")
        self.refresh()
        if bad:
            QMessageBox.warning(self, "Import film looks",
                                "Couldn't import:\n\n" + "\n".join(bad))

    def _ask_delete(self, names, used):
        what = (f"“{_display(names[0])}”" if len(names) == 1
                else f"these {len(names)} looks")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Delete film look")
        box.setText(f"Delete {what}?")
        info = "The file goes to the Trash, so you can still get it back."
        if used:
            info += (f" {used} open frame{'' if used == 1 else 's'} use"
                     f"{'s' if used == 1 else ''} it: they will show without it.")
        box.setInformativeText(info)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec() == QMessageBox.Yes

    def _delete(self):
        names = self.selected()
        if not names:
            return
        used = sum(int(self._in_use(n) or 0) for n in names)
        if not self._confirm(names, used):
            return
        failed = [n for n in names if not self._ll.delete_look(n)]
        self.changed = self.changed or len(failed) < len(names)
        self.refresh()
        if failed:
            QMessageBox.warning(self, "Delete film look",
                                "Couldn't delete: " + ", ".join(_display(n) for n in failed))
