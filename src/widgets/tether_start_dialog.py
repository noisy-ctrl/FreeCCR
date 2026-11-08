"""The one window that starts Hot Folder Tether (spec/tether-start-window.md).

Folder, what is being scanned (three picture tiles,
spec/scan-type-tiles.md), and the two choices that used to be separate
questions (close the open images, import the files already in the folder),
in short words. `TetherStartDialog.options()` returns the answers; the main
window acts on them.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout)

from ui import theme


def count_supported(folder):
    """How many files in `folder` Hot Folder Tether could load (0 if none or
    unreadable)."""
    from core.tether_watcher import is_supported
    try:
        return sum(1 for e in os.scandir(folder) if e.is_file() and is_supported(e.name))
    except OSError:
        return 0


class TetherStartDialog(QDialog):
    # Capture's `light_dialog` sets this to the light look's recolouring; the
    # scan-type tiles paint through it (spec/scan-type-tiles.md).
    @property
    def restyle(self):
        return getattr(self, "_restyle", None) or (lambda qss: qss)

    @restyle.setter
    def restyle(self, fn):
        self._restyle = fn
        tiles = getattr(self, "scan_tiles", None)
        if tiles is not None:
            tiles.set_colour(fn)

    def __init__(self, parent=None, n_open=0, folder="", scan_type="colour",
                 from_monitor=False, chooser=None, roll="", film_format="", film="",
                 films=()):
        super().__init__(parent)
        self.setWindowTitle("Start Hot Folder Tether")
        self._n_open = int(n_open or 0)
        self._chooser = chooser or (lambda start: QFileDialog.getExistingDirectory(
            self, "Folder to watch", start))
        lay = QVBoxLayout(self)
        intro = QLabel("Loads each new file saved into the folder.")
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.GAP_BTN)
        grid.addWidget(QLabel("Folder"), 0, 0, Qt.AlignRight)
        self.folder_edit = QLineEdit(folder or "")
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setMinimumWidth(320)
        self.choose_btn = QPushButton("Choose…")
        self.choose_btn.clicked.connect(self._choose)
        row = QHBoxLayout()
        row.addWidget(self.folder_edit, 1)
        row.addWidget(self.choose_btn)
        grid.addLayout(row, 0, 1)
        grid.addWidget(QLabel("Scanning"), 1, 0, Qt.AlignRight)
        from widgets.scan_type_tiles import ScanTypeTiles
        self.scan_tiles = ScanTypeTiles(scan_type, colour=self.restyle)
        grid.addWidget(self.scan_tiles, 1, 1)
        # what the captures are called and logged as (spec/capture-log-and-names.md)
        from core import capture_log, film_formats
        grid.addWidget(QLabel("Roll"), 2, 0, Qt.AlignRight)
        self.roll_edit = QLineEdit(capture_log.clean_roll(roll) or capture_log.DEFAULT_ROLL)
        self.roll_edit.setToolTip(
            "Starts each capture's file name, e.g. R0034_645_Portra400_013_RED.RAF, and "
            "its line in the capture log. It counts up for each new roll; type your own "
            "if you like (letters, digits and -).")
        self.roll_edit.setMaximumWidth(160)
        grid.addWidget(self.roll_edit, 2, 1, Qt.AlignLeft)
        grid.addWidget(QLabel("Format"), 3, 0, Qt.AlignRight)
        self.format_combo = QComboBox()
        self.format_combo.addItem("Not set", "")
        for key, label, _r, _c in film_formats.FILM_FORMATS:
            self.format_combo.addItem(label, key)
        i = self.format_combo.findData(film_format or "")
        self.format_combo.setCurrentIndex(max(0, i))
        grid.addWidget(self.format_combo, 3, 1, Qt.AlignLeft)
        grid.addWidget(QLabel("Film"), 4, 0, Qt.AlignRight)
        self.film_combo = QComboBox()
        self.film_combo.setEditable(True)
        self.film_combo.setInsertPolicy(QComboBox.NoInsert)
        for f in films or ():
            if f:
                self.film_combo.addItem(str(f))
        self.film_combo.setEditText(film or "")
        self.film_combo.lineEdit().setPlaceholderText("e.g. Portra 400 (optional)")
        self.film_combo.setMinimumWidth(200)
        grid.addWidget(self.film_combo, 4, 1, Qt.AlignLeft)
        lay.addLayout(grid)

        n = self._n_open
        self.close_box = QCheckBox(f"Close the {n} open image{'' if n == 1 else 's'} first")
        self.close_box.setToolTip("Edits are saved and come back when you reopen them.")
        self.close_box.setVisible(n > 0)
        lay.addWidget(self.close_box)
        self.import_box = QCheckBox("")
        lay.addWidget(self.import_box)
        self.dont_ask_box = QCheckBox("Don't ask when Capture opens")
        self.dont_ask_box.setVisible(bool(from_monitor))
        lay.addWidget(self.dont_ask_box)

        self.buttons = QDialogButtonBox()
        self.start_btn = self.buttons.addButton("Start", QDialogButtonBox.AcceptRole)
        self.cancel_btn = self.buttons.addButton(QDialogButtonBox.Cancel)
        theme.style_button(self.start_btn, "primary", default=True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._refresh_folder()

    def roll_value(self):
        from core import capture_log
        return capture_log.clean_roll(self.roll_edit.text()) or capture_log.DEFAULT_ROLL

    def _choose(self):
        picked = self._chooser(self.folder_edit.text() or "")
        if picked:
            self.folder_edit.setText(os.path.normpath(picked))
            self._refresh_folder()

    def _refresh_folder(self):
        folder = self.folder_edit.text()
        ok = bool(folder) and os.path.isdir(folder)
        self.start_btn.setEnabled(ok)
        m = count_supported(folder) if ok else 0
        self.import_box.setText(
            f"Import the {m} file{'' if m == 1 else 's'} already in the folder")
        self.import_box.setVisible(m > 0)
        if m == 0:
            self.import_box.setChecked(False)
        self.adjustSize()

    def options(self, started):
        return {"start": bool(started),
                "folder": self.folder_edit.text(),
                "scan_type": self.scan_tiles.currentData(),
                "roll": self.roll_value(),
                "format": self.format_combo.currentData() or "",
                "film": self.film_combo.currentText().strip(),
                "close_open": self._n_open > 0 and self.close_box.isChecked(),
                "import_existing": (not self.import_box.isHidden()) and self.import_box.isChecked(),
                "dont_ask": (not self.dont_ask_box.isHidden()) and self.dont_ask_box.isChecked()}
