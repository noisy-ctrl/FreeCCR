"""The main window's Write Preset and Apply Preset windows
(spec/film-presets.md §5)."""
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from core import film_presets as fp
from ui import theme

NEW = "__new__"


def _rename_button(dlg, old, new):
    for b in dlg.findChildren(QPushButton):
        if b.text() == old:
            b.setText(new)
            return b
    return None


def preset_combo(presets, current=None, with_new=False):
    c = QComboBox()
    for p in fp.ordered(presets):
        c.addItem(fp.label(p), p["id"])
    if with_new:
        c.addItem("New Preset…", NEW)
    i = c.findData(current) if current else -1
    c.setCurrentIndex(max(0, i))
    theme.shrinkable_combo(c)
    return c


def make_write_dialog(parent, options, unticked, presets, current, new_fn):
    """The Paste window's list of this frame's settings, with which preset to
    write them to above it. `new_fn()` asks for a new preset's ID and
    returns the stored preset, or None. After exec: `dlg.preset_id()`."""
    from widgets.sliders_panel import PasteSettingsDialog
    dlg = PasteSettingsDialog(parent, options, unticked, 1)
    dlg.setWindowTitle("Write to Film Preset")
    lay = dlg.layout()
    first = lay.itemAt(0).widget()
    if isinstance(first, QLabel):
        first.setText("Write these settings of this frame to the film preset:")
    row = QHBoxLayout()
    row.addWidget(QLabel("Preset"))
    dlg.preset_combo = preset_combo(presets, current, with_new=True)
    row.addWidget(dlg.preset_combo, 1)
    lay.insertLayout(0, row)
    dlg.preset_note = QLabel()
    dlg.preset_note.setWordWrap(True)
    dlg.preset_note.setStyleSheet(f"color: {theme.TEXT_MUTED};")
    lay.insertWidget(1, dlg.preset_note)
    write_btn = _rename_button(dlg, "Paste", "Write")
    state = {"presets": list(presets), "last": dlg.preset_combo.currentIndex()}

    def note():
        p = fp.find(state["presets"], dlg.preset_combo.currentData())
        if p is None:
            dlg.preset_note.setText("Choose a preset, or make a new one.")
        elif fp.carried(p):
            dlg.preset_note.setText(f"Replaces what it carries now ({fp.summary(p)}).")
        else:
            dlg.preset_note.setText("It carries no settings yet.")
        if write_btn is not None:
            write_btn.setEnabled(p is not None)

    def changed(i):
        if dlg.preset_combo.itemData(i) == NEW:
            p = new_fn()
            dlg.preset_combo.blockSignals(True)
            if p is None:
                dlg.preset_combo.setCurrentIndex(state["last"])
            else:
                state["presets"].append(p)
                dlg.preset_combo.insertItem(0, fp.label(p), p["id"])
                dlg.preset_combo.setCurrentIndex(0)
            dlg.preset_combo.blockSignals(False)
        state["last"] = dlg.preset_combo.currentIndex()
        note()

    dlg.preset_combo.currentIndexChanged.connect(changed)
    dlg.preset_id = lambda: (dlg.preset_combo.currentData()
                             if dlg.preset_combo.currentData() != NEW else None)
    note()
    return dlg


class ChoosePresetDialog(QDialog):
    """Pick one film preset (Apply Preset)."""

    def __init__(self, presets, current=None, title="Apply Film Preset", prompt="",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        if prompt:
            lab = QLabel(prompt)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        self.combo = preset_combo(presets, current)
        self._presets = presets
        lay.addWidget(self.combo)
        self.note = QLabel()
        self.note.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.combo.currentIndexChanged.connect(self._note)
        self._note()

    def _note(self, *_):
        self.note.setText(fp.summary(fp.find(self._presets, self.combo.currentData())))

    def preset_id(self):
        return self.combo.currentData()
