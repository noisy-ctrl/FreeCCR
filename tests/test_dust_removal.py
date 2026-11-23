#!/usr/bin/env python3
"""
Tests for the Dust Removal feature.

Dust edits are stored as NORMALIZED spots on the image
({kind, pts:[[x,y],...], r}) and healed at render time by
ccr_processor.apply_dust_removal (clone-heal patch fill, 16-bit native, with a
cv2.inpaint diffusion fallback; masked-only feathered composite). The AI
detector (dust_detect) only finds dust; the fill is the same path.
See spec/dust-removal.md.
"""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import cv2  # noqa: E402
from core import catalog, dust_detect  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from core.ccr_processor import (apply_dust_removal,  # noqa: E402
                                rasterize_dust_mask, DUST_FEATHER_DEFAULT)


def _flat_with_speck(h=100, w=100, base=30000, speck=60000, cx=50, cy=50, r=4):
    """Flat gray image with one bright circular speck near the center."""
    img = np.full((h, w, 3), base, dtype=np.uint16)
    cv2.circle(img, (cx, cy), r, (speck, speck, speck), -1)
    return img


# --- rasterize_dust_mask ----------------------------------------------------
class TestRasterize:
    def test_centered_spot_is_a_filled_circle(self):
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.1}
        mask = rasterize_dust_mask([spot], 100, 100)
        assert mask.dtype == np.uint8
        assert mask[50, 50] == 255          # center filled
        assert mask[50, 70] == 0            # well outside r_px=10
        area = int((mask > 0).sum())
        assert abs(area - np.pi * 100) < 60  # ~ pi*r_px^2, r_px = 10

    def test_resolution_independence(self):
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.1}
        a1 = int((rasterize_dust_mask([spot], 100, 100) > 0).sum())
        a2 = int((rasterize_dust_mask([spot], 200, 200) > 0).sum())
        # Same normalized spot covers ~4x the pixels at 2x resolution.
        assert 3.5 < a2 / a1 < 4.6

    def test_empty_spots_is_blank(self):
        mask = rasterize_dust_mask([], 50, 50)
        assert mask.shape == (50, 50)
        assert not mask.any()


# --- apply_dust_removal -----------------------------------------------------

def _reach(r_px):
    """How far the heal's source search looks for a round dab of radius
    r_px (spec/dust-heal-ring-match.md): 4 x its size, +50 % slack."""
    from core import ccr_processor as cp
    size = 2 * r_px + 1
    reach = min(cp._HEAL_SEARCH_MAX * max(1.0, r_px / 4.0),
                max(cp._HEAL_SEARCH_MIN, cp._HEAL_SEARCH * size))
    return 1.5 * reach + 2

