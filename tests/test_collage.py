#!/usr/bin/env python3
"""The collage maker's layouts (spec/collage-maker.md): every cell inside the
canvas, none overlapping, gaps kept, shapes kept with Respect crop, crop
changes capped without it, the same seed the same arrangement, the carousel's
seam rules, Full height."""
import itertools
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import collage as cl  # noqa: E402

ROLL = [1.5, 0.667, 1.5, 1.5, 0.667, 1.35, 1.5, 0.74, 1.0, 1.5, 1.5, 0.667]


def _overlap(a, b, tol=0.5):
    return (a.x < b.right() - tol and b.x < a.right() - tol
            and a.y < b.bottom() - tol and b.y < a.bottom() - tol)


def _inside(arr, tol=0.5):
    return all(c.x >= -tol and c.y >= -tol and c.right() <= arr.width + tol
               and c.bottom() <= arr.height + tol for c in arr.cells)


@pytest.mark.parametrize("n", [1, 2, 3, 5, 9, 12])
@pytest.mark.parametrize("respect", [True, False])
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_mosaic_fills_the_canvas_without_overlap(n, respect, seed):
    st = cl.Settings(layout="mosaic", respect=respect, gap=14)
    arr = cl.arrange(ROLL[:n], st, seed)
    assert len(arr.cells) == n and sorted(c.index for c in arr.cells) == list(range(n))
    assert _inside(arr)
    for a, b in itertools.combinations(arr.cells, 2):
        assert not _overlap(a, b)
    if respect:
        for c in arr.cells:                       # each frame keeps its shape
            assert abs((c.w / c.h) / ROLL[c.index] - 1) < 0.005
            assert c.trim == (0.0, 0.0, 1.0, 1.0)
    else:
        assert arr.worst_change <= st.variance + 1e-6
        for c in arr.cells:
            assert abs((c.w / c.h) / ROLL[c.index] - 1) <= st.variance + 1e-6


def test_mosaic_gaps_are_kept():
    st = cl.Settings(layout="mosaic", gap=20)
    arr = cl.arrange(ROLL[:9], st, 3)
    for a, b in itertools.combinations(arr.cells, 2):
        if abs(a.y - b.y) < 1:                    # same row
            l, r = (a, b) if a.x < b.x else (b, a)
            assert r.x - l.right() >= 20 - 0.5


def test_the_same_seed_gives_the_same_arrangement_and_others_differ():
    st = cl.Settings(layout="mosaic")
    a = [c.rect() for c in cl.arrange(ROLL[:9], st, 5).cells]
    b = [c.rect() for c in cl.arrange(ROLL[:9], st, 5).cells]
    assert a == b
    others = {tuple(round(v) for r in cl.arrange(ROLL[:9], st, s).cells for v in r.rect())
              for s in range(1, 12)}
    assert len(others) > 3


def test_seed_history_walks_back_and_forth():
    h = cl.SeedHistory(1, random.Random(0))
    s2 = h.shuffle()
    s3 = h.shuffle()
    assert h.label() == "arrangement 3 of 3" and h.seed == s3
    assert h.back() and h.seed == s2 and h.back() and h.seed == 1 and not h.back()
    assert h.forward() and h.seed == s2
    s4 = h.shuffle()                              # a new branch drops what was ahead
    assert h.seeds == [1, s2, s4] and h.label() == "arrangement 3 of 3"
    assert not h.forward()


@pytest.mark.parametrize("style", ["varied", "aligned"])
@pytest.mark.parametrize("seed", [2, 5, 11, 23])
def test_carousel_frames_cross_a_post_edge_with_enough_on_each_side(style, seed):
    st = cl.Settings(layout="carousel", style=style, posts=3)
    arr = cl.arrange(ROLL[:9], st, seed)
    assert arr.posts == 3 and arr.width == 3 * 1080
    assert _inside(arr, tol=2)
    for a, b in itertools.combinations(arr.cells, 2):
        assert not _overlap(a, b)
    bad = 0
    for c in arr.cells:
        for seam in (1080, 2160):
            if c.x < seam < c.right():
                share = (seam - c.x) / c.w
                bad += not (cl.SEAM_SHARE - 0.02 <= share <= 1 - cl.SEAM_SHARE + 0.02)
    assert bad == 0
    if style == "aligned":
        assert len({round(c.h, 3) for c in arr.cells}) == 1


@pytest.mark.parametrize("seed", [1, 3, 8])
def test_full_height_frames_are_95_percent_tall_aligned_and_never_slivers(seed):
    """The user, 2026-11-20: frames ~95 % of the full height, aligned, split
    between swipes."""
    st = cl.Settings(layout="carousel", style="full")
    arr = cl.arrange(ROLL, st, seed)
    hs = {round(c.h, 3) for c in arr.cells}
    assert hs == {round(0.95 * 1350, 3)}
    assert len({round(c.y, 3) for c in arr.cells}) == 1
    assert abs(arr.cells[0].y - (1350 - 0.95 * 1350) / 2) < 1e-6
    assert arr.posts == arr.width // 1080 and arr.posts >= 2
    split = 0
    for c in arr.cells:
        for p in range(1, arr.posts):
            seam = p * 1080
            if c.x < seam < c.right():
                split += 1
                limit = max(cl.SLIVER_SHARE * c.w, cl.SLIVER_PX)
                assert seam - c.x >= limit - 0.5 and c.right() - seam >= limit - 0.5
    assert split >= 1                             # frames do run between swipes
    assert _inside(arr, tol=2)
    for a, b in itertools.combinations(arr.cells, 2):
        assert not _overlap(a, b)


