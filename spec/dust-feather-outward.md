# Dust heals fade OUTWARD from the brushed edge

The user, 2026-11-23: "The dust tool is working better with negatives but the
actual removal leaves unrealistic marks (usually circles)." (Screenshots: thin
light rings round healed spots on sky and wall, at 26 % in dust mode.)

## Cause (measured)

The composite's alpha rose from 0 at each hole's edge to 1 over the Feather
width INWARD (`_feather_alpha`; spec/dust-removal.md §5.2 step 6, "a wide
feather fades the heal out even across the defect itself"). A brush fitted to
a speck (Fit each click to the speck, IR finds grown over the colour shots'
darkness) reaches only just past its soft edge, so the outer quarter of the
brush kept part of that edge, faded: a ring. On a grainy sky with specks
whose halo reaches the brush's rim, healed − clean by radius (8-bit levels):
feather 0 → flat +0.4 (the tone match), 25 % → +1.7 at 0.7–0.9 r, 50 % →
+4.5. At 2160 px with 60 specks, ring minus centre: median +1.44, worst
+2.26.

## Change

- Every brushed pixel is FULLY healed at every Feather setting; the Feather
  is now how far the heal blends out PAST the brushed edge, into clean
  picture: the same width as before (Feather × the hole's own depth, capped
  at the depth), outward.
- `_heal_patch` writes its fill (the same clone + tone membrane) over a band
  `away <= fw + 0.5` outside its hole, clean pixels only (`mask_pad == 0`),
  and records `fw` there in `fmap`.
- The composite uses `_feather_alpha_out`: 1 on every hole pixel, a
  smoothstep from 1 to 0 over each band pixel's `fw` outward (exact Euclidean
  distance: the 3×3 chamfer put the next pixel at 0.955 and leaked 2 % past
  a hard edge), the largest where two regions' fades meet, blended once.
- Patches that fall back to the diffusion fill write no band (their band is
  the original), so they keep a hard edge as before.
- Feather 0 is unchanged (a hard edge, nothing outside touched).

After: ring gone — 25 % gives the flat +0.4 of a hard edge; at 2160 px ring
minus centre median +0.13, worst +1.01.

This replaces the earlier rule that a wide feather fades the heal out across
the defect itself. The slider still acts on every heal (it softens the seam),
which is what that rule was protecting.

## Tests (`tests/test_dust_removal.py`)

Feather softens the seam OUTSIDE and leaves the heal inside identical to a
hard one; a full feather fades monotonically outward and touches nothing
past its reach; the reach scales with the brush; the defect is gone to the
brush's edge at 0, 25 and 100 %; `_feather_alpha_out` is 1 in the hole,
falls outward, and is 0 next to a hard edge.