class TestApplyDustRemoval:
    def test_identity_returns_same_object(self):
        img = _flat_with_speck()
        assert apply_dust_removal(img, []) is img
        assert apply_dust_removal(img, None) is img

    def test_speck_removed_and_far_pixels_untouched(self):
        img = _flat_with_speck(base=30000, speck=60000, cx=50, cy=50, r=4)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}
        out = apply_dust_removal(img, [spot])
        assert out.dtype == np.uint16
        assert out is not img                       # new array (non-destructive)
        # The speck is filled toward the flat surround.
        assert abs(int(out[50, 50, 0]) - 30000) < 15000
        assert int(out[50, 50, 0]) < 55000
        # A corner far from the mask is bit-for-bit unchanged.
        assert np.array_equal(out[0:10, 0:10], img[0:10, 0:10])

    def test_input_not_mutated(self):
        img = _flat_with_speck()
        before = img.copy()
        apply_dust_removal(img, [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}])
        assert np.array_equal(img, before)

    def test_fill_is_16bit_native_on_flat_field(self):
        # 30000 is NOT representable through an 8-bit round trip (the old
        # cv2.inpaint path quantized the fill to multiples of 257 -> 30069).
        # The clone heal copies real 16-bit pixels, so a flat field heals to
        # exactly the surrounding value.
        img = _flat_with_speck(base=30000, speck=60000, cx=50, cy=50, r=4)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}
        out = apply_dust_removal(img, [spot])
        core = out[47:54, 47:54]  # deep inside the healed hole (alpha == 1)
        assert int(np.abs(core.astype(np.int32) - 30000).max()) <= 1

    def test_heal_preserves_grain(self):
        # On a grainy background the fill must carry real texture, not the
        # smooth averaged patch diffusion inpainting produces (the round,
        # fan-like artifact). Healed-region noise must stay comparable to the
        # surround's.
        rng = np.random.default_rng(7)
        img = np.clip(rng.normal(30000, 3000, (100, 100, 3)), 0,
                      65535).astype(np.uint16)
        cv2.circle(img, (50, 50), 4, (60000, 60000, 60000), -1)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}
        out = apply_dust_removal(img, [spot])
        healed = out[46:55, 46:55].astype(np.float32)
        yy, xx = np.mgrid[0:100, 0:100]
        ann = (np.hypot(yy - 50, xx - 50) > 15) & (np.hypot(yy - 50, xx - 50) < 30)
        surround = out[ann].astype(np.float32)
        # Tone lands on the surround; grain survives (Telea gives ~0 std here).
        assert abs(float(healed.mean()) - float(surround.mean())) < 2000
        assert float(healed.std()) > 0.4 * float(surround.std())

    def test_heal_continues_gradient(self):
        # A linear gradient must continue through the patch (the tone
        # correction interpolates the ring differences), not flatten into a
        # single-toned blob.
        ramp = (10000 + np.arange(100, dtype=np.float32) * 400)
        img = np.broadcast_to(ramp[None, :, None], (100, 100, 3)).astype(np.uint16).copy()
        expected = img.copy()
        cv2.circle(img, (50, 50), 4, (60000, 60000, 60000), -1)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}
        out = apply_dust_removal(img, [spot])
        yy, xx = np.mgrid[0:100, 0:100]
        hole = np.hypot(yy - 50, xx - 50) <= 8
        err = np.abs(out[hole].astype(np.float32) - expected[hole].astype(np.float32))
        assert float(err.max()) < 2500

    def test_fallback_still_fills_when_no_clean_source(self):
        # A spot so large no clean source window exists anywhere must fall
        # back to diffusion inpainting rather than leaving the speck.
        img = _flat_with_speck(base=30000, speck=60000, cx=50, cy=50, r=30)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.4}
        out = apply_dust_removal(img, [spot])
        assert int(out[50, 50, 0]) < 45000  # moved toward the surround

    def test_a_stroke_samples_within_reach(self):
        # The source search (spec/dust-heal-ring-match.md, 2026-11-23) looks
        # only within 4 x a piece's size: every automatic sample is near its
        # patch (the old rule demanded touching; it broke lines and pasted
        # discs of whatever touched the spot).
        sky = np.array([20000, 25000, 40000], np.float32)
        img = np.broadcast_to(sky.astype(np.uint16), (200, 200, 3)).copy()
        cv2.line(img, (30, 100), (170, 100), (62000, 60000, 30000), 13)
        spot = {"kind": "brush",
                "pts": [[0.2, 0.5], [0.5, 0.5], [0.8, 0.5]], "r": 2.0 / 200}
        plan = []
        apply_dust_removal(img, [spot], collect_plan=plan)
        assert plan
        for cy, cx, off, *_ in plan:
            if off is None:
                continue
            # within reach of the piece (2 px brush, pieces up to 32 px)
            assert float(np.hypot(off[0] * 200, off[1] * 200)) <= _reach(16)
        # And a stroke that COVERS the hair heals it to sky from the
        # touching sky on either side.
        wide = {"kind": "brush",
                "pts": [[0.2, 0.5], [0.5, 0.5], [0.8, 0.5]], "r": 9.0 / 200}
        out = apply_dust_removal(img, [wide])
        core = out[99:102, 60:140].astype(np.float32).reshape(-1, 3)
        err = np.abs(core.mean(axis=0) - sky)
        assert float(err.max()) < 6000   # covered defect heals to sky

    def test_feather_alpha_ramps_inward(self):
        # The fill blends over a smooth inward ramp: ~0 at the hole boundary,
        # 1 in the core, exactly 0 outside the mask; a ramp of 0.5 px (the
        # feather-0 convention) is a hard, fully filled edge.
        from core.ccr_processor import _feather_alpha
        mask = np.zeros((60, 60), np.uint8)
        cv2.circle(mask, (30, 30), 20, 255, -1)
        a = _feather_alpha(mask, np.full((60, 60), 8.0, np.float32))
        assert a[30, 30] == 1.0                    # core fully filled
        assert a[30, 51] == 0.0                    # outside untouched
        edge, mid = a[30, 49], a[30, 45]
        assert 0.0 < edge < 0.35                   # soft start at the rim
        assert edge < mid < 1.0                    # monotone ramp
        hard = _feather_alpha(mask, np.full((60, 60), 0.5, np.float32))
        assert hard[30, 49] > 0.9                  # hard fill at the rim

    def test_feather_param_softens_the_seam_outside(self):
        # Revision 2026-11-23 (spec/dust-feather-outward.md): the Feather
        # setting widens a cross-fade OUTSIDE the brushed area; inside it the
        # heal is complete at every setting. feather is a fraction of the
        # hole's own radius (1.0 = its full depth).
        rng = np.random.default_rng(5)
        img = np.clip(rng.normal(30000, 2000, (200, 200, 3)), 0,
                      65535).astype(np.uint16)
        cv2.circle(img, (100, 100), 6, (60000, 60000, 60000), -1)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}  # mask r=16
        hard = apply_dust_removal(img, [spot], feather=0.0)
        soft = apply_dust_removal(img, [spot], feather=1.0)
        yy, xx = np.mgrid[0:200, 0:200]
        d = np.hypot(yy - 100, xx - 100)
        outside = (d > 17.5) & (d < 22)
        assert np.array_equal(hard[d > 17.5], img[d > 17.5])   # hard: nothing outside
        d_soft = np.abs(soft[outside].astype(np.float32) - img[outside]).mean()
        assert d_soft > 200                                     # soft: a seam blend
        inside = d < 15.5
        assert np.array_equal(soft[inside], hard[inside])       # the heal itself: the same
        assert int(hard[100, 100, 0]) < 40000
        assert int(soft[100, 100, 0]) < 40000

    def test_full_feather_fades_out_smoothly_outside(self):
        # 100% feather: full heal inside the brush, then a smooth fade outward
        # — falling monotonically with distance, with no isolated
        # full-strength pixels at its outer edge.
        rng = np.random.default_rng(3)
        img = np.clip(rng.normal(30000, 3000, (200, 200, 3)), 0,
                      65535).astype(np.uint16)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.1}  # r_px = 20
        out = apply_dust_removal(img, [spot], feather=1.0)
        diff = np.abs(out.astype(np.float32) - img.astype(np.float32)).mean(axis=2)
        yy, xx = np.mgrid[0:200, 0:200]
        d = np.hypot(yy - 100, xx - 100)
        core = float(diff[d < 18].mean())
        bands = [float(diff[(d >= a) & (d < b)].mean())
                 for a, b in ((21, 26), (26, 31), (31, 36), (36, 41))]
        assert all(bands[i] > bands[i + 1] for i in range(len(bands) - 1))
        assert bands[0] < core
        outer = diff[(d >= 36) & (d < 41)]
        assert float(np.percentile(outer, 99)) < 0.8 * (core + 1e-6)
        assert np.array_equal(out[d > 43], img[d > 43])         # beyond the fade: untouched

    def test_feather_reach_scales_with_brush_size(self):
        # The fade's reach is a fraction of the hole's own radius: at the SAME
        # setting, 8 px outside the rim is still blending for a big brush but
        # past the fade for a small one.
        rng = np.random.default_rng(11)
        base = np.clip(rng.normal(30000, 3000, (300, 300, 3)), 0,
                       65535).astype(np.uint16)

        def reach(r_norm):
            r_px = r_norm * 300
            img = base.copy()
            cv2.circle(img, (150, 150), max(3, int(r_px / 4)),
                       (60000, 60000, 60000), -1)
            spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": r_norm}
            soft = apply_dust_removal(img, [spot], feather=0.5)
            yy, xx = np.mgrid[0:300, 0:300]
            d = np.hypot(yy - 150, xx - 150)
            probe = (d > r_px + 7) & (d < r_px + 9)
            return float(np.abs(soft[probe].astype(np.float32) - img[probe]).mean())

        assert reach(0.12) > 300      # big brush (r 36, fade 18 px): still blending
        assert reach(0.04) == 0.0     # small brush (r 12, fade 6 px): untouched

    def test_feather_never_leaves_the_defect(self):
        # The circles the user saw (2026-11-23): an inward fade kept the
        # brush's outer quarter partly unhealed, and a speck reaching the rim
        # showed as a ring. At every feather the defect is now gone all the
        # way to the brush's edge.
        sky = np.array([20000, 25000, 40000], np.float32)
        img = np.broadcast_to(sky.astype(np.uint16), (200, 200, 3)).copy()
        cv2.circle(img, (100, 100), 10, (62000, 60000, 30000), -1)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.06}  # r = 12 px
        yy, xx = np.mgrid[0:200, 0:200]
        d = np.hypot(yy - 100, xx - 100)
        for feather in (0.0, 0.25, 1.0):
            out = apply_dust_removal(img, [spot], feather=feather).astype(np.float32)
            rim = out[(d > 8) & (d < 10.5)]          # the defect's edge, inside the brush
            assert float(np.abs(rim.mean(axis=0) - sky).max()) < 1500, feather
            core = out[92:109, 92:109].reshape(-1, 3)
            assert float(np.abs(core.mean(axis=0) - sky).max()) < 1500, feather

    def test_the_outward_alpha(self):
        from core.ccr_processor import _feather_alpha_out
        comp = np.zeros((60, 60), bool)
        cv2.circle(comp.view(np.uint8), (30, 30), 10, 1, -1)
        comp = comp.astype(bool)
        a = _feather_alpha_out(comp, np.full((60, 60), 6.0, np.float32))
        assert a[30, 30] == 1.0 and a[30, 40] == 1.0           # the whole hole: 1
        out = [float(a[30, 41 + k]) for k in range(8)]
        assert all(out[i] >= out[i + 1] for i in range(7))      # falls outward
        assert 0.0 < out[0] < 1.0 and out[-1] < 0.05 and a[30, 52] == 0.0
        hard = _feather_alpha_out(comp, np.full((60, 60), 0.5, np.float32))
        assert hard[30, 41] == 0.0                               # feather 0: a hard edge


    def test_big_merged_dabs_clone_texture_not_smear(self):
        # Big overlapping dabs used to become ONE segment whose source
        # window (bbox + thickness-scaled pad) could not fit anywhere clean,
        # dumping the whole blob into the flat diffusion fallback — a
        # visible grainless disc on film. The segment-size cap tiles the
        # blob so each tile clones real texture from beside it.
        rng = np.random.default_rng(21)
        img = np.clip(rng.normal(32000, 3000, (640, 640, 3)), 0,
                      65535).astype(np.uint16)
        # A merged blob whose ONE-segment window has no clean in-bounds home
        # in this frame, while per-tile windows do.
        spots = [{"kind": "brush", "pts": [[0.42, 0.40]], "r": 0.075},
                 {"kind": "brush", "pts": [[0.55, 0.38]], "r": 0.075},
                 {"kind": "brush", "pts": [[0.48, 0.52]], "r": 0.07}]
        out = apply_dust_removal(img, spots)
        mask = rasterize_dust_mask(spots, 640, 640) > 0
        core = cv2.erode(mask.astype(np.uint8), np.ones((25, 25), np.uint8)) > 0
        healed = out[core].astype(np.float32)
        ann = (~mask) & (cv2.dilate(mask.astype(np.uint8),
                                    np.ones((41, 41), np.uint8)) > 0)
        surround = out[ann].astype(np.float32)
        # Tone lands on the surround AND grain survives (Telea ~ 0 std).
        assert abs(float(healed.mean()) - float(surround.mean())) < 2000
        assert float(healed.std()) > 0.5 * float(surround.std())

    def test_prior_plan_pins_sources_verbatim(self):
        # Once a patch's source is set, nothing may move it: the previous
        # plan seeds every re-heal and matching segments reuse its offset
        # VERBATIM (no re-scoring). Proof by tampering: a shifted-but-clean
        # prior offset must drive the heal and be carried into the new plan.
        # (The record carries the current rule's tag: an automatic pin from
        # the old touching rule is searched again,
        # spec/dust-heal-ring-match.md.)
        rng = np.random.default_rng(17)
        img = np.clip(rng.normal(30000, 3000, (400, 400, 3)), 0,
                      65535).astype(np.uint16)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
        plan1 = []
        apply_dust_removal(img, [spot], collect_plan=plan1)
        (cy, cx, off, g, d, *_), = plan1
        assert off is not None
        from core import ccr_processor as cp
        tampered = [(cy, cx, (off[0], off[1] + 3.0 / 400.0), g, d, False, cp._HEAL_RULE)]
        plan2 = []
        apply_dust_removal(img, [spot], collect_plan=plan2,
                           prior_plan=tampered)
        assert plan2[0][2][1] == pytest.approx(tampered[0][2][1])

    def test_new_stroke_keeps_prior_sources_even_in_its_ring(self):
        # A stroke painted inside an existing segment's context ring used to
        # change that segment's matching inputs and could flip its source;
        # with the prior plan pinning sources, the first stroke's sample
        # stays bit-identical (only a stroke ON the source itself may force
        # a re-search).
        rng = np.random.default_rng(17)
        img = np.clip(rng.normal(30000, 3000, (400, 400, 3)), 0,
                      65535).astype(np.uint16)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
        plan1 = []
        apply_dust_removal(img, [spot], collect_plan=plan1)
        (cy, cx, off, _, _, *_), = plan1
        # Perturb the ring on the side OPPOSITE the chosen source, so the
        # prior source window stays clean.
        bx = 0.44 if (cx + off[1]) >= 0.5 else 0.56
        b = {"kind": "brush", "pts": [[bx, 0.5]], "r": 0.01}
        plan2 = []
        apply_dust_removal(img, [spot, b], collect_plan=plan2,
                           prior_plan=plan1)
        recs = [r for r in plan2
                if abs(r[0] - cy) < 1e-9 and abs(r[1] - cx) < 1e-9]
        assert recs, "first stroke's segment disappeared"
        assert recs[0][2] == off, "first stroke's source moved"

    def test_search_prefers_the_strokes_surroundings(self):
        # On content where every direction matches equally well (uniform
        # grain), the pick must come from the NEAREST candidate ring: the
        # sweep runs nearest-first and stops once the best match reaches the
        # grain floor, instead of letting a lucky far patch win the SSD by
        # noise.
        rng = np.random.default_rng(3)
        img = np.clip(rng.normal(30000, 3000, (400, 400, 3)), 0,
                      65535).astype(np.uint16)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
        plan = []
        apply_dust_removal(img, [spot], collect_plan=plan)
        (cy, cx, off, *_), = plan
        assert off is not None
        # r = 12 px -> guard 6, ring 9, pad 15 -> d_base = 41 px.
        dist = float(np.hypot(off[0] * 400, off[1] * 400))
        assert dist <= 1.6 * 41

    def test_stripes_defect_removed_from_a_near_sample(self):
        # The source search matches the surroundings (the ring), so on
        # stripes the fill comes from a nearby patch in phase; what must
        # hold: the defect is gone and the sample is within reach.
        yy, xx = np.mgrid[0:200, 0:200]
        base = 30000 + 12000 * np.sin(2 * np.pi * xx / 24.0)
        img = np.stack([base] * 3, axis=-1).astype(np.uint16)
        cv2.circle(img, (100, 100), 5, (62000, 62000, 62000), -1)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.05}
        plan = []
        out = apply_dust_removal(img, [spot], feather=0.0, collect_plan=plan)
        (cy, cx, off, *_), = plan
        assert off is not None
        assert float(np.hypot(off[0] * 200, off[1] * 200)) <= _reach(10)
        hole = np.hypot(yy - 100, xx - 100) <= 8
        healed = out[hole].astype(np.float32)
        assert float(healed.max()) < 50000       # the bright speck is gone
        assert 15000 < float(healed.mean()) < 45000  # stripe-range content

    def test_manual_source_pick_clones_verbatim(self):
        # A USER-PICKED source (overlay ring drag, manual=True in the plan)
        # is cloned verbatim — color AND texture. The membrane re-toning is
        # for automatic picks only: applied to a manual re-pick it kept the
        # destination's old color and toned away the picked look ("texture
        # is gone but color remains").
        rng = np.random.default_rng(41)
        img = np.clip(rng.normal(30000, 3000, (400, 400, 3)), 0,
                      65535).astype(np.uint16)
        img[150:260, 100:300] = np.clip(
            rng.normal(12000, 500, (110, 200, 3)), 0, 65535)  # dark region
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.075}  # inside it
        plan1 = []
        apply_dust_removal(img, [spot], collect_plan=plan1)
        # Re-pick every segment's source to the bright grain 120 px above.
        manual = [(cy, cx, (-120.0 / 400.0, 0.0), g, d, True)
                  for cy, cx, off, g, d, *_ in plan1]
        out = apply_dust_removal(img, [spot], prior_plan=manual)
        m = rasterize_dust_mask([spot], 400, 400) > 0
        core = cv2.erode(m.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0
        healed = out[core].astype(np.float32)
        # The fill carries the SOURCE's tone and grain, not the dark ring's.
        assert abs(float(healed.mean()) - 30000) < 2500
        assert float(healed.std()) > 1500

    def test_sources_stay_inside_the_crop(self):
        # Content outside the confirmed crop (film holder, rebate, junk the
        # user cut away) is not scene: the pixels a heal READS must never
        # come from it. The source WINDOW may overlap it with its unused
        # corners (pixels-actually-read semantics — same rule that keeps
        # sources near inside dust clusters), but the fill projection stays
        # in-crop and matching/tone anchor only on the ring's clean subset,
        # so none of the junk's brightness can reach the output.
        rng = np.random.default_rng(29)
        img = np.clip(rng.normal(30000, 2500, (400, 400, 3)), 0,
                      65535).astype(np.uint16)
        img[:, 260:] = 62000  # junk strip the crop removes
        crop = ((0.0, 0.0, 0.65, 1.0), 0.0)  # keeps x < 260 px
        spot = {"kind": "brush", "pts": [[0.60, 0.5]], "r": 0.03}
        plan = []
        out = apply_dust_removal(img, [spot], collect_plan=plan, crop=crop)
        assert plan
        # Every FILL projection lies inside the crop (hole r 12 px).
        for cy, cx, off, *_ in plan:
            if off is not None:
                assert (cx + off[1]) * 400 + 12 <= 260 + 1e-6
        # The fill is sky, not the junk strip — tone anchored on the clean
        # ring subset, junk excluded from the membrane.
        m = rasterize_dust_mask([spot], 400, 400) > 0
        healed = out[m].astype(np.float32)
        assert abs(float(healed.mean()) - 30000) < 3000
        assert float(healed.max()) < 50000

    def test_stroke_on_prior_source_stays_separate_and_heals_clean(self):
        # MAINTAINER RULE: two strokes are NEVER auto-connected — even when
        # one is painted right on the other's touching source, each patch
        # keeps its own segments and samples separately. The first stroke's
        # pinned source now sits under the new stroke's dust, so it defers
        # and clones the HEALED content at the same touching offset; the new
        # stroke argmins its own touching sample. No dust is ever cloned.
        rng = np.random.default_rng(23)
        img = np.clip(rng.normal(30000, 2500, (400, 400, 3)), 0,
                      65535).astype(np.uint16)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
        plan1 = []
        apply_dust_removal(img, [spot], collect_plan=plan1)
        (cy, cx, off, _, _, *_), = plan1
        assert off is not None
        # Touching: the chosen source borders the patch.
        assert float(np.hypot(off[0] * 400, off[1] * 400)) <= 2 * 12 + 4
        # A bright defect appears at the chosen source and the user paints
        # over it.
        sy, sx = cy + off[0], cx + off[1]
        img2 = img.copy()
        cv2.circle(img2, (int(sx * 400), int(sy * 400)), 5,
                   (62000, 62000, 62000), -1)
        b = {"kind": "brush", "pts": [[sx, sy]], "r": 0.03}
        plan2 = []
        out = apply_dust_removal(img2, [spot, b], collect_plan=plan2,
                                 prior_plan=plan1)
        # Two separate patches -> (at least) two records, one per stroke.
        assert len(plan2) >= 2
        # No dust cloned anywhere; background restored in both fills.
        m = rasterize_dust_mask([spot, b], 400, 400) > 0
        healed = out[m].astype(np.float32)
        assert float(healed.mean()) < 40000
        assert float(healed.max()) < 55000

    def test_touching_strokes_sample_separately(self):
        # MAINTAINER RULE, verbatim: "do not auto connect 2 strokes — sample
        # separately." Two overlapping dabs must remain two patches, each
        # with its own record and its own sample; the union used to
        # merge into one component healed as a single bigger patch.
        rng = np.random.default_rng(12)
        img = np.clip(rng.normal(30000, 2000, (300, 300, 3)), 0,
                      65535).astype(np.uint16)
        a = {"kind": "brush", "pts": [[0.45, 0.5]], "r": 0.03}   # r = 9 px
        b = {"kind": "brush", "pts": [[0.50, 0.5]], "r": 0.03}   # overlaps a
        plan = []
        apply_dust_removal(img, [a, b], collect_plan=plan)
        assert len(plan) == 2                    # one record per stroke
        for (cy, cx, off, *_), s in zip(plan, (a, b)):
            assert off is not None
            # Each record's segment lies on its own stroke...
            assert abs(cx - s["pts"][0][0]) < 0.03
            # ...and each sample is within reach of ITS patch.
            assert float(np.hypot(off[0] * 300, off[1] * 300)) <= _reach(9)

    def test_new_stroke_keeps_far_samples_stable(self):
        # Adding a dab must only re-sample segments near the edit. In the
        # app this is the STICKY-SOURCES invariant: the panel always passes
        # the cached plan as prior_plan, so unchanged segments rebind their
        # offsets verbatim (the touch rule shares ONE offset per patch for
        # fresh picks, so a no-prior re-derivation of a merged component
        # legitimately re-seeds — priors are what pin committed strokes).
        rng = np.random.default_rng(31)
        img = np.clip(rng.normal(32000, 3000, (640, 640, 3)), 0,
                      65535).astype(np.uint16)
        base = [{"kind": "brush", "pts": [[0.45, 0.45]], "r": 0.075},
                {"kind": "brush", "pts": [[0.58, 0.45]], "r": 0.075}]
        plan1, plan2 = [], []
        apply_dust_removal(img, base, collect_plan=plan1)
        # A small dab overlapping the blob's top-left corner: extends the
        # component bbox origin (0.38*640-19 px < the blob's old 240 px
        # edge) and its thickness, touches nothing on the far (right) side.
        added = base + [{"kind": "brush", "pts": [[0.38, 0.38]], "r": 0.03}]
        apply_dust_removal(img, added, collect_plan=plan2, prior_plan=plan1)
        far1 = [r for r in plan1 if r[1] > 0.55]
        assert far1
        for rec in far1:
            match = [r for r in plan2
                     if abs(r[0] - rec[0]) < 1e-9 and abs(r[1] - rec[1]) < 1e-9]
            assert match, f"far segment moved: {rec[:2]}"
            assert match[0][2] == rec[2], "far segment re-sampled"

    def test_clean_stroke_near_black_border_stays_sky(self):
        # THE black-blob artifact: a generous brush over clean sky near the
        # frame's black holder border. The defect estimate collapses onto
        # the sky itself, the ring rejection then discarded the whole sky
        # ring as "leak" and kept the black border as the only matching and
        # tone anchor — the stroke healed to solid black, cloned from a
        # window overlapping the border.
        rng = np.random.default_rng(9)
        img = np.clip(rng.normal(34000, 2500, (200, 300, 3)), 0,
                      65535).astype(np.uint16)
        img[:14] = np.clip(rng.normal(900, 300, (14, 300, 3)), 0, 65535)
        spot = {"kind": "brush",
                "pts": [[0.30, 0.22], [0.40, 0.20], [0.50, 0.22]],
                "r": 0.055}  # ~17 px dabs, ~30 px below the border
        out = apply_dust_removal(img, [spot], feather=0.25)
        mask = rasterize_dust_mask([spot], 200, 300) > 0
        healed = out[mask].astype(np.float32).mean(axis=1)
        # Nothing near black anywhere in the fill; tone stays sky-like.
        assert float(np.percentile(healed, 1)) > 20000
        assert abs(float(healed.mean()) - 34000) < 4000

    def test_underscoped_dab_keeps_tone(self):
        # A click smaller than the speck (the small dot ghost): the speck's
        # edge leaks past the mask but must not lift the fill's tone.
        sky = np.array([20000, 25000, 40000], np.float32)
        img = np.broadcast_to(sky.astype(np.uint16), (100, 100, 3)).copy()
        cv2.circle(img, (50, 50), 6, (62000, 60000, 30000), -1)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.04}  # mask r=4 < speck r=6
        out = apply_dust_removal(img, [spot])
        center = out[49:52, 49:52].astype(np.float32).reshape(-1, 3).mean(axis=0)
        assert float(np.abs(center - sky).max()) < 6000


