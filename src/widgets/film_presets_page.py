"""Settings → Film Presets (spec/film-presets.md §6).

The film presets and what each carries: its ID (film, push/pull, version,
notes), its Film Look, the slider settings Write Preset put in it, and
the film bases Capture measured for it. Changes are saved as they are made.
Film bases go through the main window (`film_bases_data` /
`set_film_bases_data`) so an open Capture reloads them."""
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from core import base_exposure as be
from core import film_presets as fp
from ui import theme

SCAN_NAMES = {"trichrome": "RGB", "colour": "White light", "bw": "B&W"}


def _muted(text=""):
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.TEXT_MUTED};")
    return lab


class FilmPresetsPage(QWidget):

    def __init__(self, main_window=None, settings=None, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._st = settings or QSettings("FreeCCR", "FreeCCR")
        lay = QVBoxLayout(self)
        theme.apply_panel_spacing(lay, spacing=theme.GAP_SECTION)
        lay.addWidget(_muted(
            "A film preset is one film at one push or pull, in one version: "
            "“Portra400 V1”, “Colorplus200 +1 V2 (Barcelona 2022)”. It carries the film "
            "bases Capture measures for it, a Film Look, and the slider settings you "
            "write to it with Write Preset (beside Colour Profile). Pick it as the "
            "Film when you start Hot Folder Tether: the roll's first frame starts from it. "
            "Changes here are saved as you make them."))

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentRowChanged.connect(self._show)
        body.addWidget(self.list, 1)

        right = QVBoxLayout()
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.GAP_BTN)
        grid.addWidget(QLabel("Film"), 0, 0, Qt.AlignRight)
        self.film_edit = QLineEdit()
        self.film_edit.setPlaceholderText("Film and speed, e.g. Portra400")
        grid.addWidget(self.film_edit, 0, 1)
        grid.addWidget(QLabel("Push / pull"), 1, 0, Qt.AlignRight)
        from widgets.film_preset_id_dialog import push_combo
        self.push = push_combo(0)
        grid.addWidget(self.push, 1, 1, Qt.AlignLeft)
        grid.addWidget(QLabel("Version"), 2, 0, Qt.AlignRight)
        self.version = QSpinBox()
        self.version.setRange(1, 99)
        self.version.setPrefix("V")
        grid.addWidget(self.version, 2, 1, Qt.AlignLeft)
        grid.addWidget(QLabel("Notes"), 3, 0, Qt.AlignRight)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("optional, e.g. Barcelona 2022, home kit")
        grid.addWidget(self.notes, 3, 1)
        right.addLayout(grid)
        id_row = QHBoxLayout()
        self.id_note = _muted()
        id_row.addWidget(self.id_note, 1)
        self.save_id_btn = QPushButton("Save Name")
        theme.style_button(self.save_id_btn, "primary")
        id_row.addWidget(self.save_id_btn)
        right.addLayout(id_row)

        look_row = QHBoxLayout()
        look_row.addWidget(QLabel("Film Look"))
        self.look_combo = QComboBox()
        theme.shrinkable_combo(self.look_combo)
        look_row.addWidget(self.look_combo, 1)
        right.addLayout(look_row)

        # a flat field per scan type, pointing into the flat library
        # (spec/preset-flat-fields.md)
        right.addWidget(QLabel("Flat fields"))
        flat_grid = QGridLayout()
        flat_grid.setHorizontalSpacing(theme.GAP_BTN)
        self.flat_combos = {}
        for row, scan in enumerate(fp.FLAT_SCANS):
            flat_grid.addWidget(QLabel(SCAN_NAMES[scan]), row, 0, Qt.AlignRight)
            cb = QComboBox()
            theme.shrinkable_combo(cb)
            cb.setToolTip("The flat this preset's frames scanned this way are corrected "
                          "with. Automatic: the flat of their light and format. A flat "
                          "is for its own format: frames of another format (or whose "
                          "carrier opening has another shape) keep the automatic one. "
                          "Flats live in their own folder (Color Management → Field "
                          "correction → Show Folder); any number of presets can share one.")
            cb.currentIndexChanged.connect(lambda _i, sc=scan: self._flat_changed(sc))
            flat_grid.addWidget(cb, row, 1)
            self.flat_combos[scan] = cb
        flat_grid.setColumnStretch(1, 1)
        right.addLayout(flat_grid)

        set_row = QHBoxLayout()
        self.settings_note = _muted()
        set_row.addWidget(self.settings_note, 1)
        self.clear_btn = QPushButton("Clear Settings")
        self.clear_btn.setToolTip("Forget the slider settings and look this preset carries.")
        set_row.addWidget(self.clear_btn)
        right.addLayout(set_row)

        right.addWidget(QLabel("Film bases"))
        self.bases_note = _muted()
        right.addWidget(self.bases_note)
        right.addStretch(1)
        body.addLayout(right, 2)
        lay.addLayout(body, 1)

        btns = QHBoxLayout()
        self.new_btn = QPushButton("New…")
        self.version_btn = QPushButton("New Version")
        self.version_btn.setToolTip("A copy on the next free version (its settings and "
                                    "look; film bases are measured in Capture).")
        self.delete_btn = QPushButton("Delete…")
        theme.style_button(self.delete_btn, "danger")
        for b in (self.new_btn, self.version_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.delete_btn)
        lay.addLayout(btns)

        un_row = QHBoxLayout()
        self.unlinked_note = _muted()
        un_row.addWidget(self.unlinked_note, 1)
        self.adopt_btn = QPushButton("Make Presets")
        self.adopt_btn.setToolTip("Make a film preset for each film base that has none, "
                                  "named from it (a trailing +1, −1 or V2 is read as push "
                                  "and version).")
        un_row.addWidget(self.adopt_btn)
        lay.addLayout(un_row)

        self.film_edit.textChanged.connect(self._id_changed)
        self.push.currentIndexChanged.connect(self._id_changed)
        self.version.valueChanged.connect(self._id_changed)
        self.notes.textChanged.connect(self._id_changed)
        self.save_id_btn.clicked.connect(self.save_id)
        self.look_combo.currentIndexChanged.connect(self._look_changed)
        self.clear_btn.clicked.connect(self.clear_settings)
        self.new_btn.clicked.connect(self.new_preset)
        self.version_btn.clicked.connect(self.new_version)
        self.delete_btn.clicked.connect(self.delete_preset)
        self.adopt_btn.clicked.connect(self.make_presets)
        self._read_flat_library()
        self.refresh()

    # --- data ---------------------------------------------------------------
    def presets(self):
        return fp.load(self._st)

    def _save(self, presets):
        fp.save(self._st, presets)
        mw = self._mw
        if mw is not None and hasattr(mw, "push_preset_flats"):
            mw.push_preset_flats()

    def bases(self):
        mw = self._mw
        if mw is not None and hasattr(mw, "film_bases_data"):
            return mw.film_bases_data()
        return be.normalise(self._st.value("live/film_bases", ""))

    def _save_bases(self, d):
        mw = self._mw
        if mw is not None and hasattr(mw, "set_film_bases_data"):
            mw.set_film_bases_data(d)
        else:
            self._st.setValue("live/film_bases", be.dumps(d))

    def current(self):
        item = self.list.currentItem()
        return fp.find(self.presets(), item.data(Qt.UserRole)) if item else None

    # --- view ---------------------------------------------------------------
    def refresh(self, select=None):
        sel = select or (self.list.currentItem().data(Qt.UserRole)
                         if self.list.currentItem() else None)
        presets = fp.ordered(self.presets())
        bases = self.bases()
        self.list.blockSignals(True)
        self.list.clear()
        for p in presets:
            kinds = sorted({s for _i, b in be.bases_of(bases, p["id"]) for s in be.scans(b)},
                           key=list(SCAN_NAMES).index)
            text = fp.label(p)
            if kinds:
                text += "   · " + " · ".join(SCAN_NAMES[k] for k in kinds)
            it = QListWidgetItem(text, self.list)
            it.setData(Qt.UserRole, p["id"])
        self.list.blockSignals(False)
        row = next((i for i, p in enumerate(presets) if p["id"] == sel), 0 if presets else -1)
        self.list.setCurrentRow(row)
        self._show(row)
        un = be.unlinked(bases)
        if un:
            names = ", ".join(f"“{b['name']}”" for _i, b in un[:6])
            more = f" and {len(un) - 6} more" if len(un) > 6 else ""
            self.unlinked_note.setText(f"Film bases with no preset: {names}{more}.")
        self.unlinked_note.setVisible(bool(un))
        self.adopt_btn.setVisible(bool(un))

    def _fill_looks(self, current):
        from core.look_lut import colour_only_name, list_looks
        self.look_combo.blockSignals(True)
        self.look_combo.clear()
        self.look_combo.addItem("None", "")
        for name in list_looks():
            self.look_combo.addItem(fp.look_name(name), name)
            self.look_combo.addItem(fp.look_name(colour_only_name(name)),
                                    colour_only_name(name))
        if current:
            from core.look_lut import look_choice
            i = self.look_combo.findData(look_choice(current))
            if i < 0:
                self.look_combo.addItem(fp.look_name(current) + " (missing)", current)
                i = self.look_combo.count() - 1
            self.look_combo.setCurrentIndex(i)
        self.look_combo.blockSignals(False)

    def _read_flat_library(self):
        """Read the flat library once as the page opens (a flat shot
        meanwhile is listed)."""
        try:
            from core.ccr_backend import ccr_backend
            ccr_backend.refresh_field_library()
        except Exception as e:
            print(f"Flat library: {e}")

    def _fill_flats(self, p):
        from core import film_formats
        from core import flat_field as ff
        for scan, cb in self.flat_combos.items():
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("Automatic (by light and format)", "")
            cb.addItem("None", fp.NO_FLAT)
            for prof in sorted(ff.flats_for_scan(scan), key=lambda x: x.name.casefold()):
                ref = ff.flat_ref(prof)
                if not ref:
                    continue
                light, fmt = ff.profile_light(prof), ff.profile_format(prof)
                tag = ff.LIGHT_LABELS.get(light, "") if light else "any light"
                if fmt:
                    tag += " · " + film_formats.short_label(fmt)
                cb.addItem(f"{prof.name}  ({tag})", ref)
            want = fp.flat_of(p, scan) if p is not None else ""
            i = cb.findData(want)
            if i < 0:
                cb.addItem(f"{want} (missing: Automatic is used)", want)
                i = cb.count() - 1
            cb.setCurrentIndex(i)
            cb.setEnabled(p is not None)
            cb.blockSignals(False)

    def _flat_changed(self, scan):
        p = self.current()
        if p is None:
            return
        ref = self.flat_combos[scan].currentData() or ""
        if ref == fp.flat_of(p, scan):
            return
        presets, new = fp.set_flat(self.presets(), p["id"], scan, ref)
        if new is None:
            return
        self._save(presets)
        mw = self._mw
        if mw is not None and hasattr(mw, "_refresh_profile_mismatch"):
            mw._refresh_profile_mismatch()       # open frames of it flagged ⚠
        self.refresh(select=new["id"])

    def _show(self, _row=-1):
        p = self.current()
        on = p is not None
        for w in (self.film_edit, self.push, self.version, self.notes, self.look_combo,
                  self.clear_btn, self.version_btn, self.delete_btn):
            w.setEnabled(on)
        self._fill_flats(p)
        for w in (self.film_edit, self.push, self.version, self.notes):
            w.blockSignals(True)
        self.film_edit.setText(p["film"] if on else "")
        self.push.setCurrentIndex(max(0, self.push.findData(p["push"] if on else 0)))
        self.version.setValue(p["version"] if on else 1)
        self.notes.setText(p["notes"] if on else "")
        for w in (self.film_edit, self.push, self.version, self.notes):
            w.blockSignals(False)
        self._fill_looks(fp.look_of(p) if on else "")
        if not on:
            self.settings_note.setText("No film presets yet: New… makes one, and Measure "
                                       "in Capture makes one with its film base.")
            self.bases_note.setText("")
            self.id_note.setText("")
            self.save_id_btn.setEnabled(False)
            return
        n = fp.carried(p) - (1 if fp.look_of(p) else 0)
        self.settings_note.setText(
            f"Slider settings: {n} setting{'' if n == 1 else 's'}" if n > 0 else
            "Slider settings: none yet (Write Preset, beside Colour Profile).")
        self.clear_btn.setEnabled(fp.carried(p) > 0)
        lines = []
        for _i, b in be.bases_of(self.bases(), p["id"]):
            kinds = " / ".join(SCAN_NAMES[k] for k in sorted(
                be.scans(b), key=list(SCAN_NAMES).index))
            lines.append(f"{kinds}: {be.summary(b, be.scan_of(b) == 'trichrome')}")
        self.bases_note.setText("\n".join(lines) if lines else
                                "None yet: measure them in Capture with this preset's "
                                "film.")
        self._id_changed()

    def _id_changed(self, *_):
        p = self.current()
        if p is None:
            return
        v = self._id_values()
        same = (fp.id_key(v["film"], v["push"], v["version"]) == fp.key_of(p)
                and v["notes"] == p["notes"])
        if not v["film"]:
            self.id_note.setText("Type the film's name.")
            self.save_id_btn.setEnabled(False)
        elif same:
            self.id_note.setText(fp.label(p))
            self.save_id_btn.setEnabled(False)
        elif fp.taken(self.presets(), v["film"], v["push"], v["version"], but=p["id"]):
            self.id_note.setText(f"{fp.label(v, notes=False)} is another preset already.")
            self.save_id_btn.setEnabled(False)
        else:
            self.id_note.setText(f"Becomes {fp.label(v)}")
            self.save_id_btn.setEnabled(True)

    def _id_values(self):
        return {"film": self.film_edit.text().strip(), "push": self.push.currentData() or 0,
                "version": self.version.value(), "notes": self.notes.text().strip()}

    # --- actions --------------------------------------------------------------
    def save_id(self):
        p = self.current()
        if p is None:
            return False
        presets, new = fp.update(self.presets(), p["id"], **self._id_values())
        if new is None:
            return False
        self._save(presets)
        self._save_bases(be.relabel(self.bases(), new["id"], fp.label(new)))
        self.refresh(select=new["id"])
        return True

    def _look_changed(self, _i):
        p = self.current()
        if p is None:
            return
        presets, new = fp.set_look(self.presets(), p["id"], self.look_combo.currentData() or "")
        if new is not None:
            self._save(presets)
            self.refresh(select=new["id"])

    def _confirm(self, title, text):
        """The test seam."""
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def clear_settings(self):
        p = self.current()
        if p is None or not self._confirm("Clear Settings",
                                          f"Forget the settings and look “{fp.label(p)}” "
                                          "carries? Its film bases stay."):
            return
        presets, new = fp.update(self.presets(), p["id"], clip=None, chosen=[])
        self._save(presets)
        self.refresh(select=p["id"])

    def _ask_new(self):
        """The test seam: an ID dict, or None."""
        from widgets.film_preset_id_dialog import FilmPresetIdDialog
        from PySide6.QtWidgets import QDialog
        dlg = FilmPresetIdDialog(self.presets(), {}, title="New Film Preset", parent=self)
        return dlg.values() if dlg.exec() == QDialog.Accepted else None

    def new_preset(self):
        v = self._ask_new()
        if not v or not v.get("film"):
            return None
        presets, p = fp.ensure(self.presets(), v["film"], v["push"], v["version"],
                               v.get("notes", ""))
        self._save(presets)
        self.refresh(select=p["id"])
        return p

    def new_version(self):
        p = self.current()
        if p is None:
            return None
        presets, new = fp.new_version(self.presets(), p["id"])
        self._save(presets)
        self.refresh(select=new["id"] if new else None)
        return new

    def delete_preset(self):
        p = self.current()
        if p is None or not self._confirm(
                "Delete Film Preset",
                f"Delete “{fp.label(p)}”? Its film bases are kept, with no preset; frames "
                "graded with it keep their settings."):
            return False
        self._save(fp.delete(self.presets(), p["id"]))
        self._save_bases(be.unlink_preset(self.bases(), p["id"]))
        self.refresh()
        return True

    def make_presets(self):
        """A preset for each film base that has none, named from it."""
        presets = self.presets()
        d = self.bases()
        made = 0
        for i, b in be.unlinked(d):
            ident = fp.parse_label(b["name"])
            presets, p = fp.ensure(presets, ident["film"], ident["push"], ident["version"],
                                   ident["notes"])
            if be.base_for(d, p["id"], (be.scan_of(b) or "colour"))[0] is not None:
                # that preset already has a base of this kind: the next version
                presets, p = fp.ensure(presets, ident["film"], ident["push"],
                                       fp.next_version(presets, ident["film"], ident["push"]))
            d = be.link(d, i, p["id"], fp.label(p))
            d = be.relabel(d, p["id"], fp.label(p))
            made += 1
        self._save(presets)
        self._save_bases(d)
        self.refresh()
        return made
