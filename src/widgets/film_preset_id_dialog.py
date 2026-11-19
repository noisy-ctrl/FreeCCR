"""Ask for a film preset's ID: film, push/pull, version, notes
(spec/film-presets.md). Used by Capture's Measure and Rename and by Write to
Preset's "New Preset…". `values()` gives {film, push, version, notes}."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                               QLabel, QLineEdit, QSpinBox, QVBoxLayout)

from core import film_presets as fp
from ui import theme


def push_combo(value=0):
    """−2, −1, 0, +1, +2 (0 = box speed), item data the int."""
    c = QComboBox()
    for p in fp.PUSHES:
        c.addItem(fp.push_text(p) or "0", p)
    c.setCurrentIndex(max(0, c.findData(fp.clamp_push(value))))
    c.setToolTip("Push or pull in development, in stops: 0 is box speed. "
                 "Each value is its own preset.")
    c.setMaximumWidth(64)
    return c


class FilmPresetIdDialog(QDialog):

    @staticmethod
    def restyle(qss):
        return qss

    def __init__(self, presets, initial=None, title="Film Preset", prompt="",
                 own_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._presets = fp.normalise(presets)
        self._own = own_id
        ini = dict(initial or {})
        lay = QVBoxLayout(self)
        if prompt:
            p = QLabel(prompt)
            p.setWordWrap(True)
            lay.addWidget(p)
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.GAP_BTN)
        grid.addWidget(QLabel("Film"), 0, 0, Qt.AlignRight)
        self.film_combo = QComboBox()
        self.film_combo.setEditable(True)
        self.film_combo.setInsertPolicy(QComboBox.NoInsert)
        self.film_combo.addItems(fp.films(self._presets))
        self.film_combo.setEditText(str(ini.get("film") or ""))
        self.film_combo.lineEdit().setPlaceholderText("Film and speed, e.g. Portra400")
        self.film_combo.setMinimumWidth(220)
        grid.addWidget(self.film_combo, 0, 1)
        grid.addWidget(QLabel("Push / pull"), 1, 0, Qt.AlignRight)
        self.push = push_combo(ini.get("push", 0))
        grid.addWidget(self.push, 1, 1, Qt.AlignLeft)
        grid.addWidget(QLabel("Version"), 2, 0, Qt.AlignRight)
        self.version = QSpinBox()
        self.version.setRange(1, 99)
        self.version.setPrefix("V")
        self.version.setValue(max(1, int(ini.get("version") or 1)))
        grid.addWidget(self.version, 2, 1, Qt.AlignLeft)
        grid.addWidget(QLabel("Notes"), 3, 0, Qt.AlignRight)
        self.notes = QLineEdit(str(ini.get("notes") or ""))
        self.notes.setPlaceholderText("optional, e.g. Barcelona 2022, home kit")
        grid.addWidget(self.notes, 3, 1)
        lay.addLayout(grid)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        lay.addWidget(self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.film_combo.editTextChanged.connect(self._update)
        self.push.currentIndexChanged.connect(self._update)
        self.version.valueChanged.connect(self._update)
        self.notes.textChanged.connect(self._update)
        self._update()

    def values(self):
        return {"film": self.film_combo.currentText().strip(),
                "push": self.push.currentData() or 0,
                "version": self.version.value(),
                "notes": self.notes.text().strip()}

    def existing(self):
        """The preset this ID names, if there is one."""
        v = self.values()
        return fp.find_key(self._presets, v["film"], v["push"], v["version"])

    def _update(self, *_):
        v = self.values()
        ok = bool(v["film"])
        have = self.existing() if ok else None
        name = fp.label(v)
        if not ok:
            text = "Type the film's name."
        elif have is not None and have["id"] != self._own:
            text = (f"{name}: the existing preset “{fp.label(have)}”."
                    if self._own is None else
                    f"{fp.label(have, notes=False)} is already a preset: pick another "
                    "version.")
        else:
            text = f"{name}" + ("" if self._own else ": a new preset.")
        self.preview.setText(text)
        bad = not ok or (self._own is not None and have is not None and have["id"] != self._own)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not bad)