# --- apply_adjustments integration (dust runs before the early-return guard) -
class TestApplyAdjustmentsIntegration:
    def test_dust_only_image_still_inpaints(self, tmp_path):
        # Build a bare CCRImage; neutralize bases so apply_adjustments takes the
        # early-return path AFTER dust removal (proving dust runs before it).
        path = str(tmp_path / "x.png")
        cv2.imwrite(path, np.zeros((10, 10, 3), np.uint8))
        img = CCRImage(path)
        img.adjustment_settings = {}
        img.contrast_base = 0
        img.temperature_base = 0
        img.brightness_base = 0
        img.color_profile = "color"
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.08}]

        src = _flat_with_speck()
        out = img.apply_adjustments(src)
        # Speck healed even though every slider/base is neutral.
        assert int(out[50, 50, 0]) < 55000
        assert np.array_equal(out[0:10, 0:10], src[0:10, 0:10])

    def test_no_dust_no_change_in_neutral_pipeline(self, tmp_path):
        path = str(tmp_path / "y.png")
        cv2.imwrite(path, np.zeros((10, 10, 3), np.uint8))
        img = CCRImage(path)
        img.adjustment_settings = {}
        img.contrast_base = img.temperature_base = img.brightness_base = 0
        img.dust_spots = []
        src = _flat_with_speck()
        out = img.apply_adjustments(src)
        assert np.array_equal(out, src)


