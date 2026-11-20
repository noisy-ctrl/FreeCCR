"""Settings → Field correction → Camera and Lens…: name the camera and lens
every flat in the library was shot with (spec/flat-field-info.md §3)."""
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from core import flat_field as ff


def _from_metadata(p):
    """The flat as its metadata alone names it: (camera, lens)."""
    bare = ff.FieldProfile(p.name, p.gain, p.aspect,
                           meta={k: v for k, v in (p.meta or {}).items() if k != "details"})
    return ff.camera_name(bare), ff.lens_name(bare)


class FlatDetailsDialog(QDialog):
    def __init__(self, profiles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Camera and Lens")
        self.profiles = list(profiles)
        lay = QVBoxLayout(self)
        text = QLabel(
            "What each flat field was shot with. The shots' own metadata fills in what "
            "it knows (shown greyed); type a name where it knows nothing, as with an "
            "adapted lens, or where you'd rather call it something else. Naming them "
            "never changes which flat a frame gets.")
        text.setWordWrap(True)
        text.setMinimumWidth(560)
        lay.addWidget(text)
        self.table = QTableWidget(len(self.profiles), 3)
        self.table.setHorizontalHeaderLabels(["Flat", "Camera", "Lens"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        self.camera_edits, self.lens_edits = [], []
        for row, p in enumerate(self.profiles):
            item = QTableWidgetItem(ff.flat_label(p) or p.name)
            item.setToolTip(p.name + (f"\n{p.path}" if p.path else ""))
            self.table.setItem(row, 0, item)
            typed = ff.typed_details(p)
            meta_cam, meta_lens = _from_metadata(p)
            cam = QLineEdit(typed["camera"])
            cam.setPlaceholderText(meta_cam or "Not in the metadata")
            lens = QLineEdit(typed["lens"])
            lens.setPlaceholderText(meta_lens or "Not in the metadata")
            for col, w in ((1, cam), (2, lens)):
                w.setFrame(False)
                self.table.setCellWidget(row, col, w)
            self.camera_edits.append(cam)
            self.lens_edits.append(lens)
        if self.profiles:
            self.table.selectRow(0)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.same_btn = QPushButton("Same for All")
        self.same_btn.setToolTip("Give every flat the camera and lens typed in the "
                                 "selected row.")
        self.same_btn.clicked.connect(self.same_for_all)
        self.same_btn.setEnabled(len(self.profiles) > 1)
        row.addWidget(self.same_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.resize(760, min(520, 170 + 34 * max(1, len(self.profiles))))

    def _row(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else 0

    def same_for_all(self):
        if not self.profiles:
            return
        r = self._row()
        cam, lens = self.camera_edits[r].text(), self.lens_edits[r].text()
        for c, l in zip(self.camera_edits, self.lens_edits):
            c.setText(cam)
            l.setText(lens)

    def values(self):
        """[(profile, camera, lens)] as typed."""
        return [(p, c.text().strip(), l.text().strip())
                for p, c, l in zip(self.profiles, self.camera_edits, self.lens_edits)]

    def save(self):
        """Write the flats whose names changed; how many. The last names
        typed are remembered for the next flat."""
        changed = 0
        last = None
        for p, cam, lens in self.values():
            if ff.set_details(p, cam, lens):
                changed += 1
            if cam or lens:
                last = (cam, lens)
        if last is not None:
            ff.remember_details(*last)
        return changed
