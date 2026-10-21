#!/usr/bin/env python3
"""Rotate / flip several selected frames at once, and the lit mirror icons.
See spec/multi-frame-orientation.md."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtGui import QColor, QIcon, QPixmap  # noqa: E402
from PySide6.QtWidgets import (QApplication, QGraphicsPixmapItem,  # noqa: E402
                               QMessageBox, QVBoxLayout, QWidget)

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from ui import theme  # noqa: E402
from widgets import image_preview as ipm  # noqa: E402
from widgets.image_preview import (ImagePreview, flipped_count,  # noqa: E402
                                   next_orientation, orientation_prompt,
                                   resource_path)


# --- fakes -------------------------------------------------------------------

class _Frame:
    def __init__(self, rot=0, h=False, v=False):
        self.rotation_angle, self.horizontal_mirrored, self.vertical_mirrored = rot, h, v
        self.undo_pushes = 0
        self.converted = False
        self.fine_rotation_angle = 0

    def push_undo_state(self):
        self.undo_pushes += 1

    @property
    def triple(self):
        return (self.rotation_angle, self.horizontal_mirrored, self.vertical_mirrored)


class _Thumbs:
    def __init__(self):
        self.selected = []
        self.updated = []

    def selected_indices(self):
        return sorted(self.selected)

    def update_thumbnail(self, idx):
        self.updated.append(idx)


class _Hist:
    def set_caption(self, text):
        pass


class _Sliders:
    def __init__(self):
        self.histogram = _Hist()
        self.hints = []

    def set_histogram(self, data):
        pass

    def set_sliders_enabled(self, *a):
        pass

    def set_temporary_hint(self, text, duration=0):
        self.hints.append(text)


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.sliders_panel = _Sliders()
        self.thumbnail_list = _Thumbs()
        self.mid = QWidget(self)


_HOSTS = []


@pytest.fixture
def frames(monkeypatch):
    imgs = [_Frame() for _ in range(5)]
    monkeypatch.setattr(ccr_backend, "images", imgs)
    saves = []
    monkeypatch.setattr(ccr_backend, "save_catalog", lambda *a, **k: saves.append(1))
    return imgs, saves


def _preview(current=0):
    host = _Host()
    _HOSTS.append(host)
    QVBoxLayout(host).addWidget(host.mid)
    ip = ImagePreview(host.mid)
    QVBoxLayout(host.mid).addWidget(ip)
    host.resize(700, 600)
    host.show()
    _app.processEvents()
    pm = QPixmap(120, 80)
    pm.fill(QColor(90, 90, 90))
    ip.current_pixmap = pm
    ip.pixmap_item = QGraphicsPixmapItem(pm)
    ip.scene.addItem(ip.pixmap_item)
    ip.current_idx = current
    ip._fit_view_to_content()
    return ip, host


def _answer(ip, monkeypatch, mode, seen=None):
    def ask(prompt):
        if seen is not None:
            seen.append(prompt)
        return mode
    monkeypatch.setattr(ip, "_ask_orientation_all", ask)


def _never_ask(ip, monkeypatch):
    def ask(prompt):
        raise AssertionError("no prompt expected")
    monkeypatch.setattr(ip, "_ask_orientation_all", ask)


# --- 1. the pure step --------------------------------------------------------

def test_next_orientation_rotations_wrap():
    assert next_orientation("left", 0, False, False) == (270, False, False)
    assert next_orientation("left", 90, True, False) == (0, True, False)
    assert next_orientation("right", 270, False, True) == (0, False, True)
    assert next_orientation("right", 0, False, False) == (90, False, False)


def test_next_orientation_flips_touch_only_their_own_flag():
    assert next_orientation("hflip", 90, False, True) == (90, True, True)
    assert next_orientation("hflip", 90, True, True) == (90, False, True)
    assert next_orientation("vflip", 180, True, False) == (180, True, True)
    assert next_orientation("vflip", 180, True, True) == (180, True, False)


def test_next_orientation_rest_mode_sets_rather_than_toggles():
    assert next_orientation("hflip", 0, True, False, mode="rest") == (0, True, False)
    assert next_orientation("hflip", 0, False, False, mode="rest") == (0, True, False)
    assert next_orientation("vflip", 0, False, True, mode="rest") == (0, False, True)


def test_next_orientation_rejects_unknown_kind():
    with pytest.raises(ValueError):
        next_orientation("spin", 0, False, False)


# --- the prompt's wording ----------------------------------------------------

def test_prompt_is_yes_no_for_rotations_and_uniform_flips():
    fs = [_Frame(h=True), _Frame(h=True), _Frame(h=True)]
    for kind, text in (("left", "Rotate all 3 selected frames left?"),
                       ("right", "Rotate all 3 selected frames right?"),
                       ("hflip", "Flip all 3 selected frames horizontally?"),
                       ("vflip", "Flip all 3 selected frames vertically?")):
        p = orientation_prompt(kind, fs)
        assert p["text"] == text
        assert p["choices"] == [("each", "Yes")] and p["cancel"] == "No"
        assert p["note"] == ""


def test_prompt_offers_flip_the_other_n_for_a_mixed_selection():
    fs = [_Frame(h=True), _Frame(), _Frame(h=True), _Frame(), _Frame()]
    assert flipped_count("hflip", fs) == 2 and flipped_count("vflip", fs) == 0
    p = orientation_prompt("hflip", fs)
    assert p["title"] == "Flip Horizontal"
    assert p["choices"] == [("each", "Flip Each"), ("rest", "Flip the Other 3")]
    assert p["cancel"] == "Cancel"
    assert "2 of them are already flipped" in p["note"]
    assert "those 2 back to normal" in p["note"] and "all 5 end up flipped" in p["note"]
    # A vertical flip of the same frames is uniform (none flipped): Yes / No.
    assert orientation_prompt("vflip", fs)["choices"] == [("each", "Yes")]


def test_prompt_note_singular():
    p = orientation_prompt("vflip", [_Frame(v=True), _Frame(), _Frame()])
    assert p["note"].startswith("1 of them is already flipped this way.")
    assert "Flip the Other 2 leaves it as it is" in p["note"]


def test_ask_box_returns_the_chosen_mode(monkeypatch):
    ip, _h = _preview()
    prompt = orientation_prompt("hflip", [_Frame(h=True), _Frame()])
    for label, want in (("Flip Each", "each"), ("Flip the Other 1", "rest"), ("Cancel", None)):
        def fake_exec(box, label=label):
            seen = {b.text(): b for b in box.buttons()}
            assert set(seen) == {"Flip Each", "Flip the Other 1", "Cancel"}
            assert box.defaultButton() is seen["Flip Each"]
            assert box.escapeButton() is seen["Cancel"]
            seen[label].click()
            return 0
        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        assert ip._ask_orientation_all(prompt) == want


# --- 2. one frame: as before -------------------------------------------------

def test_single_frame_changes_without_a_prompt(frames, monkeypatch):
    imgs, saves = frames
    ip, host = _preview(current=1)
    host.thumbnail_list.selected = [1]
    _never_ask(ip, monkeypatch)
    ip.rotate_right()
    assert imgs[1].triple == (90, False, False) and imgs[1].undo_pushes == 1
    assert ip.current_rotation == 90
    ip.mirror_horizontal()
    assert imgs[1].triple == (90, True, False) and ip.current_horizontal_flip
    assert all(f.triple == (0, False, False) for i, f in enumerate(imgs) if i != 1)
    assert saves == [] and host.sliders_panel.hints == []


def test_no_selection_falls_back_to_the_frame_on_screen(frames, monkeypatch):
    imgs, _ = frames
    ip, host = _preview(current=2)
    host.thumbnail_list.selected = []
    _never_ask(ip, monkeypatch)
    ip.mirror_vertical()
    assert imgs[2].triple == (0, False, True)


# --- 3 / 4. several frames: Yes and No ---------------------------------------

def test_yes_changes_every_selected_frame(frames, monkeypatch):
    imgs, saves = frames
    imgs[3].rotation_angle = 90
    ip, host = _preview(current=1)
    host.thumbnail_list.selected = [0, 1, 3]
    seen = []
    _answer(ip, monkeypatch, "each", seen)
    ip.rotate_left()
    assert seen and seen[0]["text"] == "Rotate all 3 selected frames left?"
    assert imgs[0].rotation_angle == 270 and imgs[1].rotation_angle == 270
    assert imgs[3].rotation_angle == 0                     # 90 from where it was
    assert imgs[2].rotation_angle == 0 and imgs[4].rotation_angle == 0
    assert [imgs[i].undo_pushes for i in range(5)] == [1, 1, 0, 1, 0]
    assert ip.current_rotation == 270                       # canvas follows
    assert sorted(host.thumbnail_list.updated) == [0, 1, 3]
    assert saves == [1]
    assert host.sliders_panel.hints[-1] == "Rotated 3 frames left."


def test_no_cancels_everything(frames, monkeypatch):
    imgs, saves = frames
    ip, host = _preview(current=0)
    host.thumbnail_list.selected = [0, 1, 2]
    _answer(ip, monkeypatch, None)
    for control in (ip.rotate_left, ip.rotate_right, ip.mirror_horizontal, ip.mirror_vertical):
        control()
    assert all(f.triple == (0, False, False) and f.undo_pushes == 0 for f in imgs)
    assert ip.current_rotation == 0 and not ip.current_horizontal_flip
    assert host.thumbnail_list.updated == [] and saves == []


# --- 5. mixed flips -----------------------------------------------------------

def _mixed(frames):
    imgs, _ = frames
    imgs[0].horizontal_mirrored = True
    imgs[2].horizontal_mirrored = True
    return imgs


def test_flip_each_toggles_every_frame(frames, monkeypatch):
    imgs = _mixed(frames)
    ip, host = _preview(current=0)
    host.thumbnail_list.selected = [0, 1, 2, 3, 4]
    seen = []
    _answer(ip, monkeypatch, "each", seen)
    ip.mirror_horizontal()
    assert seen[0]["choices"][1] == ("rest", "Flip the Other 3")
    assert [f.horizontal_mirrored for f in imgs] == [False, True, False, True, True]
    assert all(f.undo_pushes == 1 for f in imgs)
    assert not ip.current_horizontal_flip                   # frame 0 went back


def test_flip_the_other_n_changes_only_the_unflipped(frames, monkeypatch):
    imgs = _mixed(frames)
    ip, host = _preview(current=1)
    host.thumbnail_list.selected = [0, 1, 2, 3, 4]
    _answer(ip, monkeypatch, "rest")
    ip.mirror_horizontal()
    assert all(f.horizontal_mirrored for f in imgs)
    assert [f.undo_pushes for f in imgs] == [0, 1, 0, 1, 1]
    assert sorted(host.thumbnail_list.updated) == [1, 3, 4]
    assert ip.current_horizontal_flip
    assert host.sliders_panel.hints[-1] == (
        "Flipped 3 frames horizontally. All 5 selected are now flipped.")


# --- 6. the frame on screen outside the selection ----------------------------

def test_frame_on_screen_outside_the_selection_is_left_alone(frames, monkeypatch):
    imgs, _ = frames
    ip, host = _preview(current=4)
    host.thumbnail_list.selected = [0, 1]
    _answer(ip, monkeypatch, "each")
    ip._zoom = 2.0
    ip.rotate_right()
    assert imgs[0].rotation_angle == 90 and imgs[1].rotation_angle == 90
    assert imgs[4].triple == (0, False, False) and imgs[4].undo_pushes == 0
    assert ip.current_rotation == 0
    assert ip._zoom == 2.0                                  # view did not jump


# --- 7. the lit mirror icons --------------------------------------------------

def test_mirror_actions_follow_the_frame_on_screen(frames, monkeypatch):
    imgs, _ = frames
    ip, host = _preview(current=0)
    assert ip.mirror_h_action.isCheckable() and ip.mirror_v_action.isCheckable()
    host.thumbnail_list.selected = [0]
    ip.mirror_horizontal()
    assert ip.mirror_h_action.isChecked() and not ip.mirror_v_action.isChecked()
    assert ip.mirror_h_action.toolTip() == "Mirror Horizontal (this frame is mirrored)"
    ip.mirror_v_action.trigger()                            # a real toolbar click
    assert imgs[0].vertical_mirrored and ip.mirror_v_action.isChecked()
    ip.mirror_h_action.trigger()
    assert not imgs[0].horizontal_mirrored and not ip.mirror_h_action.isChecked()
    assert ip.mirror_h_action.toolTip() == "Mirror Horizontal"
    # Another frame on screen: its own state shows.
    imgs[3].horizontal_mirrored = True
    ip.current_idx = 3
    ip._sync_mirror_actions()
    assert ip.mirror_h_action.isChecked() and not ip.mirror_v_action.isChecked()
    ip.clear_preview()
    assert not ip.mirror_h_action.isChecked() and not ip.mirror_v_action.isChecked()


def test_cancelled_prompt_puts_the_clicked_icon_back(frames, monkeypatch):
    imgs, _ = frames
    ip, host = _preview(current=0)
    host.thumbnail_list.selected = [0, 1]
    _answer(ip, monkeypatch, None)
    ip.mirror_h_action.trigger()                            # Qt toggles it on click
    assert not imgs[0].horizontal_mirrored
    assert not ip.mirror_h_action.isChecked()


def test_bracket_keys_go_through_the_same_prompt(frames, monkeypatch):
    imgs, _ = frames
    ip, host = _preview(current=0)
    host.thumbnail_list.selected = [0, 2]
    seen = []
    _answer(ip, monkeypatch, "each", seen)
    ip.rotate_right()                                       # what "]" calls
    assert seen and imgs[0].rotation_angle == 90 and imgs[2].rotation_angle == 90


# --- 8. the brighter On icon --------------------------------------------------

def _mean_lightness(pm):
    img = pm.toImage()
    total = count = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 128:
                total += c.lightness()
                count += 1
    return total / max(count, 1)


def test_on_icon_is_brighter_than_off():
    path = resource_path("icons/horizontal-mirror-icon.png")
    icon = theme.load_tinted_icon(path, on_color=theme.ICON_ON)
    off = icon.pixmap(24, 24, QIcon.Normal, QIcon.Off)
    on = icon.pixmap(24, 24, QIcon.Normal, QIcon.On)
    assert _mean_lightness(on) > _mean_lightness(off) + 20
    plain = theme.load_tinted_icon(path)                    # unchanged without on_color
    assert abs(_mean_lightness(plain.pixmap(24, 24, QIcon.Normal, QIcon.On))
               - _mean_lightness(plain.pixmap(24, 24, QIcon.Normal, QIcon.Off))) < 1