# --- dust_detect.prob_to_spots (model-free) ---------------------------------
def _bright_luma(prob):
    """Luma where high-prob regions read bright (white film dust) on a dark
    surround, so detections pass the bright-speck gate."""
    return np.clip(prob, 0.0, 1.0).astype(np.float32)


class TestProbToSpots:
    def test_threshold_direction(self):
        prob = np.zeros((100, 100), np.float32)
        prob[10:13, 10:13] = 0.5            # a small mid-confidence blob
        luma = _bright_luma(prob)
        # sensitivity 0 -> thr 0.85 -> nothing; 100 -> thr 0.25 -> detected.
        assert dust_detect.prob_to_spots(prob, luma, 0) == []
        spots = dust_detect.prob_to_spots(prob, luma, 100)
        assert len(spots) == 1
        assert spots[0]["kind"] == "auto"
        x, y = spots[0]["pts"][0]
        assert 0.08 < x < 0.16 and 0.08 < y < 0.16
        assert spots[0]["r"] > 0

    def test_size_gate_drops_large_blobs(self):
        # max_blob = MAX_BLOB(400) * max(h,w) / 2000 = 20 px for a 100x100 map.
        prob = np.zeros((100, 100), np.float32)
        prob[10:13, 10:13] = 0.9            # 9 px  -> kept
        prob[50:62, 50:62] = 0.9            # 144 px -> dropped
        spots = dust_detect.prob_to_spots(prob, _bright_luma(prob), 50)
        assert len(spots) == 1
        x, y = spots[0]["pts"][0]
        assert x < 0.3 and y < 0.3          # the small blob, not the big one

    def test_blank_prob_yields_nothing(self):
        z = np.zeros((40, 40), np.float32)
        assert dust_detect.prob_to_spots(z, z, 100) == []

    def test_drops_elongated_keeps_compact(self):
        # A thin line is real image structure (bike frame / horizon), not dust:
        # the aspect filter drops it while keeping a compact speck. Guards the
        # AI-artifact fix (see spec/dust-removal.md §5.3/§5.4).
        prob = np.zeros((100, 100), np.float32)
        prob[50, 10:22] = 0.9       # 1x12 thin line (area 12, aspect 12) -> dropped
        prob[80:83, 80:83] = 0.9    # 3x3 compact speck (area 9)          -> kept
        spots = dust_detect.prob_to_spots(prob, _bright_luma(prob), 60)
        assert len(spots) == 1
        x, y = spots[0]["pts"][0]
        assert x > 0.5 and y > 0.5  # the compact speck, not the line

    def test_radius_is_area_equivalent_not_extent(self):
        # A 6x6 compact blob -> radius ~ sqrt(36/pi)*SPOT_SCALE (+pad), NOT
        # the old bounding-box sizing blown up by elongation. The scale covers
        # a soft speck's faint skirt; the area base keeps it from smudging.
        prob = np.zeros((200, 200), np.float32)
        prob[100:106, 100:106] = 0.9
        spots = dust_detect.prob_to_spots(prob, _bright_luma(prob), 60)
        assert len(spots) == 1
        r_px = spots[0]["r"] * 200
        expected = np.sqrt(36 / np.pi) * dust_detect.SPOT_SCALE + dust_detect.SPOT_PAD
        assert abs(r_px - expected) < 0.5
        assert r_px < 2.0 * 6.0     # still circle-sized, no big smudge

    def test_bright_gate_drops_dark_blob(self):
        # Film dust inverts to WHITE specks. A compact, right-sized blob that is
        # DARKER than its surround (e.g. a face on bright sky) is NOT dust and
        # must be rejected — this is what removed a person's head before.
        # 200x200 so the 6x6 blob clears the size gate (max_blob ~ 40 here) and
        # the brightness gate is what actually decides.
        prob = np.zeros((200, 200), np.float32)
        prob[40:46, 40:46] = 0.9                 # detector fires on a 6x6 region
        dark = np.full((200, 200), 0.8, np.float32)
        dark[40:46, 40:46] = 0.2                 # dark blob, bright surround
        assert dust_detect.prob_to_spots(prob, dark, 60) == []
        bright = np.full((200, 200), 0.2, np.float32)
        bright[40:46, 40:46] = 0.9               # bright blob (real dust)
        assert len(dust_detect.prob_to_spots(prob, bright, 60)) == 1

    def test_soft_dust_passes_on_smooth_surround_only(self):
        # Dust sits off the focal plane, so real specks are soft and FAINT: a
        # +0.03 lift is many grain-sigmas above smooth sky (kept) but lost in
        # the texture of a busy surround (rejected). The bright gate is
        # noise-relative — a fixed absolute margin missed nearly every soft
        # speck on smooth sky (the example_raw dusty-sky case).
        rng = np.random.default_rng(5)
        prob = np.zeros((200, 200), np.float32)
        prob[40:46, 40:46] = 0.9
        smooth = np.clip(rng.normal(0.75, 0.005, (200, 200)), 0, 1).astype(np.float32)
        smooth[40:46, 40:46] += 0.03
        assert len(dust_detect.prob_to_spots(prob, smooth, 60)) == 1
        busy = np.clip(rng.normal(0.75, 0.04, (200, 200)), 0, 1).astype(np.float32)
        busy[40:46, 40:46] += 0.03
        assert dust_detect.prob_to_spots(prob, busy, 60) == []

    def test_texture_rule_rejects_even_sharp_glints(self):
        # Auto-detection is sky/shadow ONLY (maintainer rule): in busy texture
        # a compact bright glint is indistinguishable from dust and healing it
        # eats real detail, so even a SHARP, high-lift blob is rejected when
        # its surround ring is textured. Manual brush territory.
        rng = np.random.default_rng(9)
        prob = np.zeros((200, 200), np.float32)
        prob[40:46, 40:46] = 0.9
        busy = np.clip(rng.normal(0.4, 0.06, (200, 200)), 0, 1).astype(np.float32)
        busy[40:46, 40:46] = 0.95              # huge lift — still rejected
        assert dust_detect.prob_to_spots(prob, busy, 60) == []


