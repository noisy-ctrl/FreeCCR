"""Edit a film base by hand (spec/film-base-exposure.md, revision 4).

One row per colour: shutter speed and ISO from the camera's own lists, the
LED level (1-255, as the light's sliders show it) and the base reading
estimated from the measured one. `FilmBaseEditDialog.action` is "save",
"new" or None after it closes; `values()` gives what to save.
"""
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from core import base_exposure as be
from ui import theme

ROW_NAMES = {"r": "Red", "g": "Green", "b": "Blue", "w": "One exposure"}
ORDER = ("r", "g", "b", "w")


def speed_options(camera, current):
    """The camera's speeds that have a number, plus `current` (in speed
    order) when the camera doesn't list it."""
    vals = [str(v) for v in (camera or []) if be.seconds(v) is not None]
    cur = str(current)
    if cur not in vals and be.seconds(cur) is not None:
        t = be.seconds(cur)
        at = next((i for i, v in enumerate(vals) if be.seconds(v) > t), len(vals))
        # the camera's list may run either way: keep it sorted as it came
        if len(vals) >= 2 and be.seconds(vals[0]) > be.seconds(vals[-1]):
            at = next((i for i, v in enumerate(vals) if be.seconds(v) < t), len(vals))
        vals.insert(at, cur)
    return vals


def iso_options(camera, current):
    """The camera's ISO values (no Auto modes), plus `current` if missing."""
    vals = [str(v) for v in (camera or []) if be.iso_number(v) is not None]
    cur = str(current)
    if cur not in vals and be.iso_number(cur) is not None:
        n = be.iso_number(cur)
        at = next((i for i, v in enumerate(vals) if be.iso_number(v) > n), len(vals))
        vals.insert(at, cur)
    return vals


def base_text(est, changed):
    """(text, warn) for the Base column."""
    if est is None:
        return "?", False
    pct = f"{100 * est:.0f} %"
    if not changed:
        return pct, est >= be.CLIP_TRUST
    if est >= be.CLIP_TRUST:
        return f"≈ {pct} · may clip", True
    if 0 < est < be.TARGET / 4:
        under = math.log2(be.TARGET / est)
        return f"≈ {pct} · {under:.1f} stops under", True
    return f"≈ {pct}", False


class FilmBaseEditDialog(QDialog):
    def __init__(self, base, shutters=(), isos=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Film Base")
        self._base = base
        self.action = None
        self.rows = {}
        lay = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit(base["name"])
        self.name_edit.setMinimumWidth(260)
        name_row.addWidget(self.name_edit, 1)
        lay.addLayout(name_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.GAP_BTN)
        grid.setVerticalSpacing(theme.GAP_ROW)
        for c, head in enumerate(("Shutter", "ISO", "LED", "Base"), start=1):
            h = QLabel(head)
            h.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            grid.addWidget(h, 0, c)
        cols = base.get("colours") or {}
        row = 1
        for k in ORDER:
            col = cols.get(k)
            if not col:
                continue
            grid.addWidget(QLabel(ROW_NAMES[k]), row, 0, Qt.AlignRight | Qt.AlignVCenter)
            sh = QComboBox()
            for v in speed_options(shutters, col["shutter"]):
                sh.addItem(v, v)
            sh.setCurrentIndex(max(0, sh.findData(col["shutter"])))
            iso = QComboBox()
            for v in iso_options(isos, col["iso"]):
                iso.addItem(f"ISO {v}", v)
            iso.setCurrentIndex(max(0, iso.findData(col["iso"])))
            led = QSpinBox()
            led.setRange(1, 255)
            lvl = be.led_level(col)
            if lvl is None:
                led.setSpecialValueText("as it is")
                led.setRange(1, 1)
                led.setEnabled(False)
                led.setToolTip("Measured with the light as it was: no level to set.")
            else:
                led.setValue(lvl)
                led.setToolTip("The LED level, 1-255 (the brightest lit channel).")
            est = QLabel()
            est.setMinimumWidth(150)
            for c, w in enumerate((sh, iso, led, est), start=1):
                grid.addWidget(w, row, c)
            sh.currentIndexChanged.connect(self._update)
            iso.currentIndexChanged.connect(self._update)
            led.valueChanged.connect(self._update)
            self.rows[k] = {"shutter": sh, "iso": iso, "led": led, "base": est,
                            "level": lvl}
            row += 1
        lay.addLayout(grid)

        note = QLabel("Base is estimated from the measured reading: twice the time, "
                      "ISO or LED level gives twice the level. Measure Again reads it "
                      "for real.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {theme.FS_CAPTION}px;")
        lay.addWidget(note)

        btns = QHBoxLayout()
        theme.apply_button_row(btns)
        btns.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.new_btn = QPushButton("Save as New")
        self.new_btn.setToolTip("Keep this film base as it is and add the edit as a new one.")
        self.save_btn = QPushButton("Save")
        self.save_btn.setToolTip("Overwrite this film base.")
        theme.style_button(self.save_btn, "primary")
        self.save_btn.setDefault(True)
        for b in (self.cancel_btn, self.new_btn, self.save_btn):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.cancel_btn.clicked.connect(self.reject)
        self.new_btn.clicked.connect(lambda: self._finish("new"))
        self.save_btn.clicked.connect(lambda: self._finish("save"))
        self._update()

    def _finish(self, action):
        self.action = action
        self.accept()

    def values(self):
        """(name, {k: {"shutter", "iso", "level"}})."""
        out = {}
        for k, r in self.rows.items():
            out[k] = {"shutter": r["shutter"].currentData(), "iso": r["iso"].currentData(),
                      "level": r["led"].value() if r["level"] is not None else None}
        return self.name_edit.text().strip(), out

    def _update(self, *_):
        cols = self._base.get("colours") or {}
        _name, vals = self.values()
        for k, r in self.rows.items():
            col, v = cols[k], vals[k]
            changed = (v["shutter"] != col["shutter"] or v["iso"] != col["iso"]
                       or (v["level"] is not None and v["level"] != r["level"]))
            est = be.estimate_base(col, v["shutter"], v["iso"],
                                   v["level"] if r["level"] is not None else None)
            if not changed and not col.get("edited"):
                est = col["base"]
            text, warn = base_text(est, changed or bool(col.get("edited")))
            r["base"].setText(text)
            r["base"].setStyleSheet(f"color: {theme.WARN_TEXT if warn else theme.TEXT};")
