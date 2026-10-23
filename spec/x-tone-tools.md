# Highlights L / Shadows ⅃ / Punch × (the measured tone tools)

Renamed 2026-10-22: the sliders were labelled Highlights × and Shadows ×; they are now
**Highlights L** and **Shadows ⅃** (U+2143, a reversed L). Only the labels changed: the
keys stay `highlights_x` / `shadows_x`, so catalogs and copied settings are unaffected.
Below, "Highlights ×" and "Shadows ×" mean these two.

Three sliders that do what the measured **Lights**, **Darks** and **Punch** do,
measured from the measured exports, with right-click options. Highlights and
Shadows go back to their plain original behaviour (their range / keep options
move here).

## Goals

- **Highlights ×**, **Shadows ×**, **Punch ×**: −100..+100, default 0. At
  ±50 and ±100 they reproduce the measured measured change; in between they follow
  a smooth path through those points.
- By default **exactly what the measured render does**, ends included: Highlights × leaves
  black alone and barely moves white; Shadows × leaves white alone and **lifts
  black at +** (the measured render fades the blacks: its 0.015 black becomes 0.084 at +100)
  and crushes it at −; Punch × moves both ends a little (+100: white +0.017,
  black −0.005).
- Right-click each label:
  - Highlights × — **Range** (Narrow ↔ Wide; how far down the tones it
    reaches) and **Keep white point**.
  - Shadows × — **Range** (how far up it reaches) and **Keep black point**.
  - Punch × — **Keep black point** and **Keep white point**.
  The middle of Range with Keep off is the measured behaviour.
- **Highlights / Shadows** lose their right-click popup and render with the
  original curve, always. Edits that used the old range / keep options render
  without them.

## Non-goals

- the measured base positive render itself (it brightens: mid-grey 0.5 → 0.66).
  The tools reproduce the measured *change* from that render, not the render.
- Combinations of the three, which were not measured (see Processing).
- A GPU kernel version; the tools run in numpy on both paths (see Integration).

## What the measured render does (measured)

Exports of a level-16 HALD (256³ colours, 16-bit) from the measured positive mode:
no adjustment, and each tool at −100, −50, +50, +100. All are smooth, global
colour transforms (a 15-step resample reproduces each to < 1.1/255 rms), not
local tone mapping.

- The base render never reaches black or white: black (0.006, 0.015, 0.012),
  white 0.958–0.960.
- **Lights**: black fixed; white 0.960 → 0.965 (+100) / 0.947 (−100); mid-grey
  lifted 0.48 → 0.63 at +100. Not just a tone curve: +100 desaturates bright
  colours (OKLab chroma ×0.6 at L 0.8) and saturates dark ones; −100 the
  reverse.
- **Darks**: white fixed; black 0.015 → 0.084 (+100) / 0.003 (−100).
- **Punch**: a contrast change through the upper mids, both ends moving; trims
  bright-colour chroma at +.
- Per-channel curves on the measured output, luminance scaling, or a curve before
  its render all miss by 6–40/255 rms: the colour behaviour has to be kept as
  a 3D transform.
- Amount: Punch is linear in the slider (the ±50 change is 0.49–0.51 of the
  ±100 one); Lights and Darks are not (0.43–0.55, and not a pure scale).

## Processing

`core/x_tones.py`, data `core/x_tones.npz` built by
`tools/fit_x_tones.py` from the HALD exports.

**Space.** FreeCCR's display values `u` (0..1). the measured black..white is
mapped onto FreeCCR's: `y = b + u (w − b)` per channel with the measured base black
`b` and white `w`, and a change in the measured values is divided by `(w − b)`.
So FreeCCR's white behaves like the measured white, and every near-neutral tone
from black to white lies inside what the measured render can produce (no guessing on the
grey axis).

**Grid.** For each tool and sign, on a 33³ grid of `u`: invert the measured base
render at `y` (Gauss–Newton on the HALD, clamped to the cube), then
`D50 = (tool50(x) − base(x)) / (w − b)` and the same for ±100. Nodes the measured
render cannot reach (strong colours: its render compresses saturation; about
half the cube) are filled by **harmonic extension** of the reachable ones
(Neumann at the faces): smooth, where copying the nearest reachable value
folded colours over. Stored as the coefficients of the quadratic through
0, 50 and 100: `A = 4·D50 − D100`, `B = 2·D100 − 4·D50`, so a slider at
`v` adds `A t + B t²` with `t = |v|/100` (sign picks the grid). Exact at
0, ±50, ±100; float16; ~2.3 MB.

**Lookup.** Trilinear on the grid, in chunks (export-size images stay within
memory).

**Range** (0..100, 50 = the measured render). The change is read at a remapped tone
`q = u / (u + k(1 − u))` per channel (a bias curve: identity at k = 1,
endpoints fixed, slope between 1/k and k, so it never folds by itself) and
added at `u`. Highlights ×: `k = 2^(−(r−50)/50)` (wider reads brighter tones'
change, reaching further down); Shadows ×: `k = 2^((r−50)/50)`.