# --- AUTO sampling rule: touch + HSV/texture argmin ---------------------------
class TestAutoSamplingRule:
    """Maintainer rule, verbatim: (1) the auto sample area MUST TOUCH the
    patch area; (2) it samples the touching candidate with the lowest
    combined delta-hue/delta-sat/delta-V vs the patch's own background plus
    the lowest texture; (3) again — the areas must touch. Total selection:
    always an answer, no distance escalation."""

    def test_white_speck_on_light_wall_beside_shadow_band(self):
        # THE reported case: white dust on the light wall right above a dark
        # shadow band. The old ring machinery stripped the light context as
        # "defect leak" and anchored on the band — sampling dark shadow from
        # far away. The rule: sample must touch and match the LIGHT wall.
        rng = np.random.default_rng(5)
        H_, W_ = 220, 270
        img = np.zeros((H_, W_, 3), np.float32)
        img[:, :] = [52000, 51000, 49000]        # light wall
        img[95:160, :] = [17000, 17000, 18000]   # dark shadow band
        img[160:, :] = [46000, 45000, 43000]
        img += rng.normal(0, 1200, img.shape)
        img = np.clip(img, 0, 65535).astype(np.uint16)
        cv2.line(img, (96, 122), (114, 104), (58000,) * 3, 3,
                 lineType=cv2.LINE_AA)                   # nearby white streak
        cv2.circle(img, (123, 88), 3, (62000,) * 3, -1,
                   lineType=cv2.LINE_AA)                 # the white speck
        spot = {"kind": "auto", "pts": [[123 / W_, 88 / H_]], "r": 9.0 / W_}
        plan = []
        out = apply_dust_removal(img, [spot], collect_plan=plan)
        (cy, cx, off, *_), = plan
        assert off is not None
        dy, dx = off
        # (1)+(3) touching: offset ~ 2·half_th + a few px of mask dilation.
        assert float(np.hypot(dy * H_, dx * W_)) <= 2 * 9 + 8
        # (2) the fill is LIGHT WALL, not shadow.
        m = rasterize_dust_mask([spot], H_, W_) > 0
        assert float(out[m].astype(np.float32).mean()) > 40000

    def test_isolated_speck_samples_touching(self):
        rng = np.random.default_rng(8)
        img = np.clip(rng.normal(30000, 2000, (200, 200, 3)), 0,
                      65535).astype(np.uint16)
        spot = {"kind": "auto", "pts": [[0.5, 0.5]], "r": 0.04}  # r = 8 px
        plan = []
        apply_dust_removal(img, [spot], collect_plan=plan)
        (cy, cx, off, *_), = plan
        assert off is not None
        assert float(np.hypot(off[0] * 200, off[1] * 200)) <= 2 * 8 + 8


# --- Stale plan records must never seed new spots ------------------------------
class TestStalePlanPruning:
    def test_deleted_spots_records_never_seed_new_spots(self):
        # THE field bug that made the touch rule look broken: catalog plans
        # saved by OLDER code hold far offsets, the plan cache deliberately
        # outlives spot edits (sticky sources), and a spot repainted or
        # re-detected at a dead record's position rebound the old offset
        # VERBATIM — the sampling rule never ran for the new spot. Records
        # of spots that no longer exist are pruned before the cache seeds a
        # re-heal.
        rng = np.random.default_rng(2)
        img16 = np.clip(rng.normal(30000, 2000, (400, 400, 3)), 0,
                        65535).astype(np.uint16)
        holder = CCRImage.__new__(CCRImage)
        old_spot = {"kind": "auto", "pts": [[0.5, 0.5]], "r": 0.03}
        holder._dust_plan_cache = (
            "stale", [(0.5, 0.5, (120.0 / 400, 100.0 / 400),
                       False, False, False)])       # 156 px fossil offset
        holder._dust_plan_spots = [old_spot]
        # The user cleared/deleted and repainted at (almost) the same place.
        holder.dust_spots = [{"kind": "brush",
                              "pts": [[0.501, 0.499]], "r": 0.03}]
        holder.dust_feather = 0.25
        holder.crop_rect = None
        holder.crop_angle = 0.0
        holder._apply_dust_removal(img16)
        key, plan = holder._dust_plan_cache
        (cy, cx, off, *_), = plan
        assert off is not None
        # Fresh TOUCHING pick, not the resurrected 156 px fossil.
        assert float(np.hypot(off[0] * 400, off[1] * 400)) <= 2 * 12 + 8

    def test_fossil_automatic_pins_repick_under_the_rule(self):
        # Fossil offsets propagate through GENERATIONS of sticky plans (each
        # re-detect rebound the old offset into a fresh, validly-keyed plan
        # for as long as the pre-rule search existed) — no spot bookkeeping
        # can identify them. The rule retroacts instead: an AUTOMATIC pin
        # whose sample no longer touches its patch is dropped and re-picked;
        # a USER re-pick keeps any distance (explicit choice).
        rng = np.random.default_rng(6)
        img16 = np.clip(rng.normal(30000, 2000, (400, 400, 3)), 0,
                        65535).astype(np.uint16)
        spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
        far = (120.0 / 400, 100.0 / 400)
        plan = []
        apply_dust_removal(img16, [spot], collect_plan=plan,
                           prior_plan=[(0.5, 0.5, far, False, False, False)])
        (cy, cx, off, *_), = plan
        assert off is not None
        assert float(np.hypot(off[0] * 400, off[1] * 400)) <= _reach(12)
        plan2 = []
        apply_dust_removal(img16, [spot], collect_plan=plan2,
                           prior_plan=[(0.5, 0.5, far, False, False, True)])
        (cy, cx, off2, *_), = plan2
        assert off2 == far   # the user's own pick is never second-guessed

    def test_surviving_spots_keep_their_pinned_sources(self):
        # The stickiness the pruning must NOT break: a spot that still
        # exists keeps its planned source verbatim across a re-heal.
        rng = np.random.default_rng(4)
        img16 = np.clip(rng.normal(30000, 2000, (400, 400, 3)), 0,
                        65535).astype(np.uint16)
        holder = CCRImage.__new__(CCRImage)
        keep = {"kind": "brush", "pts": [[0.3, 0.3]], "r": 0.03}
        pinned = (25.0 / 400, 0.0)   # within reach (hole r = 12 px)
        from core import ccr_processor as cp
        holder._dust_plan_cache = ("k", [(0.3, 0.3, pinned,
                                          False, False, False, cp._HEAL_RULE)])
        holder._dust_plan_spots = [keep]
        holder.dust_spots = [keep]
        holder.dust_feather = 0.25
        holder.crop_rect = None
        holder.crop_angle = 0.0
        holder._apply_dust_removal(img16)
        key, plan = holder._dust_plan_cache
        (cy, cx, off, *_), = plan
        assert off == pinned


