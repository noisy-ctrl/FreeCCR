# Highlights ⅃ / Shadows L / Contrast L ⅃ (measured tone tables)

Three tone sliders whose change is stored as a measured 3D table rather than
a formula, with right-click options. Labels: **Highlights ⅃** (U+2143, a
reversed L), **Shadows L** and **Contrast L ⅃**, the marks drawn in red
(revision 2026-10-23: the Highlights / Shadows marks were the wrong way
round); keys `highlights_x`,
`shadows_x`, `punch_x` (the keys never changed, so catalogs and copied
settings are unaffected by any relabelling). Highlights and Shadows keep
their plain original behaviour.

## Goals

- Three sliders, −100..+100, default 0. At ±50 and ±100 each reproduces its
  measured change exactly; in between it follows a smooth path through those
  points.
- The ends, by default: Highlights ⅃ leaves black alone and barely moves
  white; Shadows L leaves white alone and **lifts black at +** (a fade: the
  measured base black 0.015 becomes 0.084 at +100) and crushes it at −;
  Contrast L ⅃ moves both ends a little (+100: white +0.017, black −0.005).
- Right-click each label:
  - Highlights ⅃ — **Range** (Narrow ↔ Wide; how far down the tones it
    reaches) and **Keep white point**.
  - Shadows L — **Range** (how far up it reaches) and **Keep black point**.
  - Contrast L ⅃ — **Keep black point** and **Keep white point**.
  The middle of Range with Keep off is the default behaviour.

## Non-goals

- A base render: the tables hold a *change* from an ungraded render, not a
  render.
- Combinations of the three, which were not measured (see Processing).
- A GPU kernel version; the tools run in numpy on both paths (see Pipeline).

## The measured behaviour

Level-16 HALD images (256³ colours, 16-bit): one ungraded, and one per tool
at −100, −50, +50, +100. All are smooth, global colour transforms (a 15-step
resample reproduces each to < 1.1/255 rms), not local tone mapping.

- The ungraded render never reaches black or white: black (0.006, 0.015,
  0.012), white 0.958–0.960.
- **Highlights ⅃**: black fixed; white 0.960 → 0.965 (+100) / 0.947 (−100);
  mid-grey lifted 0.48 → 0.63 at +100. Not just a tone curve: +100
  desaturates bright colours (OKLab chroma ×0.6 at L 0.8) and saturates dark
  ones; −100 the reverse.
- **Shadows L**: white fixed; black 0.015 → 0.084 (+100) / 0.003 (−100).
- **Contrast L ⅃**: a contrast change through the upper mids, both ends
  moving; trims bright-colour chroma at +.
- Per-channel curves, luminance scaling, or a curve before the render all
  miss by 6–40/255 rms: the colour behaviour has to be kept as a 3D
  transform.
- Amount: Contrast L ⅃ is linear in the slider (the ±50 change is 0.49–0.51
  of the ±100 one); the other two are not (0.43–0.55, and not a pure scale).

## Processing

`core/x_tones.py`, data `core/x_tones.npz` built by `tools/fit_x_tones.py`
from the HALD images.

**Space.** FreeCCR's display values `u` (0..1). The measured black..white is
mapped onto FreeCCR's: `y = b + u (w − b)` per channel with the ungraded
render's black `b` and white `w`, and a measured change is divided by
`(w − b)`. So FreeCCR's white behaves like the measured white, and every
near-neutral tone from black to white lies inside what was measured (no
guessing on the grey axis).

**Grid.** For each tool and sign, on a 33³ grid of `u`: invert the ungraded
render at `y` (Gauss–Newton on the HALD, clamped to the cube), then
`D50 = (tool50(x) − base(x)) / (w − b)` and the same for ±100. Nodes the
ungraded render cannot reach (strong colours: it compresses saturation;
about half the cube) are filled by **harmonic extension** of the reachable
ones (Neumann at the faces): smooth, where copying the nearest reachable
value folded colours over. Stored as the coefficients of the quadratic
through 0, 50 and 100: `A = 4·D50 − D100`, `B = 2·D100 − 4·D50`, so a slider
at `v` adds `A t + B t²` with `t = |v|/100` (sign picks the grid). Exact at
0, ±50, ±100; float16; ~2.3 MB. Keys `hx_*`, `sx_*`, `px_*` (`pos`/`neg`,
`A`/`B`), plus `black`, `white`, `reachable`.

**Lookup.** Trilinear on the grid, in chunks (export-size images stay within
memory).