**Keep.** The change is faded towards the pinned end by Rec.709 luma `Y` of
`u`: keep white `× (1 − smoothstep(0.75, 1, Y))`, keep black
`× smoothstep(0, 0.25, Y)`. Pure white / black then stay exactly where they
are; nothing new clips or crushes there.

**Combining.** Highlights × and Shadows × are read at the same input and
added (they work on complementary regions); Punch × then acts on the result.
Clamped to 0..1. (the measured own combination was not measured; this can be
fitted later from a combined export.)

**Accuracy** (against the measured exports, in the measured values): colours up to 40 %
saturation within 0.1–0.3/255 rms (p99 < 1/255) at every measured amount;
strongly saturated colours within 1–3/255 rms. Known limit: at −100,
Highlights × on bright, strongly coloured areas can step slightly (the measured render
itself pushes their chroma ×1.5–1.7 there, beyond what its render can show,
so that region is extrapolated).

## Pipeline position

Right after Highlights / Shadows, before Black / White Point and Contrast:
they are tone tools on the display image, like the measured. `highlights_x`,
`shadows_x`, `punch_x`, `xtone_shape` are appended LAST to `adjust_image` /
`adjust_image_opencl` (positional callers). On the OpenCL path, when any is
set, every kernel stage before Highlights/Shadows (non-windowed Channel
Levels + Master Gain, Gain, Brightness), Highlights/Shadows (the one shared
numpy helper) and the three tools run in numpy and are zeroed for the kernel
— the pre-stage the old custom H/S shape used — so parity is exact.

`tone_shape` stays in both signatures (positional callers) and is ignored.

## Data model

- Keys `highlights_x`, `shadows_x`, `punch_x` in `ADJUSTMENT_KEYS` (slider
  zip; created after Black Point, before Contrast), Tone sync group, paste
  rows, catalog via the adjustment dict, area layers like any slider.
- `adjustment_settings["xtone_shape"]`: `{"hx_range", "hx_keep", "sx_range",
  "sx_keep", "px_keep_black", "px_keep_white"}`, absent / None when default
  (`normalize_xtone_shape`). Plumbed exactly as `tone_shape` was (it replaces
  it): attached on every slider → dict rebuild, loaded per layer, one undo per
  popup, synced with the Tone group, its own paste row, copied.

## UI

Three sliders, labels "Highlights L", "Shadows ⅃", "Punch ×". Shown
(2026-10-23) Highlights L directly under Highlights, Shadows ⅃ directly under
Shadows, Punch × under Black Point; created (and in `ADJUSTMENT_KEYS`) after
Black Point as before, so only the layout order changed. Label tooltip: what the tool does + "Right-click: options"; underlined
when options are not default. Popup (`widgets/x_tone_popup.py`): the
options above, a curve preview (the tool on the grey axis at the current
amount, or faint ±100 at 0), Reset. Kept on screen (`place_on_screen`).

## Tests (`tests/test_x_tones.py`)

1. Data loads; at 0 the tools are an exact no-op (byte-identical render).
2. Against the HALD-derived grid points: the grey axis at ±50/±100 matches
   the measured the measured render values (Lights black fixed, Darks white fixed, Darks
   +100 black ≈ +0.073 in FreeCCR units, Punch both ends).
3. Range 50 / Keep off = default; Range moves the peak of the change
   (wider → further into the other tones); Keep pins black / white exactly.
4. Monotone grey axis for every tool at ±100 across Range 0..100 with and
   without Keep.
5. CPU == OpenCL path (pre-stage) for a mixed setting; works with areas.
6. Highlights / Shadows ignore an old `tone_shape` (render = the classic
   formula).
7. Panel: three sliders in place, keys zipped right, popup changes
   `xtone_shape` with one undo step, paste row, sync with Tone.

## Refinement notes

- Why not a parametric model: every simple one missed by 6–40/255 (above);
  the measured tools couple tone and colour.
- Why map the measured black..white onto FreeCCR's: without it the top 4 % and
  bottom 1.5 % of FreeCCR's grey axis were outside the measured reach, and the
  extrapolated change there bent the curve backwards (slope −1.4 near white
  at Highlights × −100).
- Options can steepen a tool where the measured render is already steep: Shadows × +100
  with Range 100 turned the grey axis back over 0.88–0.97 (by 0.007), and
  Shadows × −100 with Keep black did near black. `_unfolded` pulls the
  ranges towards the middle (bisection), then widens the kept stretch
  (black end 0.25 → 0.6, white start 0.75 → 0.4), only as far as needed for
  the grey axis to never turn back. Default options are the measured and are never
  altered. One remaining ripple, under 0.5/255, is the grid interpolating
  where the chain clips at black.
- Speed: the chain is evaluated at the 33³ nodes once per setting and cached,
  then one trilinear lookup (four lerps along red, two along green, one along
  blue, float32) per pixel: ~110 ms for a 1080 px preview on the 2-core test
  machine, a fraction of that on an M-series Mac.
- Why harmonic fill and not fading the guessed region: fading the change by
  distance outside the measured colours created its own gradients and doubled the
  folds.
