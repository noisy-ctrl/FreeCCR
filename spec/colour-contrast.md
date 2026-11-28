# Colour Contrast

The user, 2026-11-26: "A 'Colour contrast' tool sounds good", after the
colour-separation study; and on what he is after: "Colour separation is
amazing but with effortless gradations … Skin tones are beautiful and stay
neutral. You never really notice the white balance. Colours feel painterly
how they're rendered and you can almost group colours when looking at the
image."

## What the study found (spec context)

On his 50 paired frames, FreeCCR's renders already match the lab scans of
the same negatives on whole-picture colour measures (colourfulness, shared
tint, colour difference between areas, warm lights / cool shadows, hue
spread). The 8 full-size references in "Film refs" have more colour
difference between areas than either (0.226 vs 0.175 lab, 0.170 FreeCCR,
region colour against region tone at >= 16 px of 1000) and less of one
shared tint (0.54 vs 0.75 / 0.74). So this is a look beyond the labs', for
taste: groups of colour set apart, the inside of each area left alone.

## Goals / non-goals

- Raise the colour difference between neighbouring areas; leave the colour
  variation inside an area (grain, texture, gradations) as it is.
- Lightness never changes; greys stay grey; skin stays natural.
- Not a saturation slider (an area as colourful as what surrounds it isn't
  pushed), not a hue grouping (pulling hues together is a different tool),
  not a local contrast on tone.

## UX

Slider **Colour Contrast** 0..100 (default 0) in "Saturation & Lab Finish",
right under Organic Saturation; key `colour_contrast`, "sat" sync group.
Right-click the label: **Protect skin** (default on: colour of skin's hue
takes none of the push). Stored as `adjustment_settings["colour_contrast_shape"]`
= {"skin": false} only when off (None = default), plumbed like
`organics_shape`. Tooltip explains it in one paragraph.

## Processing (`core/colour_contrast.py`)

On display values in OKLab, per render, at the render's own resolution:

1. Decision view `decision_lab` (denoised). AREA colour = its a, b through a
   guided filter on its lightness, radius 6 px at the preview's 1080 (scaled
   with the render), eps 0.02^2: smooth inside areas, edges kept.
2. SURROUND = the area colour blurred with a Gaussian of sigma 4 % of the
   long side, on a reduced copy (sigma >= 8 px there) scaled back up (a field
   far smoother than its sampling, as Glow's spread).
3. push = k (area - surround), k = amount/100 x 1.2, times a chroma gate
   smoothstep((C_area - 0.008)/0.03) (near-grey areas aren't pushed: no
   complementary tint on a grey wall beside a red door); softly limited
   0.035 tanh(|push|/0.035); a push towards grey keeps at least half the
   area's chroma (no hue flip).
4. The push is smoothed along edges (guided filter on the decision
   lightness, `matte_radius(30)`), then with Protect skin multiplied by
   1 - skin, skin = a flat-topped hue window on the decision view's own
   colour (full within 16 deg of OKLab 62 deg, none past 32 deg, faded in by
   chroma 0.01..0.03), smoothed the same way.
5. Added to every pixel's own a, b at unchanged L (`fit_move` shortens a
   move that would leave 0..1); |push| < 1e-5 returns the pixel exactly.

Quality rules: no-op at 0; float32; C1 weights; a linear colour ramp is not
moved (area and surround agree, both filters keep linear functions inside
the frame); in range; grain not steered (decided on the decision view,
applied as a smooth field; measured grain x1.0001 at 1200 px); resolution
independent (radii scale with the render).

## Integration

Runs right after Clean Lights in `adjust_image` (before Lab Tone's own call
there and Golden Highlights),
`colour_contrast` / `colour_contrast_shape` appended LAST to
`adjust_image` / `adjust_image_opencl`; on OpenCL with it on the numpy
finishing chain runs in order (`_finish_on_gpu` skipped, as Organics v2), a
GPU twin later. `CCRImage` passes both at its two render sites. Panel: created
LAST in `ADJUSTMENT_KEYS` (after `luma_nr_detail`) and inserted under Organic
Saturation; hidden in B&W with the colour rows.

## Measured (drafts 2026-11-26)

His 50 paired frames (FreeCCR default + Organics v2 Lab A 50/50 + Clean
Lights 50), median, Protect skin on:

| Colour Contrast | region colour vs tone | inside areas | shared tint |
|---|---|---|---|
| 0 | 0.170 | 0.214 | 0.74 |
| 50 | 0.197 (+16 %) | 0.217 | 0.72 |
| 100 | 0.211 (+24 %) | 0.213 | 0.70 |

