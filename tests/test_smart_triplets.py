#!/usr/bin/env python3
"""Trichrome sets grouped by the light that lit each frame
(spec/smart-triplets.md)."""

import glob
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest  # noqa: E402

from core import triplet_plan as tp  # noqa: E402

SHARES = {"R": (0.9, 0.05, 0.05), "G": (0.05, 0.9, 0.05), "B": (0.05, 0.05, 0.9),
          "W": (0.34, 0.33, 0.33), "?": None}


def _plan(seq, quality=None):
    paths = [f"/roll/F{i:03d}_{c}.RAF" for i, c in enumerate(seq)]
    lit = dict(zip(paths, seq))
    plan = tp.plan_triplets(paths, shares_fn=lambda p: SHARES[lit[p]],
                            quality=quality or (lambda p, c: (1.0, 0.0)), workers=1)
    sets = ["".join(lit[p] + p[7:10] for p in t) for t in plan.sets]
    notes = [(n["short"], [p[7:10] for p in n["paths"]]) for n in plan.notes]
    return sets, notes, plan


def test_complete_sets_in_order():
    sets, notes, _ = _plan("RGBRGBRGB")
    assert sets == ["R000G001B002", "R003G004B005", "R006G007B008"] and notes == []


def test_an_odd_count_merges_what_it_can_and_reports_the_rest():
    sets, notes, _ = _plan("RGBRGBRG")
    assert sets == ["R000G001B002", "R003G004B005"]
    assert notes == [("Frames missing", ["006", "007"])]


def test_a_folder_that_starts_mid_shot():
    sets, notes, _ = _plan("GBRGB")
    assert sets == ["R002G003B004"] and notes == [("Frames missing", ["000", "001"])]


def test_a_frame_not_lit_by_one_colour_is_left_out():
    sets, notes, _ = _plan("RGBWRGB")
    assert sets == ["R000G001B002", "R004G005B006"]
    assert notes == [("Not one colour", ["003"])]


def test_an_abandoned_shot_then_a_full_one():
    sets, notes, _ = _plan("RGRGB")
    assert sets == ["R002G003B004"] and notes == [("Frames missing", ["000", "001"])]


def test_another_capture_order():
    sets, notes, _ = _plan("BGRBGR")
    assert len(sets) == 2 and notes == []


def test_a_retake_keeps_the_sharper_and_offers_the_other():
    sharp = {"001": 2.0, "002": 1.0}

    def q(p, c):
        return sharp.get(p[7:10], 1.0), 0.0
    sets, notes, plan = _plan("RGGB", q)
    assert sets == ["R000G001B003"]
    assert notes[0][0] == "Unused retake"
    assert notes[0][1] == ["000", "002", "003"]           # the other green, in its set
    assert "sharper" in plan.notes[0]["reason"]
    assert plan.chosen == [("G", "/roll/F001_G.RAF", ["/roll/F002_G.RAF"])]


def test_a_retake_prefers_unclipped_then_the_later_take():
    def clipped_first(p, c):
        return (3.0, 0.2) if p.endswith("001_G.RAF") else (1.0, 0.0)
    sets, _n, _p = _plan("RGGB", clipped_first)
    assert sets == ["R000G002B003"]                        # the clipped one loses
    sets, _n, plan = _plan("RGGB", lambda p, c: (1.0, 0.0))
    assert sets == ["R000G002B003"]                        # a tie: the retake
    assert "later take" in plan.notes[0]["reason"]
    sets, _n, _p = _plan("RGGB", lambda p, c: (1.0 if "001" in p else 0.99, 0.0))
    assert sets == ["R000G002B003"]                        # within 3 %: still the retake


def test_each_frame_is_scored_once():
    calls = []
    _plan("RGGBRGGB", lambda p, c: (calls.append(p), (1.0, 0.0))[1])
    assert len(calls) == len(set(calls)) == 4


def test_unreadable_light_falls_back_to_name_order():
    sets, notes, _ = _plan("????????")
    assert len(sets) == 2 and notes == [("Left over", ["006", "007"])]


