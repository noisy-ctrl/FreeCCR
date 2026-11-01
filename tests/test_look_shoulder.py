#!/usr/bin/env python3
"""A Film Look's shoulder rebuilt smooth (core/look_shoulder.py,
spec/film-look-lut.md revision 2026-11-01 (2)): the user's "Noritsu Lomo 800"
went nearly flat in the whites and then jumped to white."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from core import look_lut as ll  # noqa: E402
from core import look_shoulder as ls  # noqa: E402

LO, HI, N = -0.25, 1.25, 33
X = np.linspace(LO, HI, N)


def _curve(x):
    """A lab-like tone curve: a toe, mid slope ~1.5, a shoulder that goes
    flat at 0.95 from density 0.85 and jumps to 1 at 1.02 (the defect)."""
    y = 1.0 / (1.0 + np.exp(-(x - 0.35) * 6.0))
    y = np.where(x > 0.85, 0.95 + 0.004 * (x - 0.85), np.minimum(y, 0.95))
    return np.where(x >= 1.02, 1.0, y)


def _table(tint=(1.0, 0.98, 0.96)):
    """[b][g][r] table: each output channel its own input's curve, a little
    tinted, plus a small dependence on the other channels (crosstalk)."""
    b, g, r = np.meshgrid(X, X, X, indexing="ij")
    out = np.stack([_curve(r) * tint[0] + 0.01 * _curve(g),
                    _curve(g) * tint[1] + 0.01 * _curve(r),
                    _curve(b) * tint[2] + 0.01 * _curve(g)], -1)
    return np.clip(out, 0, 1)


def test_the_plateau_and_the_jump_go_nothing_darker_nothing_below_the_knee():
    t = _table()
    new, info = ls.reshape(t, LO, HI, 0.0)
    assert info is not None
    Y0 = ls.grey_axis(t) @ ls.LUMA
    Y1 = ls.grey_axis(new) @ ls.LUMA
    below = X < info["knee"]
    assert np.array_equal(new[below][:, below][:, :, below], t[below][:, below][:, :, below])
    assert np.all(new >= t - 1e-12)                     # never darker
    assert np.all(np.diff(Y1) >= -1e-12)                # monotone along grey
    # the flat run 0.85..1.0 now climbs: every node step there gains levels
    run = (X > info["knee"]) & (X < info["white"] - 0.03)
    steps1 = np.diff(Y1)[run[:-1]] * 255
    steps0 = np.diff(Y0)[run[:-1]] * 255
    assert steps1.min() > 3 * max(steps0.min(), 0.05)
    # detail 0 clips nothing new: white where it was
    assert info["white"] == pytest.approx(info["white_before"])
    # no jump: the largest single step near white is far smaller than before
    top = X > 0.8
    assert np.diff(Y1)[top[:-1]].max() < 0.5 * np.diff(Y0)[top[:-1]].max()


def test_more_detail_keeps_more_highlight_contrast_and_reaches_white_sooner():
    t = _table()
    out = {d: ls.reshape(t, LO, HI, d) for d in (0.0, 0.5, 1.0)}
    rep = {d: ls.report(t, out[d][0], LO, HI) for d in out}
    lv = [rep[d]["levels_0.65_0.80"][1] for d in (0.0, 0.5, 1.0)]
    assert lv[0] <= lv[1] <= lv[2] and lv[2] > lv[0] + 3
    whites = [out[d][1]["white"] for d in (0.0, 0.5, 1.0)]
    assert whites[0] >= whites[1] >= whites[2]
    for d in out:
        assert rep[d]["monotone_after"] and rep[d]["never_darker"]


def test_tint_kept_and_each_channel_follows_its_own_density():
    t = _table()
    new, info = ls.reshape(t, LO, HI, 0.5)
    k = int(np.searchsorted(X, info["knee"]))
    i = k + 2                                            # a little past the knee
    before, after = t[i, i, i], new[i, i, i]
    # the headroom keeps its proportions (warm stays warm)
    h0, h1 = 1 - before, 1 - after
    assert h1 / h1.sum() == pytest.approx(h0 / h0.sum(), abs=1e-6)
    # a node bright in red only: green and blue (dark inputs) don't move
    lo_i = int(np.searchsorted(X, 0.2))
    assert np.allclose(new[lo_i, lo_i, i, 1:], t[lo_i, lo_i, i, 1:])
    assert new[lo_i, lo_i, i, 0] > t[lo_i, lo_i, i, 0]


def test_a_look_without_a_flat_shoulder_is_left_alone():
    b, g, r = np.meshgrid(X, X, X, indexing="ij")
    lin = np.clip(np.stack([r, g, b], -1) * 0.8 + 0.1, 0, 1)   # straight to the top
    new, info = ls.reshape(lin, LO, HI, 0.0)
    assert info is None and np.array_equal(new, lin)


def test_the_tool_writes_a_readable_cube(tmp_path, capsys):
    import look_shoulder as tool
    src = tmp_path / "Lab Look.cube"
    ll.write_cube(str(src), _table(), (LO,) * 3, (HI,) * 3, title="Lab Look")
    assert tool.main([str(src), "--detail", "50"]) == 0
    out = tmp_path / "Lab Look (shoulder 50).cube"
    lut = ll.read_cube(str(out))
    assert lut.size == N and lut.title == "Lab Look"
    text = capsys.readouterr().out
    assert "knee at density" in text and "monotone: True, never darker: True" in text
