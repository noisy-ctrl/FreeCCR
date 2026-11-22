"""The colour shots' marks learn from corrections (spec/marks-learning.md)."""
import math
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import film_marks as fmk  # noqa: E402
from core import ir_dust_learn, marks_model  # noqa: E402


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECCR_IR_DUST_LEARNED", str(tmp_path / "learned.json"))
    ir_dust_learn.reset_cache()
    yield
    ir_dust_learn.reset_cache()


def _mark(depth, stand, kind="dark", step=0.1, area=30, elong=1.5, irs=0.0, colour=0.05,
          x0=10, y0=10):
    m = np.zeros((6, 6), bool)
    m[1:5, 1:5] = True
    feats = np.array([depth, stand, step, math.log(area), elong, irs, colour], np.float32)
    return fmk.Mark(kind=kind, x0=x0, y0=y0, mask=m, feats=feats, seed=(x0 + 2, y0 + 2))


def test_below_ten_corrections_the_rule_stands_exactly():
    rng = np.random.default_rng(4)
    for kind in ("light", "dark"):
        ir_dust_learn.add("marks-" + kind, [([12, 9, 0.1, 3, 1.2, 0, 0], 0, 1.0)] * 9)
    for _ in range(300):
        mk = _mark(float(rng.uniform(2, 20)), float(rng.uniform(2, 15)),
                   kind=str(rng.choice(["light", "dark"])), step=float(rng.uniform(0, 0.8)),
                   area=float(rng.uniform(4, 1200)), irs=float(rng.uniform(-1, 4)))
        for s in (0, 50, 100):
            for has_ir in (False, True):
                need_d, need_s = fmk.thresholds(s)
                old = (fmk.gate(mk, has_ir) and float(mk.feats[0]) >= need_d
                       and float(mk.feats[1]) >= need_s)
                assert fmk.passes(mk, s, has_ir) == old
    assert marks_model.learned("light") is None and marks_model.learned("dark") is None


def test_the_default_is_the_rule_far_from_its_line_and_follows_sensitivity():
    for kind in ("light", "dark"):
        base = marks_model.default(kind)
        deep, faint = _mark(25, 18, kind), _mark(4, 2.5, kind)
        assert marks_model.logit(base, deep.feats) > marks_model.shift(kind, 0)
        assert marks_model.logit(base, faint.feats) < marks_model.shift(kind, 100)
        # more Sensitivity, a lower line
        sh = [marks_model.shift(kind, s) for s in (0, 25, 50, 75, 100)]
        assert all(a > b for a, b in zip(sh, sh[1:])) and sh[2] == 0.0


def test_ten_not_a_mark_corrections_stop_those_marks_and_leave_the_rest():
    like = _mark(11, 8.5, "dark")
    assert fmk.passes(like, 50)                                   # the rule flags it
    ir_dust_learn.add("marks-dark", [([11 + 0.3 * i, 8.5, 0.1, math.log(30), 1.5, 0, 0.05],
                                      0, 1.0) for i in range(12)])
    assert marks_model.learned("dark") is not None
    assert not fmk.passes(like, 50)                               # now it isn't
    assert fmk.passes(_mark(40, 30, "dark"), 50)                  # a strong one still is
    assert marks_model.learned("light") is None                   # the other kind untouched
    assert fmk.passes(_mark(11, 8.5, "light"), 50)


def test_ten_missed_marks_get_faint_ones_flagged():
    faint = _mark(6.5, 5.0, "light")
    assert not fmk.passes(faint, 50)
    ir_dust_learn.add("marks-light", [([6.5, 5.0 + 0.1 * i, 0.1, math.log(30), 1.5, 0, 0.05],
                                       1, 1.0) for i in range(10)])
    assert not fmk.passes(faint, 50)                              # ten so far below: not yet
    ir_dust_learn.add("marks-light", [([6.5, 5.0 + 0.1 * i, 0.1, math.log(30), 1.5, 0, 0.05],
                                       1, 1.0) for i in range(10)])
    assert fmk.passes(faint, 50)                                  # twenty: yes
    assert not fmk.passes(_mark(2.5, 1.5, "light"), 50)           # far below stays out
    assert not fmk.passes(_mark(25, 18, "light", step=0.9), 50)   # the gate still holds


def test_fit_map_takes_seven_features():
    base = marks_model.default("dark")
    X = marks_model.features([[10, 8, 0.1, 3.0, 1.2, 0.0, 0.0]] * 5)
    mu, sd, w, b = ir_dust_learn.fit_map(X, np.zeros(5), np.ones(5), base)
    assert w.shape == (7,) and b < base[3]


def test_a_brush_stroke_over_an_unflagged_mark_is_a_missed_one():
    class Img:
        pass
    img = Img()
    a, b = _mark(6, 5, "light", x0=10, y0=10), _mark(12, 9, "dark", x0=60, y0=40)
    img._mark_map = fmk.MarkMap(marks=[a, b], size=(100, 80), shots=3, has_ir=False)
    img.ir_dust_pending = [{"kind": "mark", "mk_c": 1, "mk_k": "dark", "pts": [[0.6, 0.5]],
                            "r": 0.01, "mk_f": b.feats.tolist()}]
    img.dust_spots = []
    ax, ay = a.centre()
    bx, by = b.centre()
    assert fmk.learn_from_stroke(img, [[bx / 100, by / 80]], 0.01, 0.8) == 0   # flagged already
    assert fmk.learn_from_stroke(img, [[ax / 100, ay / 80]], 0.01, 0.8) == 1
    ex = ir_dust_learn.examples("marks-light")
    assert len(ex) == 1 and ex[0][-2:] == [1, 1.0]
    assert fmk.learn_from_stroke(img, [[0.99, 0.99]], 0.005, 0.8) == 0         # nothing there
    img._mark_map = None
    assert fmk.learn_from_stroke(img, [[ax / 100, ay / 80]], 0.01, 0.8) == 0


def test_forget_clears_the_marks():
    ir_dust_learn.add("marks-dark", [([11, 8.5, 0.1, 3.4, 1.5, 0, 0.05], 0, 1.0)] * 12)
    assert marks_model.learned("dark") is not None
    for k in ir_dust_learn.MARK_KINDS:
        ir_dust_learn.forget(k)
    assert marks_model.learned("dark") is None


def test_the_panel_forgets_marks_and_says_what_it_learned(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.dust_panel import DustRemovalPanel

    class Prev:
        current_idx = None
        dust_fit_spot = True

        def set_dust_tool(self, t):
            pass

        def has_dust_before(self):
            return False
    p = DustRemovalPanel(None, Prev())
    assert p._ir_learned_text(None) == ""
    ir_dust_learn.add("marks-dark", [([11, 8.5, 0.1, 3.4, 1.5, 0, 0.05], 0, 1.0)] * 12)
    assert p._ir_learned_text(None) == "Marks learned from 12 of your corrections."
    (text, cb, enabled), = p.ir_heading_actions()
    assert enabled
    monkeypatch.setattr(p, "_confirm_forget_ir", lambda kind: True)
    cb()
    assert ir_dust_learn.examples("marks-dark") == []
    p.deleteLater()
