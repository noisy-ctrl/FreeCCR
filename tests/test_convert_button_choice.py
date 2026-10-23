#!/usr/bin/env python3
"""The toolbar Convert asks Convert Current / Convert All / Cancel
(spec/convert-button-choice.md)."""

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


class _Preview:
    """Just enough of ImagePreview for convert_ccr."""
    def __init__(self, answer):
        self.current_idx = 0
        self.answer = answer
        self.panel = _Panel()
        self.by_ref = 0
        mw = type("MW", (), {"sliders_panel": self.panel})()
        self.parent = lambda: type("P", (), {"parent": lambda s: mw})()

    def _ask_convert_scope(self):
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
