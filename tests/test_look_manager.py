#!/usr/bin/env python3
"""Film Look lists → Manage… (spec/look-manager.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import look_lut as ll  # noqa: E402

_KEEP = []


def _table(n=3):
    g = np.linspace(0, 1, n)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r, gg, b], axis=-1)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    d = tmp_path / "looks"
    d.mkdir()
    monkeypatch.setattr(ll, "looks_dir", lambda: str(d))
    ll.write_cube(str(d / "Plain.cube"), _table())
    ll.write_cube(str(d / "Lab.cube"), _table(),
                  balance=(np.array([-0.05, -0.02, 0.07]), 0.55))
    return d


def test_delete_look_trashes_or_removes(lib):
    trashed = []

    def trash(p):
        trashed.append(p)
        os.remove(p)                          # what a Trash does to the folder
        return True
    assert ll.delete_look("Plain.cube|colour|bal", trash=trash)
    assert trashed == [str(lib / "Plain.cube")] and not (lib / "Plain.cube").exists()
    assert ll.delete_look("Lab.cube", trash=lambda p: False)   # no Trash: deleted
    assert not (lib / "Lab.cube").exists()
    assert not ll.delete_look("Nope.cube")


def test_window_lists_imports_deletes_and_opens_the_folder(lib, tmp_path):
    from widgets.look_manager import LookManager
    asked, opened = [], []
    ext = tmp_path / "New Look.cube"
    ll.write_cube(str(ext), _table())
    bad = tmp_path / "bad.cube"
    bad.write_text("nonsense")
    d = LookManager(in_use=lambda n: 2 if n == "Lab.cube" else 0,
                    confirm=lambda names, used: asked.append((names, used)) or True,
                    opener=opened.append, chooser=lambda: [str(ext)])
    _KEEP.append(d)
    texts = [d.list.item(i).text() for i in range(d.list.count())]
    assert texts == ["Lab   ·   balance data, used by 2 open frames", "Plain"]
    assert d.folder_label.text() == str(lib)
    assert not d.delete_btn.isEnabled()
    d.show_folder()
    assert opened == [str(lib)]
    d.import_btn.click()
    assert (lib / "New Look.cube").exists() and d.changed
    assert d.list.count() == 3
    d.list.item(0).setSelected(True)                      # Lab
    assert d.delete_btn.isEnabled()
    d.delete_btn.click()
    assert asked == [(["Lab.cube"], 2)]
    assert not (lib / "Lab.cube").exists()
    assert [d.list.item(i).data(0x0100) for i in range(d.list.count())] == \
        ["New Look.cube", "Plain.cube"]


def test_no_means_nothing_deleted(lib):
    from widgets.look_manager import LookManager
    d = LookManager(confirm=lambda names, used: False)
    _KEEP.append(d)
    d.list.item(0).setSelected(True)
    d.delete_btn.click()
    assert (lib / "Lab.cube").exists() and not d.changed


def test_panel_lists_offer_manage_and_rebuild_after(lib, monkeypatch):
    from widgets.sliders_panel import SlidersPanel
    from core.ccr_backend import ccr_backend
    p = SlidersPanel()
    _KEEP.append(p)
    for combo in p._look_combos:
        assert combo.itemText(combo.count() - 1) == "Manage…"
        assert combo.itemText(combo.count() - 2) == "Import…"

    class Img:
        active_area_id = None
        adjustment_settings = {"look_lut": "Lab.cube|bal"}
        renders = 0

        def update_thumbnail_and_preview(self):
            Img.renders += 1
    img = Img()
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: img)
    monkeypatch.setattr(ccr_backend, "images", [img, Img()])
    monkeypatch.setattr(p, "_update_thumb", lambda: None)
    monkeypatch.setattr(p, "_show_rendered", lambda: None)
    p.current_idx = 0
    assert p.look_users("Lab.cube|colour") == 2

    class FakeDlg:
        changed = True

        def exec(self):
            ll.delete_look("Lab.cube", trash=lambda path: False)
    monkeypatch.setattr(p, "_make_look_manager", lambda: FakeDlg())
    combo = p.look_combo_top
    p._on_look_activated(combo.findData(p.LOOK_MANAGE), combo)
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "Lab (missing)" in texts and "Plain" in texts
    assert combo.currentText() == "Lab (missing)"         # the frame still says so
    assert img.adjustment_settings["look_lut"] == "Lab.cube|bal"
    assert Img.renders == 1                               # re-rendered without it
    assert not p.look_balance_top.isEnabled()