def test_full_height_takes_as_many_posts_as_the_row_needs():
    few = cl.arrange([1.5, 1.5], cl.Settings(layout="carousel", style="full"), 1)
    many = cl.arrange([1.5] * 8, cl.Settings(layout="carousel", style="full"), 1)
    assert few.posts < many.posts
    lower = cl.arrange([1.5] * 8, cl.Settings(layout="carousel", style="full",
                                              height_share=0.8), 1)
    assert {round(c.h) for c in lower.cells} == {round(0.8 * 1350)}
    assert lower.posts <= many.posts


def test_each_post_its_own_never_crosses():
    st = cl.Settings(layout="carousel", style="posts", posts=3)
    arr = cl.arrange(ROLL[:9], st, 4)
    for c in arr.cells:
        assert int(c.x // 1080) == int((c.right() - 1) // 1080)


@pytest.mark.parametrize("direction", ["across", "down"])
def test_triptych_one_height_or_width_centred(direction):
    st = cl.Settings(layout="triptych", frames=3, direction=direction, width=1080, height=1920)
    arr = cl.arrange([0.667, 0.667, 0.667, 1.5], st, 2)
    assert len(arr.cells) == 3 and _inside(arr)
    key = (lambda c: round(c.h, 3)) if direction == "across" else (lambda c: round(c.w, 3))
    assert len({key(c) for c in arr.cells}) == 1
    xs = [c.x + c.w / 2 for c in arr.cells] if direction == "down" else \
        [c.y + c.h / 2 for c in arr.cells]
    assert all(abs(v - (540 if direction == "down" else 960)) < 1 for v in xs)


def test_tear_puts_a_band_over_a_background():
    arr = cl.arrange([1.5, 0.667, 1.5], cl.Settings(layout="tear", width=1080, height=1920), 9)
    back, band = arr.cells
    assert (back.x, back.y, back.w, back.h) == (0, 0, 1080, 1920)
    assert arr.band == (band.y, band.bottom()) and 0 < band.y < band.bottom() < 1920
    assert back.index != band.index


def test_cover_trim_keeps_the_middle():
    assert cl.cover_trim(1.5, 1.5) == (0.0, 0.0, 1.0, 1.0)
    x, y, w, h = cl.cover_trim(1.5, 1.0)          # a 3:2 frame in a square: sides trimmed
    assert y == 0 and h == 1 and abs(w - 1 / 1.5) < 1e-9 and abs(x - (1 - w) / 2) < 1e-9


# --- film strip (revision 2026-11-22: one film, end to end) -------------------

@pytest.mark.parametrize("W,H,n,direction", [(1080, 1920, 8, "down"), (1080, 1350, 8, "down"),
                                             (1080, 566, 6, "across"), (1080, 1080, 3, "down")])
def test_film_strips_lay_the_frames_end_to_end(W, H, n, direction):
    st = cl.Settings(width=W, height=H, layout="strip", gap=20, piece_aspect=35 / 38)
    arr = cl.arrange([1.5] * n, st, 7)
    assert arr.strip_dir == direction
    assert sorted(c.index for c in arr.cells) == list(range(n))
    assert sum(len(s) for s in arr.strips) == n
    for c in arr.cells:
        assert c.x >= -1e-6 and c.y >= -1e-6 and c.x + c.w <= W + 1e-6 and c.y + c.h <= H + 1e-6
        a = c.w / c.h
        assert a == pytest.approx(35 / 38 if direction == "down" else 38 / 35, rel=1e-6)
    for run in arr.strips:                        # each strip one film: no gaps in it
        cs = [arr.cells[k] for k in run]
        for a, b in zip(cs, cs[1:]):
            if direction == "down":
                assert b.y == pytest.approx(a.y + a.h) and b.x == pytest.approx(a.x)
            else:
                assert b.x == pytest.approx(a.x + a.w) and b.y == pytest.approx(a.y)
    firsts = [arr.cells[run[0]] for run in arr.strips]
    for a, b in zip(firsts, firsts[1:]):          # strips side by side, the gap between
        if direction == "down":
            assert b.x == pytest.approx(a.x + a.w + 20) and b.y == pytest.approx(a.y)
        else:
            assert b.y == pytest.approx(a.y + a.h + 20) and b.x == pytest.approx(a.x)
    # in reading order: down each strip, then the next
    assert [arr.cells[k].index for run in arr.strips for k in run] == list(range(n))


def test_more_strips_only_when_it_gives_bigger_frames():
    one = cl.film_strips(2, 35 / 38, 1080, 1920, 20)
    assert len(one[1]) == 1                       # two frames fit one strip best
    many = cl.film_strips(12, 35 / 38, 1080, 1920, 20)
    assert len(many[1]) > 1