# --- Source tone locality ------------------------------------------------------
class TestSourceMatchesLocalTone:
    def test_edge_spot_source_stays_on_its_own_tone_side(self):
        # The reported case: a speck right beside a high-contrast edge
        # sampled flat DIRT from across the frame — near candidates straddle
        # the edge at imperfect phase, so their per-pixel SSD outweighed the
        # remote patch's wholesale tone mismatch. Area tone (chroma +
        # brightness) now dominates the score and locality is strong: the
        # pick must stay in the immediate area, on the spot's own tone side.
        rng = np.random.default_rng(17)
        img = np.zeros((300, 300, 3), np.float32)
        img[:, :140] = [26000, 22000, 18000]        # brown dirt field
        img[:, 140:] = [48000, 47000, 45000]        # light pavement
        img[:, 137:143] = [61000, 61000, 61000]     # bright edge stripe
        img += rng.normal(0, 1200, img.shape)
        img = np.clip(img, 0, 65535).astype(np.uint16)
        spot = {"kind": "auto", "pts": [[0.55, 0.5]], "r": 0.025}  # pavement
        plan = []
        apply_dust_removal(img, [spot], collect_plan=plan)
        (cy, cx, off, *_), = plan
        assert off is not None
        dy, dx = off
        assert float(np.hypot(dy * 300, dx * 300)) < 70   # immediate area
        assert (cx + dx) * 300 > 143   # never the dirt across the edge


# --- Source search in dust clusters ------------------------------------------
class TestClusterSourceStaysNear:
    def test_cluster_of_specks_still_samples_nearby(self):
        # A dusty sky: neighboring specks surround the healed one, so NO fully
        # clean window exists on the near rings. The old all-or-nothing source
        # check leapt to the far rings for every speck in the cluster; the
        # pixels a heal actually READS are only the hole projection + ring, so
        # windows whose unused corners touch a neighbor stay valid and the
        # source stays in the immediate area.
        rng = np.random.default_rng(3)
        img = np.clip(rng.normal(30000, 2000, (240, 240, 3)), 0,
                      65535).astype(np.uint16)
        spots = [{"kind": "auto", "pts": [[0.5, 0.5]], "r": 0.03}]
        for k in range(10):
            a = 2 * np.pi * k / 10
            spots.append({"kind": "auto",
                          "pts": [[0.5 + 0.18 * np.cos(a),
                                   0.5 + 0.18 * np.sin(a)]],
                          "r": 0.02})
        plan = []
        out = apply_dust_removal(img, spots, collect_plan=plan)
        rec = min(plan, key=lambda r: (r[0] - 0.5) ** 2 + (r[1] - 0.5) ** 2)
        assert rec[2] is not None            # sampled, not diffusion fallback
        dy, dx = rec[2]
        dist = float(np.hypot(dy * 240, dx * 240))
        assert dist < 45                     # immediate area, not a far ring
        # And the fill still lands on the surround's tone (a partial source
        # ring anchors the membrane on its clean subset only).
        core = out[114:126, 114:126].astype(np.float32)
        assert abs(float(core.mean()) - 30000) < 4000


# --- Tiled inference grid (pure) ---------------------------------------------
class TestTileStarts:
    def test_short_dim_is_one_tile(self):
        assert dust_detect._tile_starts(512, 768, 704) == [0]
        assert dust_detect._tile_starts(768, 768, 704) == [0]

    def test_covers_to_the_end_without_remainder_tile(self):
        starts = dust_detect._tile_starts(2000, 768, 704)
        assert starts[0] == 0
        assert starts[-1] == 2000 - 768        # flush with the end
        # Full coverage: consecutive windows overlap (no gaps).
        for a, b in zip(starts, starts[1:]):
            assert b < a + 768


# --- Availability / graceful degradation ------------------------------------
class TestAvailability:
    def test_unavailable_when_onnxruntime_absent(self, monkeypatch):
        # Setting the module to None makes `import onnxruntime` raise ImportError.
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        assert dust_detect.is_available() is False

    def test_model_path_is_under_freeccr(self):
        assert "FreeCCR" in dust_detect.model_path()
        assert dust_detect.model_path().endswith("detector.onnx")


# --- Persistence (catalog) --------------------------------------------------
def _scan_png(tmp_path, name="neg.png", w=120, h=80, seed=3):
    rng = np.random.default_rng(seed)
    img = np.clip(rng.normal(30000, 4000, (h, w, 3)), 0, 65535).astype(np.uint16)
    path = str(tmp_path / name)
    cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


class TestPersistence:
    def test_dust_spots_round_trip(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.25, 0.5], [0.3, 0.55]],
                           "r": 0.01},
                          {"kind": "auto", "pts": [[0.7, 0.2]], "r": 0.004}]
        catalog.update_for_images([img], path=cat)
        restored = catalog.create_images_for_path(path, path=cat)
        assert len(restored) == 1
        assert restored[0].dust_spots == img.dust_spots

    def test_old_entry_without_dust_defaults_empty(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.adjustment_settings = {"exposure": 5}
        state = catalog.serialize_image(img)
        del state["dust_spots"]            # simulate a pre-feature catalog entry
        restored = catalog._restore_image(path, state)
        assert restored.dust_spots == []

    def test_dust_only_image_is_not_pristine(self, tmp_path):
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]
        state = catalog.serialize_image(img)
        assert catalog._is_pristine(state) is False

    def test_dust_plan_round_trips_in_catalog(self, tmp_path):
        # The heal plan persists with the spots and reseeds the cache on
        # restore, so a reload keeps the exact sources the user saw (a
        # from-scratch replan of an incrementally-built plan could differ).
        cat = str(tmp_path / "catalog.json")
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.02}]
        img.update_thumbnail_and_preview()   # preview heal caches the plan
        cached = img._dust_plan_cache
        assert cached is not None and cached[0] == repr(img.dust_spots)
        assert cached[1]
        catalog.update_for_images([img], path=cat)
        restored = catalog.create_images_for_path(path, path=cat)[0]
        rc = getattr(restored, "_dust_plan_cache", None)
        assert rc is not None
        assert rc[0] == repr(restored.dust_spots)
        assert rc[1] == cached[1]

    def test_dust_feather_round_trips_and_defaults(self, tmp_path):
        cat = str(tmp_path / "catalog.json")
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]
        img.dust_feather = 0.7
        catalog.update_for_images([img], path=cat)
        restored = catalog.create_images_for_path(path, path=cat)
        assert abs(restored[0].dust_feather - 0.7) < 1e-9
        # Pre-feature catalog entries restore to the default.
        state = catalog.serialize_image(img)
        del state["dust_feather_r"]
        old = catalog._restore_image(path, state)
        assert abs(old.dust_feather - DUST_FEATHER_DEFAULT) < 1e-9
        # Legacy width-fraction entries (old "dust_feather" key, 0..1% of
        # width) migrate by slider position onto the radius-fraction scale:
        # 0.30% of width -> 30% of radius.
        state["dust_feather"] = 0.003
        legacy = catalog._restore_image(path, state)
        assert abs(legacy.dust_feather - 0.3) < 1e-9


# --- Undo -------------------------------------------------------------------
class TestUndo:
    def test_capture_and_pop_restore_dust_spots(self, tmp_path):
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]
        img.push_undo_state()
        # Mutate after snapshot.
        img.dust_spots.append({"kind": "auto", "pts": [[0.1, 0.1]], "r": 0.005})
        assert len(img.dust_spots) == 2
        assert img.pop_undo_state()
        assert img.dust_spots == [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]

    def test_snapshot_is_independent_deep_copy(self, tmp_path):
        path = _scan_png(tmp_path)
        img = CCRImage(path)
        img.dust_spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.01}]
        img.push_undo_state()
        # Mutate a NESTED structure in place; the snapshot must not change.
        img.dust_spots[0]["pts"].append([0.6, 0.6])
        img.pop_undo_state()
        assert img.dust_spots[0]["pts"] == [[0.5, 0.5]]


# --- DustRemovalPanel wiring (headless, with stubs) -------------------------
class _StubPreview:
    def __init__(self):
        self.dust_mode = True
        self.current_idx = None
        self.brush = None
        self.source_overlay = None

    def set_dust_brush_size(self, r):
        self.brush = r

    def set_dust_source_overlay(self, on):
        self.source_overlay = on

    def dust_undo_last(self):
        return False

    def dust_clear_all(self):
        return False


class _StubMain:
    def __init__(self):
        self.toggled = None

    def toggle_dust_removal(self, on):
        self.toggled = on


