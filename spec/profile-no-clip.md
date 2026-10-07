# Camera profiles: Prevent channel clipping

## Problem

A camera profile white-balances the raw on its calibration neutral before its
matrix or cLUT: `gains = 1/neutral`, green = 1. Under a strongly coloured light
(trichrome RGB scanning is the extreme case) one gain is large: a measured
trichrome profile had `gains = 2.81 : 1 : 0.575`. The output is uint16 linear
Adobe RGB, so:

- matrix ICC and DCP clip at the output, and
- the cLUT ICC clamps the balanced input to its `[0,1]` grid BEFORE the lookup,

so any raw red above `1/2.81 = 36 %` of full scale is clipped. On a colour
negative the brightest raw values are the film base and the thinnest areas,
which are exactly the shadows of the positive, and the black point is usually
sampled on the rebate. Measured on a real trichrome negative: 3.2 % of pixels
clipped, the deepest shadows went navy, and the rebate black point read red at
0.804 instead of 0.944. Exposure-dependent clipping is not correctable
downstream.

## Goal / non-goals

- A per-profile opt-in that guarantees no channel clips after the profile's
  white balance, with output identical (up to a uniform scale) wherever nothing
  clipped before.
- Every negative conversion mode renders as before wherever nothing clipped.
- Non-goal: changing any existing profile's behaviour. The flag is absent from
  every profile written before this, and from new ones unless ticked.
- Non-goal: Positive mode. The camera profile is not applied there.

## UX

IT8 wizard, Step 3 (Locate the patches), next to Mirrored capture:
**Prevent channel clipping** (tooltip explains). Remembered in QSettings
(`it8/no_clip`, default off). It is a property of the saved profile (ICC or
DCP), not a global setting, so it travels with the profile file.

## Data

- ICC: private tag `CCRh` (XYZType payload, presence is the signal), like
  `CCRk`.
- DCP: private TIFF tag 52526 (`long [1]`), like 52525.
- Loaded profiles carry `no_clip` and `headroom` (float >= 1, 1.0 = classic).

## Math

`headroom` (k) is computed once at load, and only when the profile has its own
calibration neutral, which makes the gains (and so k) a per-profile constant.
Without one the WB is per frame, k would drift frame to frame, and the flag is
inert.

For a linear profile `A` (balanced device -> linear Adobe RGB), the balanced
input of a raw in `[0,1]` lies in the box `[0, g]`, so each output channel's
maximum is `sum_j max(A_ij, 0) * g_j`. `k = max(1, max_i of that)`: the
smallest uniform scale that guarantees no output clips. (For the measured
trichrome profile k = 2.35, 1.23 stops; less than the 2.81 red gain because
red output only needs that much.)

- Matrix ICC / DCP: output = `classic_output_before_clip / k`.
- cLUT ICC: the in-grid part is looked up as before; the part beyond the grid
  continues along the cLUT's linear base (least-squares fit of the table over
  its nodes: the wizard's table is the 3x3 plus a residual faded to zero
  outside the chart's hull), then everything is divided by k.
  `k = max(1, max_i [max table_i + sum_j max(A_ij,0) * max(g_j - 1, 0)])`.

Result: wherever the classic profile did not clip, new = old / k exactly (to
uint16 rounding, verified on real data); nothing reaches the ceiling; the
film-base colour stays constant with exposure.

## Downstream

Every anchored conversion is a ratio against points sampled on the same
(scaled) decode, so a uniform scale cancels: two-point linear and density,
black-point-only and reference frame were verified unchanged on unclipped
pixels. The one mode that reads absolute levels, the **no-anchor** density
inversion, would shift by `log10(k)` (0.37 of the display range for k = 2.35),
so it is compensated: `CCRImage.read_image` resets `profile_headroom = 1.0`
per decode and `_apply_input_icc` / `_apply_input_dcp` set it to the profile's
k only when the profile is actually applied (so Positive, monochrome, disabled
and unprofiled decodes record 1.0). `_unanchored_density_invert(img,
input_scale)` multiplies its input scale by it; all four call sites pass the
image's recorded value.

As with any profile change, B/W points sampled under a different profile must
be re-sampled (the existing profile-mismatch indicator covers this).

## Tests

`tests/test_profile_no_clip.py`: flag round-trip (matrix ICC, cLUT ICC, DCP),
inert without a neutral, tight bound, never clips, exact match where unclipped,
smooth monotonic continuation past the cLUT grid, constant base colour, every
conversion mode unchanged on unclipped pixels, the no-anchor compensation is
needed and works, decode records the scale only when applied, and the wizard
checkbox persists and writes the flag.
