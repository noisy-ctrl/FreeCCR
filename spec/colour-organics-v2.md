# Colour Organics v2: the labs' hue moves and saturation, by tone

The user, 2026-11-25: "Sometimes it feels like colour organic isn't strong
enough … earthy colours look earthy on [Noritsu] scanners but not so nice on
my scans", then "it would be ideal to not just have earthy tones but broader
colour trends too … the interplay of highlights and shadows on saturation";
on the plan: "come in as a new version ('v2' or similar - can be 2nd
slider)", "Second slider is good".

## Goals

- A newer Colour Organics that turns colours the way the labs do **by tone as
  well as by hue**, dark tones included, measured on the user's own pairs.
- A second slider for the labs' **saturation by tone and hue**: mid tones keep
  their colour, shadows and light tones ease off, more for some hues.
- Both measured, both "50 = the labs' amount", both held to the finishing
  tools' quality rules.

## Non-goals

- Replacing Colour Organics (v1). Frames edited with it keep it unchanged;
  v2 is a separate key. Use one or the other (the tooltip says so).
- The labs' tone curve and their calmer colour grain: measured in the same
  study (below) and proposed as separate tools for sign-off.
- A GPU twin (first version runs in numpy; with v2 on, the OpenCL path runs
  the whole finishing chain in numpy, in order).

## What was measured

50 matched pairs, found by matching the user's RAW scans against his lab
archive (SIFT over every frame of the archive, verified with RANSAC): his
RAW triplet decoded by FreeCCR against the lab scan of the same negative,
three Noritsu labs (00003875: 14 frames, 060940: 30, 14868425: 6; Portra
400), registered and sampled as `tools/fit_look.py` does. Each frame is
rendered by FreeCCR's default render after fitting its six Channel Levels
numbers to the lab scan (the lab's exposure and balance), then compared in
OKLab.

Findings (lab minus FreeCCR):

| | dark (L 0.2-0.45) | mid (L 0.45-0.75) | light (L > 0.75) |
|---|---|---|---|
| red-orange hue | +6 to +12 deg | +3 to +5 | +12 |
| orange/brown hue | +6 to +11 | +3 to +5 | +1 to +3 |
| sky/blue hue | -11 to -12 | -5 to -8 | -4 |
| blue/teal/green chroma | x0.71-0.83 | x0.89-1.04 | x0.81-0.90 |
| earthy brown chroma | x0.92-0.94 | x0.98-1.00 | x0.89 |

Colour Organics (v1) at its labs' amount closes the mid-tone oranges and skin
(residual about 0) but leaves reds +6-8 deg, pinks +6-9 deg and blues -5 to
-20 deg, and dark browns +7-10 deg: its fit used mid and bright tones only,
HSV hue, and a pull to 0.

The labs differ: 00003875's scans already sit close to FreeCCR's default in
mid tones; 060940 and 14868425 move further in the same directions. Held out
(fitted on the other two labs, scored on the third), at the labs' amount:

| held out | hue error v1 at 100 -> v2 | chroma error today -> v2 |
|---|---|---|
| 060940 | 2.84 -> 2.45 deg | 10 % -> 7.5 % |
| 14868425 | 2.87 -> 2.65 deg | 8 % -> 6 % |
| 00003875 | 2.23 -> 4.51 deg (half strength 2.86) | 6 % -> 9 % (half 5 %) |

So strength is the user's: the sliders.

## UX

Two sliders directly under Colour Organics (shown in colour, hidden in B&W
with Colour Organics), 0..100, "sat" sync group, tooltips on the labels:

- **Organics v2** (`organics_v2`): the hue moves by tone.
- **Organic Saturation** (`organic_sat`): the saturation by tone, measured on
  top of Clean Lights at 50, so the two add up to the labs.

50 = the labs' measured amount, 100 twice it. Copy/paste rows and the paste
dialog's labels come from ADJUSTMENT_KEYS / `_BASIC_LABELS` like every slider.

## Data

`core/organics_v2_data.py`, WRITTEN by `tools/fit_organics_v2.py`:
`HUE_KNOTS` (24, every 15 deg of OKLab hue), `L_KNOTS` (0.15 .. 0.90 OKLab
L), `HUE_MOVE` (deg) and `CHROMA_LOG` (log ratio, <= 0). Per lab, a Gaussian
kernel average (12 deg in hue, periodic; 0.08 in L) of each sample's move,
each frame weighted the same and each sample faded in from grey by its
chroma; each node shrunk by its support eff / (eff + 150); labs combined
weighted by support (a node only one lab measured keeps that lab's value,
shrunk). The chroma table is measured with Clean Lights at 50 applied to our
render and capped at 0.

## Processing (`core/organics_v2.py`)

On display values in OKLab, float32:

1. Decide on the decision view (`oklab.decision_lab`): hue h_s, lightness
   L_s, chroma C_s; gate g = smoothstep((C_s - 0.005) / 0.03).
2. Fields at the labs' amount, from the tables by the fit's own kernel
   evaluation, tabulated on a 0.5 deg x 0.005 L grid and read bilinearly:
   turn = (v2/50) . g . move(h_s, L_s), log factor = (sat/50) . g .
   clog(h_s, L_s).
3. Both fields smoothed along the picture's edges (guided filter on the
   decision lightness, the finishing tools' Smoothing 30 radius, scaled with
   the render); the log factor kept <= 0.
