#!/usr/bin/env python3
"""Colour Organics: another stock's lab fills only the hues no other lab
measured, scaled to their strength (spec/colour-organics.md, revision
2026-11-09 b)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import organics_fit as of  # noqa: E402


def _cells(rows):
    return np.array([(h, m, w, n) for h, m, w, n in rows], np.float64)


OTHERS = _cells([(15, 4.0, 100, 100), (45, 3.0, 100, 100), (225, -5.0, 100, 100),
                 (345, 10.0, 100, 100), (195, 0.0, 100, 100)])
P160 = _cells([(15, 8.0, 50, 50), (45, 6.0, 50, 50), (225, -10.0, 50, 50),
               (345, 20.0, 50, 50), (195, 5.0, 50, 50),          # sky disagrees
               (275, 16.0, 5, 5), (315, 12.0, 5, 5), (235, -10.0, 5, 5)])


def test_scale_is_measured_where_both_agree_in_direction():
    # every agreeing span is exactly half as strong in the other labs
    assert of.gap_scale(P160, OTHERS) == pytest.approx(0.5)
    assert of.gap_scale(P160, _cells([])) == 1.0
    assert of.gap_scale(P160 * [1, 10, 1, 1], OTHERS) == of.GAP_SCALE_RANGE[0]


def test_only_cells_no_other_lab_measured_are_used_scaled():
    g = of.gap_cells(P160, OTHERS)
    assert sorted(g[:, 0].tolist()) == [275, 315]                 # 235 is within 10 of 225
    assert sorted(g[:, 1].tolist()) == pytest.approx([6.0, 8.0])
    assert g[:, 2].tolist() == [5, 5]                              # weights unchanged


def test_a_gap_lab_moves_only_the_gap(monkeypatch):
    base = of.fit_curve(OTHERS)
    with_gap = of.fit_curve(np.concatenate([OTHERS, of.gap_cells(P160, OTHERS)]))
    k = np.arange(36) * 10
    far = (k < 240) | (k > 330)
    gap = (k >= 270) & (k <= 310)
    assert np.all(with_gap[gap] > base[gap])                       # towards the measured move
    assert np.abs(with_gap - base)[far].max() < np.abs(with_gap - base)[gap].max()
