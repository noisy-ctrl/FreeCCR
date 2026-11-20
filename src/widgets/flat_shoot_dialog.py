"""Capture → Shoot Flat Fields…: which format and which lights
(spec/ir-dust-and-flat-sets.md §5)."""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout)

from core import film_formats
from core import flat_capture as fc

LIGHT_TEXTS = (("white", "White light", "One shot under the white LED."),
               ("rgb", "RGB (trichrome)",
                "Three shots, red, green and blue, merged like a trichrome scan: "
                "each channel's flat comes from its own LED."),
               ("ir", "Infrared",
                "One shot under the IR LED, for the dust search: it takes out the "
                "lens' IR hotspot. Long (up to 30 s)."))


class FlatShootDialog(QDialog):
    def __init__(self, default_format="", lights=("white", "rgb"), parent=None,
                 camera="", lens=""):
        super().__init__(parent)
        self.setWindowTitle("Shoot Flat Fields")
        lay = QVBoxLayout(self)
        text = QLabel(
            "Take the film out and put the empty carrier in, at the height you scan "
            "this format at. Each light is lit alone and shot at its own exposure; "
            "the test shots aren't kept.\n\nThe flats go into the field-correction "
            "library, replacing that light and format's flats shot here before, and "
            "frames of that light and format use them, moved with the carrier's "
            "opening. A flat is for its format only: a carrier stands one format up "
            "and another lying down, and a wider one needs more of the light.")
        text.setWordWrap(True)
        text.setMinimumWidth(420)
        lay.addWidget(text)
        r = QHBoxLayout()
        r.addWidget(QLabel("Format"))
        self.format_combo = QComboBox()
        for key, label, _ratio, _cols in film_formats.FILM_FORMATS:
            self.format_combo.addItem(label, key)
        self.format_combo.addItem("Any format (no format of its own)", "")
        i = self.format_combo.findData(default_format or "")
        self.format_combo.setCurrentIndex(i if i >= 0 else self.format_combo.count() - 1)
        self.format_combo.setToolTip(
            "The carrier's format. Frames of this format use these flats; a flat of "
            "Any format is used only where a frame's format has none and its carrier "
            "opening has the same shape.")
        r.addWidget(self.format_combo, 1)
        lay.addLayout(r)
        # what the flats were shot with (spec/flat-field-info.md §3): the
        # shots' metadata has the camera; an adapted lens records nothing
        form = QFormLayout()
        self.camera_edit = QLineEdit(camera or "")
        self.camera_edit.setPlaceholderText("From the shots' metadata")
        self.lens_edit = QLineEdit(lens or "")
        self.lens_edit.setPlaceholderText("None in the metadata? Type it")
        for w in (self.camera_edit, self.lens_edit):
            w.setToolTip("Kept with the flats, so you can tell later what they were "
                         "shot with. Typed names win over the shots' metadata.")
        form.addRow("Camera", self.camera_edit)
        form.addRow("Lens", self.lens_edit)
        lay.addLayout(form)
        self.light_boxes = {}
        for key, label, tip in LIGHT_TEXTS:
            cb = QCheckBox(label)
            cb.setToolTip(tip)
            cb.setChecked(key in (lights or ()))
            cb.toggled.connect(self._refresh)
            lay.addWidget(cb)
            self.light_boxes[key] = cb
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Shoot")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._refresh()
        self.adjustSize()

    def _refresh(self, *_a):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.lights()))

    def lights(self):
        return [k for k in fc.LIGHT_ORDER if self.light_boxes[k].isChecked()]

    def values(self):
        return {"format": self.format_combo.currentData() or None, "lights": self.lights(),
                "camera": self.camera_edit.text().strip(),
                "lens": self.lens_edit.text().strip()}