Faces (park frame, 100): Protect skin on, hue -0.1 / -1.7 / -2.2 deg,
chroma x1.02 / 1.06 / 1.10; off, -2.3 / -5.5 / -7.7 deg, x1.38 / 1.40 / 1.23.
Synthetic: lightness change <= 2e-7; a grey patch unchanged; colour grain
x1.0001-1.0002 at 1200 px; 0.3-0.5 s at 1200 px, 1.2-1.6 s at 3200 px.

## Tests (`tests/test_colour_contrast.py`)

0 = the input itself; L unchanged; greys unchanged; neighbouring areas move
apart; a linear ramp's interior unchanged; in range, float32; grain doesn't
grow; skin protected (and not when off); resolution independent (600 vs
1200 px agree after resizing); unmoved pixels exact.

## Refinement (once, before building)

- Why not just more saturation? Saturation lifts everything, including what
  sits among the same colour; this only moves an area that differs from its
  surroundings, so a sky or a lawn alone stays as it is and gradations
  inside areas are untouched (inside-area variation unchanged above).
- Why protect skin by hue rather than by the Skin tool's faces? There are no
  marked faces in a render; the hue window also protects oranges, sand and
  terracotta, which the user can turn off with Protect skin.
- Surround size as an option? Not yet: one look first; Reach can come later
  as the tools' options did.

## As built (2026-11-28, the user: "Please build")

- `core/colour_contrast.py`: `normalize_cc_shape` ({"skin": False} or None),
  `protects_skin`, `apply_colour_contrast(img01, amount, shape=None,
  protect_skin=None)` (the keyword overrides the stored options; the tests
  use it).
- `ccr_processor`: the stage after Clean Lights in `adjust_image` and in the
  OpenCL numpy chain; with it on `_finish_on_gpu` is skipped (as Organics v2
  and Lab Tone), so the chain runs in numpy in order. `CCRImage` passes
  `colour_contrast` / `colour_contrast_shape` at both render sites (whole
  frame and area layers).
- Panel: `colour_contrast_slider_layout` inserted right after Organic
  Saturation, hidden in B&W with Organics v2 (`_sync_bw_rows`). The label's
  right-click is `colour_contrast_menu()` (the test seam): one checkable
  **Protect skin · skin-coloured areas take none of the push (oranges, sand
  and terracotta share skin's colour, so they are held back too)**;
  `set_cc_protect_skin(on)` is one undo step and re-renders like a slider
  move; the label is underlined while it is off and its tooltip says which.
  `_cc_shape` is plumbed like Organics v2's lab: load, attach, reset, the
  "sat" sync group, the copy snapshot, and paste — Protect skin rides the
  Colour Contrast row ("Colour Contrast  (+60, skin not protected)"), the
  clipboard's replacing the target's when that row is pasted.
- Tests: `tests/test_colour_contrast.py` (core, render order, OpenCL ==
  CPU within 8 codes on pocl, `apply_adjustments`, the panel's row, menu,
  undo, load/reset, paste, copy, sync); the key-order tests moved on by one.

## Revision 2026-11-28: memory

A full-size export of a 51 MP frame ran out of memory at 8 GB inside the
stage (every step was a whole-picture array; the stage alone peaked above
6.4 GB over its input). Same maths, less memory:

- the per-pixel steps (the OKLab conversion, the push, the skin weight, the
  final move) run in bands of `CHUNK_ROWS` (256) rows; the OKLab picture is
  not kept, each band is converted again where pixels move;
- the decision view is three planes, each raw plane let go once smoothed;
- `_guided` = `oklab.guided_filter` for several planes along one guide, the
  guide's statistics worked out once and the arithmetic in place (the same
  operations in the same order: a test holds it equal to the guided filter);
  the area pair, then the push pair and the skin matte share it;
- `apply_colour_contrast(..., inplace=True)` writes into the caller's float32
  copy; the render passes the copy it makes anyway.

Measured at 8280 × 6208: the stage 1.7 GB over its input (was > 6.4 GB), a
full export 5.7 GB peak (3.8 GB without Colour Contrast), 29 s here. The
result is the old one to the bit except where a move meets the gamut edge:
there `fit_move`'s 1/4096 bisection step can land one step either side for
a push that differs by 1e-9 (71 of 1.96 M pixels on the test picture, at
most 16 16-bit codes, 0.06 of an 8-bit level).