class TestPanelWiring:
    def test_panel_builds_without_onnxruntime(self):
        from widgets.dust_panel import DustRemovalPanel
        # Building the panel must never import onnxruntime / raise.
        panel = DustRemovalPanel(_StubMain(), _StubPreview())
        assert panel is not None

    def test_brush_slider_drives_canvas(self):
        from widgets.dust_panel import (DustRemovalPanel, brush_r_to_slider,
                                        slider_to_brush_r)
        prev = _StubPreview()
        panel = DustRemovalPanel(_StubMain(), prev)
        panel._on_brush_changed(brush_r_to_slider(0.030))
        # Log-step quantization: nearest step is within ~1% of the target r.
        assert abs(prev.brush - 0.030) < 0.030 * 0.015
        panel.sync_brush_size(0.05)            # canvas -> slider, no feedback loop
        assert panel.brush_slider.value() == brush_r_to_slider(0.05)
        # Mapping round-trips through the slider's integer steps.
        v = brush_r_to_slider(0.012)
        assert brush_r_to_slider(slider_to_brush_r(v)) == v

    def test_brush_reaches_fine_sizes(self):
        # The slider bottom is 0.05% of image width (~3 px radius on a 6000 px
        # scan) — finer than the old 0.2% floor; the 20% top is unchanged.
        from widgets.dust_panel import (DustRemovalPanel, BRUSH_STEPS,
                                        DUST_BRUSH_R_MIN, DUST_BRUSH_R_MAX)
        assert DUST_BRUSH_R_MIN <= 0.0005
        prev = _StubPreview()
        panel = DustRemovalPanel(_StubMain(), prev)
        assert panel.brush_slider.minimum() == 0
        assert panel.brush_slider.maximum() == BRUSH_STEPS
        panel._on_brush_changed(0)
        assert abs(prev.brush - DUST_BRUSH_R_MIN) < 1e-12
        panel._on_brush_changed(BRUSH_STEPS)
        assert abs(prev.brush - DUST_BRUSH_R_MAX) < 1e-12

    def test_done_button_exits_mode(self):
        from widgets.dust_panel import DustRemovalPanel
        main = _StubMain()
        panel = DustRemovalPanel(main, _StubPreview())
        panel._on_done()
        assert main.toggled is False

    def test_cancel_and_shutdown_are_safe_with_no_jobs(self):
        from widgets.dust_panel import DustRemovalPanel
        panel = DustRemovalPanel(_StubMain(), _StubPreview())
        panel.cancel_jobs()
        panel.shutdown()   # must not raise with no threads running

    def test_feather_slider_writes_image_and_label(self):
        from widgets.dust_panel import DustRemovalPanel
        from core.ccr_backend import ccr_backend

        class _Img:
            dust_feather = 0.25
            dust_spots = []

        img = _Img()
        saved = ccr_backend.images
        ccr_backend.images = [img]
        try:
            prev = _StubPreview()
            prev.current_idx = 0
            panel = DustRemovalPanel(_StubMain(), prev)
            panel.feather_slider.setValue(60)  # emits valueChanged
            assert panel.feather_value.text() == "60%"
            panel._apply_feather()             # bypass the debounce timer
            assert abs(img.dust_feather - 0.6) < 1e-9
        finally:
            ccr_backend.images = saved

    def test_show_sources_checkbox_drives_canvas_overlay(self):
        from widgets.dust_panel import DustRemovalPanel
        prev = _StubPreview()
        panel = DustRemovalPanel(_StubMain(), prev)
        panel.show_sources_cb.setChecked(True)
        assert prev.source_overlay is True
        panel.show_sources_cb.setChecked(False)
        assert prev.source_overlay is False

    def test_dust_debug_geometry_maps_plan_to_arrows(self):
        # Full-frame pixel geometry for the 'Show heal sources' overlay:
        # union borders, one arrow per cloned segment (source -> segment,
        # source = centroid + planned offset), a marker per Telea fallback.
        from widgets.image_preview import dust_debug_geometry
        spots = [{"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.05}]
        plan = [(0.5, 0.5, (0.1, -0.2), True, False),
                (0.3, 0.4, None, None, None)]
        geo = dust_debug_geometry(spots, plan, 1000, 500)
        # One border: the dab's circle contour (r = 0.05 * 1000 = 50 px).
        assert len(geo["borders"]) == 1
        pts = np.array(geo["borders"][0])
        d = np.hypot(pts[:, 0] - 500, pts[:, 1] - 250)
        assert float(np.abs(d - 50).max()) < 3
        (sx, sy, dx, dy), = geo["arrows"]
        assert (dx, dy) == (500.0, 250.0)
        assert (sx, sy) == (500.0 - 0.2 * 1000, 250.0 + 0.1 * 500)
        assert geo["fallbacks"] == [(0.4 * 1000, 0.3 * 500)]

    def test_connected_strokes_outline_as_one_border(self):
        # Overlapping strokes read as ONE region: a single union border,
        # not per-spot outlines crossing through the interior.
        from widgets.image_preview import dust_debug_geometry
        merged = [{"kind": "brush", "pts": [[0.50, 0.5]], "r": 0.05},
                  {"kind": "brush", "pts": [[0.56, 0.5]], "r": 0.05}]
        assert len(dust_debug_geometry(merged, None, 1000, 1000)["borders"]) == 1
        apart = [{"kind": "brush", "pts": [[0.3, 0.3]], "r": 0.03},
                 {"kind": "brush", "pts": [[0.7, 0.7]], "r": 0.03}]
        assert len(dust_debug_geometry(apart, None, 1000, 1000)["borders"]) == 2

    def test_dust_spot_hit_and_plan_translation(self):
        # Hit-testing for the right-button stroke editing, and the plan
        # translation that keeps a dragged stroke's ABSOLUTE source.
        from widgets.image_preview import dust_spot_hit, translate_dust_plan
        spot = {"kind": "brush", "pts": [[0.2, 0.2], [0.4, 0.2]], "r": 0.02}
        assert dust_spot_hit(spot, 0.3, 0.2, 1000, 1000)       # on the line
        assert dust_spot_hit(spot, 0.3, 0.218, 1000, 1000)     # within r=20px
        assert not dust_spot_hit(spot, 0.3, 0.25, 1000, 1000)  # 30 px off
        plan = [(0.2, 0.3, (0.05, 0.10), True, False),  # rides the stroke
                (0.8, 0.8, (0.01, 0.02), True, True)]   # elsewhere
        out = translate_dust_plan(plan, spot, 0.1, -0.05, 1000, 1000)
        cy, cx, off = out[0][0], out[0][1], out[0][2]
        assert (cy, cx) == pytest.approx((0.15, 0.4))
        # The record's ABSOLUTE source position is unchanged by the move.
        assert (cy + off[0], cx + off[1]) == pytest.approx((0.25, 0.40))
        assert out[1] == plan[1]

    def test_detect_all_no_targets_is_safe(self):
        from widgets.dust_panel import DustRemovalPanel, _DetectAllWorker  # noqa: F401
        from core.ccr_backend import ccr_backend
        saved = ccr_backend.images
        ccr_backend.images = []
        try:
            panel = DustRemovalPanel(_StubMain(), _StubPreview())
            assert hasattr(panel, "detect_all_btn")
            panel._on_detect_all()                 # no convertible images
            assert panel._detecting_all is False    # no batch started
            assert panel._detect_all_thread is None
        finally:
            ccr_backend.images = saved


# --- Ctrl+Z routing in dust mode ---------------------------------------------
class TestUndoRouting:
    def test_ctrl_z_in_dust_mode_undoes_spot_and_keeps_view(self):
        # In dust mode Ctrl+Z must behave like the panel's "Undo last spot"
        # (view preserved), NOT the general undo whose _reset_zoom read as
        # "Ctrl+Z unzoomed" while spotting dust zoomed-in.
        from ui.main_window import MainWindow

        class _Panel:
            called = False

            def _on_undo_last(self):
                self.called = True

        class _Prev:
            dust_mode = True

            def _reset_zoom(self):
                raise AssertionError("dust-mode undo must preserve the view")

        class _Stub:
            image_preview = _Prev()
            dust_panel = _Panel()

        stub = _Stub()
        MainWindow.undo_last_action(stub)
        assert stub.dust_panel.called is True


# --- Resolution consistency (preview heal == export heal) --------------------
class TestResolutionConsistency:
    """The heal PLAN (segmentation, source patches, fallbacks) is computed at
    the canonical preview scale and replayed on larger buffers, so the
    full-res export reproduces the preview's healing structure. Re-deriving
    the plan per resolution picked visibly different source patches ("the
    preview and the final don't look the same")."""

    W, H = 3000, 2000

    @classmethod
    def _scene(cls):
        """Structured content where source-patch flips are visible: a strong
        diagonal edge, a striped band, and film grain (deterministic)."""
        rng = np.random.default_rng(11)
        yy, xx = np.mgrid[0:cls.H, 0:cls.W].astype(np.float32)
        base = 22000 + 18000 * (xx / cls.W)
        base = np.where(yy > 0.30 * cls.H + 0.25 * xx, base * 0.55, base)
        stripes = 12000 * np.sin(2 * np.pi * xx / 60.0)
        band = (yy > 0.55 * cls.H) & (yy < 0.70 * cls.H)
        img = np.stack([base + np.where(band, stripes, 0)] * 3, axis=-1)
        img += rng.normal(0, 3000, img.shape)
        return np.clip(img, 0, 65535).astype(np.uint16)

    # A hair stroke crossing the striped band and a speck on the diagonal
    # edge — the cases where independent per-resolution planning visibly
    # diverged (different segment sources at preview vs export).
    SPOTS = [{"kind": "brush",
              "pts": [[0.48 + t * 0.04, 0.56 + t * 0.13]
                      for t in np.linspace(0, 1, 30)],
              "r": 0.004},
             {"kind": "brush", "pts": [[0.40, 0.43]], "r": 0.009}]

    def test_export_heal_matches_preview_heal(self):
        big = self._scene()
        # The preview is a downscale of the same content (long side 1080).
        small = cv2.resize(big, (1080, 720), interpolation=cv2.INTER_AREA)
        healed_small = apply_dust_removal(small, self.SPOTS)
        healed_big = apply_dust_removal(big, self.SPOTS)
        big_ds = cv2.resize(healed_big, (1080, 720),
                            interpolation=cv2.INTER_AREA)

        a8 = cv2.convertScaleAbs(healed_small, alpha=255.0 / 65535.0)
        b8 = cv2.convertScaleAbs(big_ds, alpha=255.0 / 65535.0)
        # Windows around the healed hair and the edge speck (1080x720 space).
        # The offsets replay VERBATIM (one shared touching offset per patch;
        # a re-plan derives the identical plan from the identical downscale),
        # so any residual is resampling, not source flips. Recalibrated for
        # the touch+HSV sampling rule: on the STRIPED band the rule has no
        # pattern-phase term, so the touching source is phase-shifted and
        # the fill is discontinuous against its surround — resampling a
        # discontinuous clone between scales measures p99 = 28 / mean = 1.14
        # here (was ~10/0.33 when the old search phase-aligned; a full
        # per-scale re-plan of THIS rule still lands in the same place, the
        # bound guards against replay-path regressions such as reroutes to
        # the deferred pass or tone-anchor subset drift). The smooth-scene
        # speck window stays tight — the rule's normal habitat.
        for (y0, y1, x0, x1), p99_lim, mean_lim in (
                ((390, 510, 490, 590), 32.0, 1.5),   # hair over stripes
                ((270, 350, 390, 480), 9.0, 0.40)):  # edge speck
            d = np.abs(a8[y0:y1, x0:x1].astype(np.float32)
                       - b8[y0:y1, x0:x1].astype(np.float32))
            assert np.percentile(d, 99) <= p99_lim
            assert d.mean() <= mean_lim

    def test_planned_heal_still_removes_dust_at_full_res(self):
        big = self._scene()
        healed = apply_dust_removal(big, self.SPOTS)
        # The dark hair is gone: no strong dark outlier remains along the
        # stroke's path (compare against the local median).
        ys = slice(int(0.54 * self.H), int(0.72 * self.H))
        xs = slice(int(0.46 * self.W), int(0.54 * self.W))
        wnd = healed[ys, xs].astype(np.float32).mean(axis=2)
        med = np.median(wnd)
        mad = np.median(np.abs(wnd - med)) + 1e-3
        assert (med - wnd.min()) / (1.4826 * mad) < 8.0
        # And pixels away from the mask are bit-for-bit untouched.
        assert np.array_equal(healed[:100, :100], big[:100, :100])

    @staticmethod
    def _bare_image(spots):
        """CCRImage skeleton carrying only what _apply_dust_removal reads —
        no file decode, so the plan-cache plumbing is tested hermetically."""
        img = CCRImage.__new__(CCRImage)
        img.dust_spots = spots
        img.dust_feather = 0.25
        img._dust_plan_cache = None
        return img

    def test_image_render_replays_the_preview_plan(self):
        """A preview-scale heal caches its plan; larger renders (hi-res,
        export) must sample what THAT plan says — not a fresh self-plan from
        their own re-decoded pixels (near-tie source flips = "it sampled
        different spots than the preview")."""
        big = self._scene()
        small = cv2.resize(big, (1080, 720), interpolation=cv2.INTER_AREA)
        img = self._bare_image(list(self.SPOTS))

        img._apply_dust_removal(small)                # preview render
        key, plan = img._dust_plan_cache
        assert key == repr(img.dust_spots)
        assert any(rec[2] is not None for rec in plan)

        out_cached = img._apply_dust_removal(big)     # export render
        # Tamper with the cached plan (shift the source offsets): the export
        # output must follow the cache — proof it replays the preview's plan
        # rather than planning for itself.
        img._dust_plan_cache = (key, [
            rec if rec[2] is None
            else (rec[0], rec[1], (rec[2][0] + 0.02, rec[2][1])) + rec[3:]
            for rec in plan])
        out_tampered = img._apply_dust_removal(big)
        assert not np.array_equal(out_cached, out_tampered)

        # A stale key (spots changed since the cached preview) self-plans.
        img.dust_spots = img.dust_spots + [
            {"kind": "brush", "pts": [[0.2, 0.85]], "r": 0.004}]
        out_stale = img._apply_dust_removal(big)
        assert out_stale.shape == big.shape

    def test_explicit_plan_drives_the_sources(self):
        big = self._scene()
        small = cv2.resize(big, (1080, 720), interpolation=cv2.INTER_AREA)
        plan = []
        apply_dust_removal(small, self.SPOTS, collect_plan=plan)
        assert plan
        # Replaying the collected plan reproduces the self-planned result
        # (the self-plan derives from the same content here).
        a = apply_dust_removal(big, self.SPOTS, plan=plan)
        b = apply_dust_removal(big, self.SPOTS)
        assert np.array_equal(a, b)


# --- AI detection source: windowed working-space base ------------------------
class TestDetectSourceDewindow:
    """dust_detect_source_for must hand the detector a DISPLAY-referred
    positive. A bw conversion with working-space headroom stores resized_raw
    in windowed container codes — the whole image sits in the bottom ~1.5% of
    the 16-bit range — so fed raw to the net it is a black frame: the detector
    never fires and the bright-speck gate's absolute margin is unreachable
    ("AI finds no dust" on every windowed image)."""

    @staticmethod
    def _img(base_u16, windowed):
        img = CCRImage.__new__(CCRImage)
        img.resized_raw = base_u16
        img.dust_spots = []
        img.dust_feather = DUST_FEATHER_DEFAULT
        img.crop_rect = None
        img.crop_angle = 0.0
        img._ws_windowed = windowed
        return img

    def test_windowed_base_is_dewindowed_for_detection(self):
        from core.ccr_processor import encode_window
        from widgets.image_preview import ImagePreview
        d = np.full((60, 80, 3), 0.4, dtype=np.float32)
        d[20:24, 30:34] = 1.0                      # white dust speck
        code = encode_window(d.copy())             # windowed container codes
        assert code.max() < 3000                   # near-black fed as-is
        src = ImagePreview.dust_detect_source_for(self._img(code, True))
        lum = src.astype(np.float32) / 65535.0
        assert abs(float(lum[10, 10, 0]) - 0.4) < 0.01  # display tone restored
        assert float(lum[21, 31, 0]) > 0.98             # speck back at white

    def test_full_range_base_passes_through_unchanged(self):
        from widgets.image_preview import ImagePreview
        base = _flat_with_speck()
        src = ImagePreview.dust_detect_source_for(self._img(base, False))
        assert np.array_equal(src, base)


# --- AI detection scope: only inside the confirmed crop ----------------------
class TestAutoSpotsCropScope:
    """AI 'auto' spots are scene fixes: a detection whose center lies outside
    the confirmed crop (film rebate, holder, edge printing) is dropped before
    it is stored — that content is cropped away on screen and in exports, and
    the rebate's bright markings read as dust to the detector."""

    @staticmethod
    def _img(crop_rect=None, crop_angle=0.0):
        img = CCRImage.__new__(CCRImage)
        img.resized_raw = np.zeros((100, 200, 3), np.uint16)
        img.crop_rect = crop_rect
        img.crop_angle = crop_angle
        return img

    @staticmethod
    def _spot(x, y):
        return {"kind": "auto", "pts": [[x, y]], "r": 0.01}

    def test_no_crop_keeps_everything(self):
        from widgets.image_preview import ImagePreview
        spots = [self._spot(0.05, 0.05), self._spot(0.95, 0.95)]
        assert ImagePreview._auto_spots_in_crop(self._img(), spots) == spots

    def test_outside_crop_spots_are_dropped(self):
        from widgets.image_preview import ImagePreview
        img = self._img(crop_rect=(0.25, 0.25, 0.75, 0.75))
        spots = [self._spot(0.5, 0.5),    # inside
                 self._spot(0.05, 0.5),   # left of the crop (rebate)
                 self._spot(0.5, 0.9)]    # below the crop
        assert ImagePreview._auto_spots_in_crop(img, spots) == [spots[0]]

    def test_rotated_crop_uses_the_rotated_footprint(self):
        from widgets.image_preview import ImagePreview
        # 200x100 frame, square 60x60 px crop at the center, rotated 45°:
        # the un-rotated box corner falls OUTSIDE the rotated footprint.
        img = self._img(crop_rect=(0.35, 0.20, 0.65, 0.80), crop_angle=45.0)
        center = self._spot(0.5, 0.5)
        corner = self._spot(0.36, 0.22)
        kept = ImagePreview._auto_spots_in_crop(img, [center, corner])
        assert kept == [center]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