4. Applied per pixel: the pixel's own (a, b) turned by that angle and scaled
   by exp(log factor) <= 1 at its own L; `oklab.fit_move` shortens a move
   that would leave 0..1; a pixel turned < 0.001 deg with a factor within
   1e-5 is returned exactly.

Quality: exact no-op at 0; lightness never changes; chroma never grows (grain
kept or reduced); the hue map never folds (1 + (amount/50) d move/d hue >=
0.48 measured at 100 on a 0.25 deg grid; `fold_limit()` holds the amount
under the 0.10 floor if a refit ever needs it); smooth weights (C1 gate,
Gaussian tables); resolution independent (decision radii scale with the
render).

## Integration

- `ccr_processor.adjust_image` / `adjust_image_opencl`: `organics_v2`,
  `organic_sat` appended LAST (positional callers); the stage runs right
  BEFORE Clean Lights. On OpenCL, with either on, `_finish_on_gpu` is skipped
  and the numpy chain runs v2 -> Clean Lights -> Golden -> Shadow
  Neutralise -> Shadow Lift -> Glow on the kernel's result. The fallbacks to
  `adjust_image` pass both.
- `CCRImage` passes `s.get('organics_v2', 0)`, `s.get('organic_sat', 0)` at
  both render sites.
- `SlidersPanel.ADJUSTMENT_KEYS` ends with them (created last, inserted in the
  layout right under Colour Organics); `_BASIC_LABELS`; "sat" sync group;
  `_sync_bw_rows` hides them in B&W.

## Test plan (`tests/test_organics_v2.py`)

No-op at 0 (the input object); greys come back exactly; L kept (< 2e-3);
directions match the labs (orange yellower, blue towards cyan, red towards
orange, by the table's amount); dark browns turn further than mid oranges;
saturation never adds chroma; the hue map never folds at 100; a 4096-step hue
ramp stays in order at 100; output in range; colour grain doesn't grow; the
same turn at two render sizes; `adjust_image` no-op at 0 and moves at 60;
panel keys last and in the "sat" group.

## Proposed next (the same study, for sign-off)

- **Lab tone curve** (all three labs agree): a lifted toe (where FreeCCR
  reaches OKLab L 0.04 the labs sit at 0.14-0.16, slope 0.15-0.45 there),
  lower mids a touch darker (-0.02 to -0.035 in two labs), steeper mids
  (slope 1.1-1.3 around L 0.45-0.7), a soft shoulder (slope 0.6-0.8 above L
  0.8; white 0.91-0.96, FreeCCR's brightest 0.5 % reach 1.0).
- **Colour grain / grouping**: against lightness detail, FreeCCR carries
  about 35 % more fine colour mottle than the Noritsu scans of the same
  negatives (colour/lightness band energy 0.291 vs 0.216 at 2 px of a 1200 px
  frame) and 15-20 % more colour variation at 2-16 px; the labs sharpen
  lightness about 1.4x at the finest scale. The labs and the user's 42
  reference frames gather colour into fewer hue families (84-86 % of the
  chroma in the top 3 of 12 sectors, FreeCCR 81 %).