def test_colour_of():
    assert tp.colour_of((0.7, 0.2, 0.1)) == "R"
    assert tp.colour_of((0.5, 0.3, 0.2)) is None          # below LIGHT_SHARE_MIN
    assert tp.colour_of(None) == "?"


def test_the_backend_merges_sets_and_reports_the_rest(tmp_path, monkeypatch):
    """Open with 3-way merge on: no refusal for 4 files; one set merges and
    the fourth comes back as an attention entry with thumbnails."""
    from core.ccr_backend import ccr_backend
    from core import scan_type as st
    from core import catalog
    paths = []
    for i, c in enumerate("RGBR"):
        p = tmp_path / f"F{i}_{c}.RAF"
        p.write_bytes(b"x")
        paths.append(str(p))
    lit = {p: SHARES[p[-5]] for p in paths}
    monkeypatch.setattr(st, "light_shares", lambda p: lit[p])
    monkeypatch.setattr(st, "thumb_bgr", lambda p, height=44: None)
    made = []

    def fake_merge(sources, **k):
        made.append(list(sources))
        from core.ccr_image import CCRImage
        im = CCRImage.__new__(CCRImage)
        im.file_path = sources[0]
        im.display_name = os.path.basename(sources[0])
        return [im]
    monkeypatch.setattr(catalog, "create_images_for_merge", fake_merge)
    monkeypatch.setattr(ccr_backend, "rgb_merge_mode", True, raising=False)
    gen = ccr_backend._load_generation
    n = ccr_backend._load_merged_triplets(paths, gen=gen)
    assert n == 1 and made == [paths[:3]]
    assert ccr_backend.last_merge_error is None
    notes = ccr_backend.last_merge_attention
    assert [(x["short"], x["paths"]) for x in notes] == [("Frames missing", [paths[3]])]
    assert notes[0]["thumbs"] == [None]
    ccr_backend.last_merge_attention = []
    ccr_backend.images = []                     # the fakes mustn't reach later tests
    ccr_backend.file_paths = []


REAL = sorted(glob.glob("/mnt/user-data/uploads/Light balanced film base/*.RAF"))


@pytest.mark.skipif(len(REAL) < 15, reason="the user's trichrome RAFs aren't here")
def test_on_the_users_raws():
    """Five R, G, B shots: five sets; one frame removed: four sets and the
    rest of that shot reported."""
    plan = tp.plan_triplets(REAL)
    assert [tuple(os.path.basename(p) for p in s) for s in plan.sets][0] == (
        "DSCF1583.RAF", "DSCF1584.RAF", "DSCF1585.RAF")
    assert len(plan.sets) == 5 and plan.notes == []
    plan = tp.plan_triplets(REAL[:4] + REAL[5:])
    assert len(plan.sets) == 4
    assert [n["short"] for n in plan.notes] == ["Frames missing"]


def test_the_window_shows_them_greyed_out_after_the_load_without_a_warning(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    from ui.main_window import MainWindow
    from core.ccr_backend import ccr_backend
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))
    ccr_backend.images = []
    ccr_backend.file_paths = []
    ccr_backend.last_load_errors = []
    w = MainWindow()
    ccr_backend.last_merge_error = None
    ccr_backend.last_merge_attention = [
        {"paths": ["/r/F3_R.RAF"], "short": "Frames missing",
         "reason": "only red for this shot", "thumbs": [None]},
        {"paths": ["/r/F0_R.RAF", "/r/F2_G.RAF", "/r/F4_B.RAF"], "short": "Unused retake",
         "reason": "green was shot twice", "thumbs": [None] * 3}]
    w._cleanup_loader()
    tl = w.thumbnail_list
    assert warned == [] and ccr_backend.last_merge_attention == []
    assert tl.attention_list.count() == 2
    assert [tl.attention_list.item(i).text() for i in range(2)] == ["Frames missing", "Unused retake"]
    w._tether_attention = []
    tl.set_attention([])
