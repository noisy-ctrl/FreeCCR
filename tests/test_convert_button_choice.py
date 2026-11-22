#!/usr/bin/env python3
"""The toolbar Convert asks Convert Current / Convert Selected (N) / Convert
All / Cancel (spec/convert-button-choice.md, revision 2026-11-23: Selected
as Auto Crop's)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.ccr_backend import ccr_backend  # noqa: E402
from widgets.image_preview import ImagePreview  # noqa: E402


class _Panel:
    def __init__(self):
        self.calls = []

    def _on_convert_current_bwpoint(self):
        self.calls.append("current")

    def _on_convert_all_bwpoint(self, confirmed=False):
        self.calls.append(("all", confirmed))

    def _on_convert_selected(self, indices):
        self.calls.append(("selected", list(indices)))


class _Preview:
    """Just enough of ImagePreview for convert_ccr."""
    def __init__(self, answer, selected=()):
        self.current_idx = 0
        self.answer = answer
        self.panel = _Panel()
        self.by_ref = 0
        self.asked = []
        tl = type("TL", (), {"selected_indices": lambda s: list(selected)})()
        mw = type("MW", (), {"sliders_panel": self.panel, "thumbnail_list": tl})()
        self.parent = lambda: type("P", (), {"parent": lambda s: mw})()

    def _ask_convert_scope(self, n_selected=0):
        self.asked.append(n_selected)
        return self.answer

    def _convert_current_by_reference(self):
        self.by_ref += 1


@pytest.fixture
def ref(monkeypatch):
    state = {"ref": None}
    monkeypatch.setattr(ccr_backend, "get_reference_frame_by_index",
                        lambda i: state["ref"])
    return state


def test_cancel_converts_nothing(ref):
    p = _Preview(None)
    ImagePreview.convert_ccr(p)
    assert p.panel.calls == [] and p.by_ref == 0


def test_convert_all_is_the_panels_without_asking_again(ref):
    p = _Preview("all")
    ImagePreview.convert_ccr(p)
    assert p.panel.calls == [("all", True)] and p.by_ref == 0


def test_convert_current_uses_a_drawn_reference_frame(ref):
    ref["ref"] = (10, 10, 100, 80)
    p = _Preview("current")
    ImagePreview.convert_ccr(p)
    assert p.by_ref == 1 and p.panel.calls == []


def test_convert_current_without_one_is_the_panels(ref):
    p = _Preview("current")
    ImagePreview.convert_ccr(p)
    assert p.panel.calls == ["current"] and p.by_ref == 0


def test_convert_selected_hands_the_panel_the_selection(ref):
    p = _Preview("selected", selected=[2, 0, 3])
    ImagePreview.convert_ccr(p)
    assert p.asked == [3]
    assert p.panel.calls == [("selected", [2, 0, 3])] and p.by_ref == 0


def test_with_several_selected_the_dialogue_offers_and_defaults_to_selected(monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget
    seen = {}

    def fake_exec(box):
        seen["buttons"] = [b.text() for b in box.buttons()]
        seen["default"] = box.defaultButton().text()
        {b.text(): b for b in box.buttons()}["Convert Selected (3)"].click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert ImagePreview._ask_convert_scope(QWidget(), 3) == "selected"
    assert [t for t in seen["buttons"] if "Cancel" not in t] == [
        "Convert Current", "Convert Selected (3)", "Convert All"]
    assert seen["default"] == "Convert Selected (3)"


def test_convert_frames_converts_only_those_frames(tmp_path, monkeypatch):
    """Convert Selected converts each chosen frame as Convert Current does and
    never touches the others."""
    import cv2
    import numpy as np
    from core.ccr_image import CCRImage
    base = np.array([0.30, 0.50, 0.75])
    imgs = []
    for k in range(3):
        neg = np.zeros((80, 120, 3), np.float32)
        neg[...] = base
        neg[10:70, 15:105] = base * (0.5 + 0.1 * k)
        p = str(tmp_path / f"n{k}.tif")
        cv2.imwrite(p, (neg * 65535).astype(np.uint16))
        imgs.append(CCRImage(p))
    monkeypatch.setattr(ccr_backend, "images", imgs)
    monkeypatch.setattr(ccr_backend, "black_point_bgr", tuple(base * 65535))
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None)
    steps = []
    summ = ccr_backend.convert_frames([imgs[0], imgs[2]],
                                      progress_callback=lambda i, n: steps.append((i, n)))
    assert imgs[0].converted and imgs[2].converted and not imgs[1].converted
    assert imgs[0].conversion_inputs["mode"] == "bw"
    assert steps[0] == (0, 2) and steps[-1] == (2, 2)
    assert summ["skipped"] == 0 and ccr_backend.last_auto_bp_summary is summ


def test_the_dialogue_has_the_three_choices(monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget
    seen = {}

    def fake_exec(box):
        seen["buttons"] = [b.text() for b in box.buttons()]
        seen["default"] = box.defaultButton().text()
        box_buttons = {b.text(): b for b in box.buttons()}
        box_buttons["Convert All"].click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    host = QWidget()
    answer = ImagePreview._ask_convert_scope(host)
    assert answer == "all"
    assert {"Convert Current", "Convert All"} <= set(seen["buttons"])
    assert any("Cancel" in t for t in seen["buttons"])
    assert seen["default"] == "Convert Current"


def test_the_panels_convert_all_still_asks(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from widgets.sliders_panel import SlidersPanel
    asked = []
    monkeypatch.setattr(ccr_backend, "auto_black_point", True, raising=False)
    monkeypatch.setattr(ccr_backend, "images", [object()])
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[1]) or QMessageBox.No)
    panel = SlidersPanel()
    panel._on_convert_all_bwpoint()
    assert asked == ["Convert All Images"]


def test_the_panels_convert_selected_runs_the_chosen_frames(monkeypatch):
    from widgets.sliders_panel import SlidersPanel
    frames = [object(), object(), object()]
    got = []
    monkeypatch.setattr(ccr_backend, "auto_black_point", True, raising=False)
    monkeypatch.setattr(ccr_backend, "images", frames)
    monkeypatch.setattr(ccr_backend, "convert_frames",
                        lambda imgs, progress_callback=None: got.append(list(imgs)))
    panel = SlidersPanel()
    panel._convert_threaded = False
    panel._on_convert_selected([2, 0, 2, 9])
    assert got == [[frames[0], frames[2]]]