**Range** (0..100, 50 = default). The change is read at a remapped tone
`q = u / (u + k(1 − u))` per channel (a bias curve: identity at k = 1,
endpoints fixed, slope between 1/k and k, so it never folds by itself) and
added at `u`. Highlights ⅃: `k = 2^(−(r−50)/50)` (wider reads brighter
tones' change, reaching further down); Shadows L: `k = 2^((r−50)/50)`.

**Keep.** The change is faded towards the pinned end by Rec.709 luma `Y` of
`u`: keep white `× (1 − smoothstep(0.75, 1, Y))`, keep black
`× smoothstep(0, 0.25, Y)`. Pure white / black then stay exactly where they
are; nothing new clips or crushes there.

**Combining.** Highlights ⅃ and Shadows L are read at the same input and
added (they work on complementary regions); Contrast L ⅃ then acts on the
result. Clamped to 0..1. (A combined measurement could refine this later.)

**Accuracy** (against the measured images, in their own values): colours up
to 40 % saturation within 0.1–0.3/255 rms (p99 < 1/255) at every measured
amount; strongly saturated colours within 1–3/255 rms. Known limit: at
−100, Highlights ⅃ on bright, strongly coloured areas can step slightly (the
measured change pushes their chroma ×1.5–1.7 there, beyond what the
ungraded render can show, so that region is extrapolated).

## Pipeline position

Right after Highlights / Shadows, before Black / White Point and Contrast:
tone tools on the display image. `highlights_x`, `shadows_x`, `punch_x`,
`xtone_shape` are appended LAST to `adjust_image` / `adjust_image_opencl`
(positional callers). On the OpenCL path, when any is set, every kernel
stage before Highlights/Shadows (non-windowed Channel Levels + Master Gain,
Gain, Brightness), Highlights/Shadows (the one shared numpy helper) and the
three tools run in numpy and are zeroed for the kernel, so parity is exact.

`tone_shape` stays in both signatures (positional callers) and is ignored.

## Data model

- Keys `highlights_x`, `shadows_x`, `punch_x` in `ADJUSTMENT_KEYS` (slider
  zip; created after Black Point, before Contrast), Tone sync group, paste
  rows, catalog via the adjustment dict, area layers like any slider.
- `adjustment_settings["xtone_shape"]`: `{"hx_range", "hx_keep", "sx_range",
  "sx_keep", "px_keep_black", "px_keep_white"}`, absent / None when default
  (`normalize_xtone_shape`): attached on every slider → dict rebuild, loaded
  per layer, one undo per popup, synced with the Tone group, its own paste
  row, copied.

## UI

Three sliders. Shown Highlights ⅃ directly under Highlights, Shadows L
directly under Shadows, Contrast L ⅃ directly under Contrast (moved there
from under Black Point on 2026-10-24); created (and in
`ADJUSTMENT_KEYS`) after Black Point, so only the layout order differs. Label
tooltip: what the tool does + "Right-click: options"; underlined when
options are not default. Popup (`widgets/x_tone_popup.py`): the options
above, a curve preview (the tool on the grey axis at the current amount, or
faint ±100 at 0), Reset. Kept on screen (`place_on_screen`).

## Tests (`tests/test_x_tones.py`)

1. Data loads; at 0 the tools are an exact no-op (byte-identical render).
2. Against the measured colours: the grey axis at ±50/±100 matches (black
   fixed for Highlights ⅃, white fixed for Shadows L, Shadows L +100 black
   ≈ +0.073 in FreeCCR units, both ends for Contrast L ⅃).
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
  the measured changes couple tone and colour.
- Why map the measured black..white onto FreeCCR's: without it the top 4 %
  and bottom 1.5 % of FreeCCR's grey axis were outside the measured range,
  and the extrapolated change there bent the curve backwards (slope −1.4
  near white at Highlights ⅃ −100).
- Options can steepen a tool where it is already steep: Shadows L +100 with
  Range 100 turned the grey axis back over 0.88–0.97 (by 0.007), and Shadows
  ⅃ −100 with Keep black did near black. `_unfolded` pulls the ranges
  towards the middle (bisection), then widens the kept stretch (black end
  0.25 → 0.6, white start 0.75 → 0.4), only as far as needed for the grey
  axis to never turn back. Default options are never altered. One remaining
  ripple, under 0.5/255, is the grid interpolating where the chain clips at
  black.
- Speed: the chain is evaluated at the 33³ nodes once per setting and cached,
  then one trilinear lookup (four lerps along red, two along green, one along
  blue, float32) per pixel: ~110 ms for a 1080 px preview on the 2-core test
  machine, a fraction of that on an M-series Mac.
- Why harmonic fill and not fading the guessed region: fading the change by
  distance outside the measured colours created its own gradients and
  doubled the folds.
