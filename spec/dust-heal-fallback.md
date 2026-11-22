# The heal's diffusion fallback: 16 bits, and looked at again

The user, 2026-11-22: on a frame of plain sky, a dust clump healed into a
white, finely hatched blob about four times its size, and a speck into a
cyan disc; "I think it's where the separate healing tools overlapped".
Reproduced exactly from his catalogue (frame R0023 005, 194 spots: 179 IR,
14 brush strokes scribbled over IR spots, 1 mark, his cached heal plan):
with his plan the patch at the clump had standard deviation 2999 against
987 for a fresh plan and 1133 for no heal at all.

## Cause
1. At the clump, his plan had chosen the DIFFUSION fallback (no clean source
   window: when he painted, the strokes and IR spots around it boxed it in).
   The fallback ran `cv2.inpaint` on an 8-bit copy of the buffer. On a
   converted base the working-space window packs a whole sky into a few
   8-bit codes (here ~60 16-bit codes, a quarter of one 8-bit step), so the
   fill was posterised; the grade stretched the steps into a checkerboard at
   preview size and the white textured blob at export size.
2. A plan's fallback was PINNED like a source: every later edit replayed
   "diffusion here", so once the strokes around it were erased or changed,
   it never got a source again (a fresh plan finds one at once).

## Fix (`core/ccr_processor.py`)
1. `_telea16`: the fallback runs Telea per channel on the 16-bit buffer
   (OpenCV takes 16-bit single-channel input), in each fallback area's box
   padded by the radius + 2. Measured on his frame with his own plan: the
   clump's patch 2999 → 984 (the no-dust render of clean sky: 987).
2. `_heal_impl(..., retry_fallbacks=)`: the same-scale edit replay
   (`apply_dust_removal`'s canonical path with `prior_plan`) searches again
   where the prior plan fell back; a source found is recorded and replayed
   at every scale. A fallback isn't a source, so nothing already sampled on
   screen moves. Cross-scale replays keep the plan's choice (preview ==
   export). His frame: 9 fallbacks → 7 at the first preview render, the
   clump healed by cloning.

## Tests (`tests/test_dust_heal_fallback.py`)
The fallback fill on a ~60-code gradient stays within 8 codes (the 8-bit
fill was > 50 out) and touches nothing outside the hole; a forced fallback
heal replayed at 2× has no 8-bit steps; an edit with a stale fallback record
finds a source; a cross-scale replay keeps the fallback.
