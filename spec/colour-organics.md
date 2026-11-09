# Colour Organics

## Goal

Lab scanners render colour for print: neighbouring hues are drawn into a
few families (warm skin/wood/brick, foliage, sky/water, blue-violet) and the
transitions between families open up, which reads as cohesive and natural.
A neutral digital rendering keeps hues evenly spread, which can feel
"digital". Colour Organics does the grouping, with one amount slider.

The hue families are an informed guess, not a measurement of Noritsu: a
film-like rendering compresses hue differences around warm (0-55 degrees),
foliage (95-120) and blue-violet (245-310), and opens them up between green
and cyan (120-155), cyan and blue (205-235) and magenta and red (325-350).
When Noritsu scan pairs arrive, the anchors are replaced with measured ones.

## Non-goals

- No change to saturation or lightness (Saturation Roll-off does saturation).
- No negative direction (spreading hues apart).
- Not a per-colour tool (the colour bands are).

## UX

- Slider **Colour Organics**, 0..100, default 0, after Subtracted Sat (the
  last of the colour sliders). Its label's tooltip: "Constrains colours for
  print, the way lab scanners do. The result is often more natural." (It
  first shipped as a muted line under the slider; removed 2026-10-16 to save
  space.)
- Syncs and pastes with the **Saturation** group; works on area layers.

## Maths

Per pixel on display values `x` in [0,1]:

1. HSV hue `h` and saturation `s`. A gate `g = smoothstep(0.06, 0.20, s)`
   (the colour bands' gate) leaves greys and near-greys untouched.
2. Anchors (HSV degrees) `25, 100, 200, 265`. `h` falls in a span
   `[A_i, A_i+1)` (wrapping); `t = (h - A_i) / span`.
3. Warp within the span, amount `a = 0.85 * g * slider / 100`:

       t' = t + a (w t - B(t)),  w = 0.3

   where `b(t)` is a raised-cosine bump of width `w` at each anchor
   (`0.5 (1 + cos(pi t / w))` for `t < w`, mirrored at `1 - t`, 0 between)
   and `B` its integral. The slope is `1 + a (w - b(t))`: `1 - a (1 - w)`
   (0.40 at full) at an anchor, `1 + a w` (1.26) between anchors. It is
   always positive, so hues are never reordered and gradients can't band;
   the opening-up between families is spread over the middle of each span
   instead of piling up at one point (a plain sine warp would reach 1.85).
   Exactly: `E(t) = 0.5 (m + (w / pi) sin(pi m / w))` with `m = min(t, w)`
   and `B(t) = E(t) + 0.5 w - E(1 - t)`, so `B(0) = 0`, `B(1) = w` and the
   span's ends stay fixed. Largest move: about 0.07 of a span at full (inside
   an anchor's reach, where `b(u) = w`), so 4-8 degrees for these spans; the
   grouping comes from the slope near the anchors, not from large moves.
4. The new hue `h' = A_i + t' span` is realised at the same luma and
   chroma: with `f(h)` the fully saturated colour of hue `h` and
   `u(h) = f(h) - luma(f(h))` (Rec.601 luma, as Saturation and Vibrance),
   `x' = luma(x) + |x - luma(x)| u(h') / |u(h')|`. Every colour is a grey
   plus a positive multiple of `u` of its own hue, so at `h' = h` this is
   the identity exactly, and the result has HSV hue `h'` exactly.
5. If that leaves [0,1], the chroma is scaled towards the grey just enough
   to fit (closed form per channel): no clipping, hue kept.

## Pipeline position

After Subtracted Sat, before Saturation Roll-off (so the roll-off is the
last limit on strong colours) and before the per-colour bands. Same place in
the OpenCL kernel (`params[27]`, after the roll-off's `params[26]`); anchors
and constants substituted into the
kernel source, so the two paths can't drift.

## Integration points

As Saturation Roll-off: `colour_organics` appended at the END of
`adjust_image` / `adjust_image_opencl` (after `sat_rolloff`), both CPU
fallbacks, `apply_adjustments` + the area path, `ADJUSTMENT_KEYS` right after
`"sub_saturation"`, `SYNC_GROUPS` "sat", `_BASIC_LABELS`.

## Tests

- 0 is the identity; greys and colours at an anchor or a span's middle stay
  put; luma kept; no clipping.
- Hues near an anchor move towards it, never past it; the hue map is
  monotone over the whole circle (no reordering), slope bounds as above.
- Hue spread within a family shrinks (measured on a band of hues around an
  anchor).
- numpy and OpenCL agree.
- Slider position, range, helper text, sync group, label.

## Revision 2026-10-16: families and reach from a right-click popup

- Right-click the **Colour Organics** label (as Highlights/Shadows): a popup
  (`widgets/organics_popup.py`) with the four families, each a tick box
  (off = no anchor there, its colours are left to the neighbouring families'
  spans), a colour swatch and a hue nudge of +-20 degrees (disabled while
  off), plus **Reach** 10..45 % (the warp's `w`, default 30) and Reset. The
  slider stays the strength. The label is underlined when the families are
  not the default, and its tooltip lists what changed.
- Stored per layer as `adjustment_settings["organics_shape"]`
  (`warm_on`/`warm_shift`, ..., `reach`); `normalize_organics_shape` returns
  None for the default, so default renders are unchanged. The nudge ranges
  can't overlap, so the anchors always stay in order.
- Travels like the Highlights/Shadows shape: re-attached on every slider
  rebuild (`_attach_curves`), loaded with the layer, one undo step per popup
  visit, synced with the **Saturation** group, its own paste row ("Colour
  Organics families (...)"), carried by copy; Reset All restores the default
  families.
- Pipeline: `organics_shape` appended last to `adjust_image` /
  `adjust_image_opencl`; the anchors and reach are no longer compiled into
  the kernel but passed in `params[28..34]` (count, anchors + wrap padded to
  5, reach), so any combination renders identically on both paths. With
  every family off it is the identity; with one, its span is the whole
  circle.

Tests (`tests/test_colour_organics_popup.py`): default is None, clamping,
anchors follow the families and stay ordered, a nudged centre is the new
fixed point, all-off is the identity, one family stays monotone round the
circle, the shape reaches the render (numpy = OpenCL), apply_adjustments
and area layers pass it, the popup writes it (one undo, survives a slider
move, Reset removes it), loading picks it up, paste row / paste / copy,
sync with and without the Saturation group.


## Revision 2026-10-21: Reach to 60 %, with the depth held

Asked for a longer Reach slider. Measured first: with a fixed strength the
warp's slope at an anchor is `1 - s (1 - w)`, so a wider reach also made the
pull shallower. The mean hue move peaked around w = 0.45, fell beyond 0.5
(the two neighbours' windows overlap and pull against each other) and was
zero at w = 1. Simply raising the maximum would have made the top of the
slider do less.

So the strength is now scaled by `(1 - W0) / (1 - w)` (`organics_strength_scale`,
W0 = the default 0.30), holding the anchor slope at 0.40 at full for every
reach, and the range is **10–60 %**. The warp stays monotone (its minimum
slope is that anchor slope); the maximum hue move grows from 0.069 of a span
at 30 % to about 0.095 at 50 % and 0.100 at 60 % (it flattens past ~60 %,
hence the cap). The scale is exactly 1.0 at the default reach, so default
renders are byte-identical; frames with a custom reach render somewhat
stronger than before (the user's choice). The kernel gets the scale folded
into the amount (`params[27]`), matching `_apply_colour_organics`.


## Revision 2026-10-22: Reach to 80 %

Asked to extend Reach again. Measured (mean hue move over the circle, full
amount): with the depth held, the pull peaks at about 60-70 % (6.1 deg) and
falls beyond (5.6 deg at 80 %), because the families' windows already cover
most of their spans. So past `ORGANICS_REACH_FIRM` (60 %) the centre also
firms: its slope falls linearly from the held 0.40 to `ORGANICS_FIRM_SLOPE`
(0.26) at 80 %, with `s = (1 - slope) / (1 - w)`. The pull then keeps rising,
to about 6.9 deg at 80 % (+13 % over 60 %), monotone throughout (minimum slope
= that centre slope). Reach 10-60 % renders exactly as before. A separate
Strength control was offered as the bigger lever (+50 % pull at 150 %) and
declined for now.


## Revision 2026-10-22 (b): the main slider to 150

The Strength lever, on the main slider: Colour Organics now runs 0–150.
`organics_strength(amount, shape)` = amount/100 × 0.85 × the reach's scale,
capped so the centre's slope `1 - s (1 - w)` never goes below
`ORGANICS_MIN_CENTRE_SLOPE` (0.10): the warp stays monotone, so hues never
swap order and gradients cannot band. At 30–60 % reach, 150 brings the centre
slope to about 0.11 (mean pull about 1.5× that at 100); at 80 % reach the cap
binds above roughly 130. Up to 100 the cap never binds, so existing edits
render exactly as before. The kernel receives the capped strength folded back
into an amount (`params[27]`), matching `_apply_colour_organics`.

## Revision 2026-10-23: shown 0–100, with 100 twice the old 100

Asked for: "make Colour Organics go to 100 but make 100 2× as strong as 100
is now".

- **Stored vs shown.** The amount is still stored in the old units, now
  0–200 (`ORGANICS_AMOUNT_MAX`), and the slider SHOWS half
  (`HalfShownSlider`, SCALE 0.5; `value()`/`setValue()` speak stored units
  like `HalfStepSlider`). So every catalog, copied setting and area layer
  renders exactly as before, with no migration: shown 50 is the old 100, an
  old 150 shows as 75. An odd stored value from the old slider shows as
  x.5 and is kept exactly until this slider itself moves (another slider
  rebuilding the settings never nudges it). The wheel moves one shown step;
  the paste row shows the shown number (`shown_value`).
- **What "strength" is.** The mean hue pull `|h' − h|` over the hue circle
  with the default families (`_organics_mean_pull`): at the default reach,
  old 100 = 3.9°, old 150 = 5.9° (1.51×). Twice (7.8°) cannot come from depth
  alone: the centre's slope would have to go below 0 (hues swap order, i.e.
  banding); at the 0.10 floor depth tops out at about 1.5×.
- **So past the floor the pull widens** (`organics_pull(amount, shape) →
  (strength, reach)`): up to where the slope floor binds (stored ≈ 151 at
  reach ≤ 60 %, ≈ 122 at 80 %) it returns exactly the old
  `(organics_strength, organics_width)`; past it the depth stays on the floor
  and the effective reach widens from the user's towards the reach of the
  largest monotone pull (`_organics_peak_width`, 0.63), the mean pull rising
  linearly to twice the old 100's at stored 200, or to that maximum if it is
  less. Measured, shown 100 vs the old 100: reach 10 → 2.00×, 30 (default)
  → 2.00× (effective reach 0.42), 45 → 1.72×, 60 → 1.52×, 80 → 1.22× (wide
  reach is already near the most a monotone warp can pull, so the top of
  the slider holds there). Cached per (amount, reach, scale); a few ms.
- **Kernel.** `params[27]` carries the pull's strength (as before) and
  `params[34]` its effective reach (`_organics_params(shape, width)`); the
  numpy path calls the same `organics_pull`. CPU == GPU (≤ 1 code).
- **Quality, measured.** Monotone everywhere (centre slope ≥ 0.10). This is
  a per-pixel hue warp (it predates the finishing tools' quality rules), so
  it stretches hue differences BETWEEN the families: peak hue stretch 1.26
  at old 100, 1.38 at old 150, 1.66 at shown 100 (default reach). On 13 real
  frames, colour grain (OKLab a/b high-pass, 16 px blocks) at old 100:
  median 0.88–1.04, p99 1.00–1.22; at shown 100: median 0.73–0.99, p99
  1.00–1.39. Moving it onto the decision view (decide the hue shift on the
  denoised view, rotate each pixel's own colour by it, so grain passes
  through at 1.00) is the fix if that matters; it changes existing renders
  at grain level and needs the warp in numpy on the OpenCL path, so it is
  left for a separate change.

## Revision 2026-10-23 (b): shown under Vibrance; Subtracted Sat removed

Colour Organics is shown directly under Vibrance again, above the Lab
Finish section (layout only: it is still created after Glow, which is what
`ADJUSTMENT_KEYS` zips with). The global Subtracted Sat slider is gone: the
per-colour Subtractive Saturations section covers it. Its key left
`ADJUSTMENT_KEYS` and the sat sync group, and `CCRImage` no longer passes
it, so a value stored by an older edit is dropped (chosen over keeping it
hidden). The processor stage stays, at 0.


## Revision 2026-11-02: measured on lab scans

The user sent RGB-light trichrome scans of Portra 400 negatives together with
the lab scans of the same frames, from two labs (12 lab scans + 9 usable
pairs from one lab, 7 + 3 from the other). Measured with them, the four
guessed families turned out to point the wrong way in several places, so the
families are replaced by the measured hue curve.

### What was measured

For every pair: our scan decoded and registered to the lab scan
(`core/look_fit`: the merge, the base as Convert finds it, SIFT + RANSAC over
the four flips, 6×6 block samples at ¼ lab size, flat and unclipped). Each
frame rendered through FreeCCR's own default render of a black-point
conversion (`adjust_image` on the windowed density base, negative brightness
baseline) after fitting that frame's six Channel Levels numbers (offset and
contrast per channel) to the lab scan, so exposure and colour balance are
the lab's and only hue rendering is left. Then the lab's HSV hue minus ours,
on samples coloured in both (HSV saturation > 0.15 ours, > 0.10 lab's),
weighted by the smaller saturation, binned by our hue.

Mid and bright tones (HSV value ≥ 0.35), both labs:

| our hue | lab 1 | lab 2 | old Organics, shown 50 |
|---|---|---|---|
| red-orange 0–30° | +5° | +7° | +5° |
| orange, skin 30–60° | +3° | +7° | −4° |
| yellow 60–90° | +1° | 0° | +3° |
| yellow-green, green 90–150° | +1° | +1 to +3° | −3 to −4° |
| cyan, sky 180–210° | 0° | 0° | +3° |
| blue 210–240° | −6° | −11° | −2° |
| magenta-red 330–360° | +13° | +27° | +5° |

- The labs turn reds towards orange, oranges and skin a little yellower,
  leave yellows, greens and sky where they are and pull blues back towards
  sky. Nothing in these frames is blue-violet, violet or magenta (240–330°):
  no measurement there.
- The old families (warm 25°, foliage 100°, sky 200°, blue-violet 265°)
  pulled skin towards red, greens towards yellow-green and sky towards blue.
- Hue error against the labs, RMS over 10° × value-band cells weighted by the
  area of picture they cover (every cell alike in brackets):

  | | lab 060940 | lab 148684250 |
  |---|---|---|
  | no Organics | 2.69° (4.57°) | 4.30° (8.14°) |
  | old Organics, shown 50 | 5.08° (5.23°) | 5.77° (9.94°) |
  | old Organics, shown 100 | 8.05° (8.12°) | 7.95° (12.46°) |
  | the curve, fitted on the OTHER lab | **2.14° (3.10°)** | **3.34° (6.74°)** |

  The old families were worse than off; a curve fitted on one lab predicts
  the other better than off on every measure, so it carries across labs.
- The best the old engine could do with free anchors (3 anchors at 70°,
  140°, 180°) still couldn't follow the reds or keep the sky still.
- Dark tones (value < 0.35): dark greens and blues go 10–35° more olive in
  the lab, but differently per lab and frame (~20° RMS either way, a curve
  neither helps nor harms there), so the curve does not depend on lightness:
  a hue × lightness table did no better across labs (5.0 vs 5.2, 3.6 vs 3.3).
  That tone-dependent part belongs to a fitted Film Look (or Channel
  Balance), not to a hue warp.
- Against the Cineon decode the per-lab curves disagree (fitted on one lab:
  4.0° on the other against 4.0° for identity), and the default render
  matches the labs' tones better (per-frame error 0.012–0.033 against
  0.017–0.076), so the default render is the reference.

### Goals / non-goals

- The slider applies the measured curve: stored 100 (shown 50) is the labs'
  measured amount, stored 200 (shown 100) twice it. Units unchanged
  (`HalfShownSlider`, `ORGANICS_AMOUNT_MAX` 200).
- No change to luma, chroma or saturation; greys untouched (same gate); no
  clipping (same gamut fit); never reorders hues.
- Not lightness dependent; no move where nothing was measured.
- Not a substitute for a Film Look: with a fitted look on, keep Organics at 0
  (the look already holds the lab's hue rendering).
- Old edits: the amount renders the new curve (the user's choice: "whatever
  is most accurate"); an old families shape (on/off, nudge, reach) is ignored.

### Data

`core/organics_measured.py`, written by `tools/fit_organics.py`:
`HUE_STEP` 10, `TABLE` = 36 knots (degrees of hue move at stored 100, knot k
at hue 10·k), plus the fit's provenance in comments. A Python module, not a
data file, so the build scripts need nothing new.

The fit: 10° hue bins × value bands (0.35–0.55, 0.55–0.75, ≥ 0.75) of the
weighted median move (≥ 25 samples), each cell weighted by its sample count
(the area of picture it covers), fitted with the same periodic Catmull-Rom
basis the render uses, a second-difference penalty round the circle
(smoothness 1) and a pull to 0 (so a hue with no data gets 0). Both labs.
Weighting cells alike (√n, capped) was tried first: it moved bright skies
2.4° that both labs leave alone and predicted lab 060940 from the other
WORSE than no curve by area (4.15° against 2.69°); by count every check
beats no curve. Smoothness 0.3–10 changed the cross-lab figures by < 3 %.

The measured table at stored 100 (degrees, every 10° from 0°): reds +1.8 to
+2.8, oranges and skin (20–60°) +3.1 to +4.1, yellows to greens +0.3 to +1.0,
sky (190°) +0.5, blues (200–230°) −1.3 to −2.5, violets and magentas ~0.
The reds measured +12° in few samples (350–0°, two cells); with so little
data the pull to 0 holds them near +2°: the Reds and oranges region can take
them further. Slope round the circle 0.79–1.10 at stored 100.

### Maths

Per pixel on display values (unchanged steps marked =):

1. = HSV hue `h`, saturation `s`, grey gate `g = smoothstep(0.06, 0.20, s)`.
2. Move `Δ(h)` = the periodic Catmull-Rom curve through the EFFECTIVE knots
   `T_k = a · m(10k) · TABLE_k`, with `a` = stored amount / 100 and `m(h)`
   the region amounts (below). Catmull-Rom is C¹ and passes through the
   knots, so stored 100 with default regions reproduces the measured knots.
3. `h' = h + g · Δ(h)`. Monotone: the slope along the circle is
   `1 + g·Δ'(h) ≥ min(1, 1 + min Δ')`; the knots are scaled down, if needed,
   so `1 + min Δ' ≥ ORGANICS_MIN_CENTRE_SLOPE` (0.10), measured on a 0.1° grid
   in float64 (cached per amount and regions). At stored 200 with default
   regions it does not bind.
4. = realised at the same Rec.601 luma and chroma, `grey + |d|·u(h')/|u(h')|`,
   then fitted into gamut towards the grey.

**Regions** (`organics_shape`, right-click popup): Reds and oranges
(330–60°), Yellows and greens (60–165°), Cyans and blues (165–250°), each
0–200 % (default 100), crossfaded with raised cosines 30° wide centred on the
borders; 250–330° (violets and magentas) has no measurement and no control.
`m(h) = Σ k_r w_r(h)`, the weights summing to 1 everywhere.

### Pipeline / integration

Same place and plumbing as before (`colour_organics`, `organics_shape`
appended at the END of `adjust_image` / `adjust_image_opencl`; area layers,
sync group "sat", paste row, copy, undo per popup visit). Kernel:
`params[27]` the stored amount (> 0 enables), `params[28]` 1 when the curve is
active, `params[29..64]` the 36 effective knots; the anchor/reach slots go.
Removed: `ORGANICS_FAMILIES`, the anchored warp (`organics_hue_map`),
`organics_pull` / `organics_strength` and the reach machinery. New:
`organics_knots(amount, shape)`, `organics_delta(hue, knots)`,
`ORGANICS_REGIONS`.

### UX

- Tooltip: what it does ("Turns hues the way lab scans do: reds towards
  orange, skin a little warmer, blues back towards sky. Measured on lab scans
  of colour negatives; 50 is their own amount.") No brand or product named.
- Right-click popup: "Colour Organics by region", a line saying 100 % is as
  measured, three rows (swatch, name, slider 0–200 % in 5 % steps, value),
  a muted line "Violets and magentas: not measured yet, left as they are",
  Reset. The label is underlined and its tooltip lists the regions changed.

### Quality (spec rule list)

No-op at 0 (not called); pixels the gate skips are returned exactly; float32
inside; C¹ curve; monotone (step 3); luma and chroma kept, no clipping; the
hue stretch is the curve's slope, max reported in tests and in the tool's
report; resolution independent; CPU == GPU within 3 codes. Grain: a hue
stretch above 1 widens colour grain in hue by that factor; the measured
curve's steepest rise at stored 100 is 1.10 (1.20 at 200; the old warp was
1.26 at its old 100 and 1.66 at shown 100). Measured on four real renders
(OKLab a/b high-pass, 16 px blocks, as revision 2026-10-23): colour grain
median ×1.00–1.01 and p99 ×1.02–1.06 at stored 100, median ×1.00–1.02 and
p99 ×1.04–1.12 at 200 (the old warp: p99 up to ×1.22 at its 100 and ×1.39 at
shown 100). Mean move 0.3–1.2 levels of 255 at stored 100.

### Tests

`tests/test_colour_organics.py` (rewritten): 0 is the identity; greys
untouched, luma kept, in range; at stored 100 the knots are the table; reds
(355°) and skin (40°) move up, blues (220°) down, sky (195°) and green (120°)
within 2°; nothing moves at 250–330°; monotone round the circle for every
amount and random region settings, slope ≥ 0.10; a region at 0 % leaves its
hues alone and doesn't touch the others' centres; the pixel lands on the
mapped hue; numpy == OpenCL; slider, tooltip, sync group. Popup tests
rewritten for regions (default None, clamping, old-format shapes ignored,
writes the shape with one undo, Reset, paste/copy/sync). The fitter's pure
part (`fit_curve` on cells) recovers a known curve and gives 0 where there is
no data.

### Refinement (before implementing)

- **Which tones feed the fit**: value ≥ 0.35 only (the dark cells disagree
  between labs; including them made the cross-lab check worse than identity).
- **Region borders** sit where the measured curve is small (165°: +0.6°,
  250°: −0.2°, 330°: 0°) except 60° (+3.2°, the warm move fading into the
  yellows); the 30° crossfade keeps that join smooth (the curve is
  multiplied by a C¹ weight), and the cap keeps it monotone.
- **Cap**: computed on the effective knots, so a region pushed to 200 % can
  only ever reduce the whole curve's scale, never reorder hues.
- **Old shapes**: `normalize_organics_shape` keeps only the three region
  keys; a dict with none of them is the default (None), so old catalogues
  load without warnings and paste rows don't show stale families.

## Revision 2026-11-09: three labs, each counted the same

The user sent a Noritsu roll of Portra 400 (36 JPEGs, roll 00003875) with
his own trichrome scans of 16 of its frames (R0001 001–012, R0002 001–004),
plus two more lab-060940 pairs. Paired automatically as before; 33 pairs in
all (060940: 10, 148684250: 7, 00003875: 16). Frames whose film base was
clipped (the per-frame exposure lengthens the shot) were not skipped this
time: their base is the roll's base per unit of the shot's shortest exposure
(constant within ±4 % across a roll: R0001 2.54 / 1.92 / 2.91) times this
shot's, and the clipped pixels are left out (1–13 % of a picture). One of
the two new "pairs" (DSCF0010–0012) shows another picture (a tree and a
pale wall) than its TIFF (060940010017, buildings) and was left out.

**Measured, by lab** (area-weighted mean hue move, degrees, by our hue):

| our hue | 060940 | 148684250 | 00003875 (Noritsu) |
|---|---|---|---|
| 0–30 | +5.4 | +5.6 | +1.9 |
| 30–60 (skin, orange) | +3.6 | +5.1 | +0.6 |
| 60–90 | +1.3 | +2.3 | +0.5 |
| 90–150 (greens) | +1.1 to +1.2 | +2.4 to +3.7 | +3.9 to +5.9 |
| 150–180 | +0.4 | −3.8 | +5.1 |
| 180–210 (sky) | 0.0 | −0.6 | −0.1 |
| 210–240 (blue) | −5.9 | −9.2 | −4.9 |
| 330–360 (magenta-red) | +12.0 | +15.1 | +6.6 |

All three turn reds and magenta-reds towards orange and blues back towards
sky, and leave sky alone; the Noritsu roll leaves skin and oranges where they
are and turns greens towards cyan more than the others. Still nothing
measured at 240–330° (violet, purple, magenta): 16 samples in all, on 33
frames.

**Change.** The curve is fitted on all three labs with each lab's cells
scaled to the same total weight (`organics_fit.equal_weight`;
`tools/fit_organics.py` does this by default now, `--by-samples` for the old
weighting), so the lab with the most frames doesn't decide it. Smoothness 1
as before. RMS by area (degrees):

| | 060940 | 148684250 | 00003875 | mean square over labs |
|---|---|---|---|---|
| no curve | 2.92 | 3.57 | 2.56 | 3.05 |
| previous table (fitted on the first two) | 1.44 | 2.39 | 2.56 | 2.19 |
| new table | 1.58 | 2.51 | 2.10 | 2.10 |
| each lab predicted from the other two | 1.77 | 2.73 | 2.54 | |

The new table at stored 100: reds +2.1 to +2.6, skin and oranges +1.9 to
+3.3, yellow-greens +0.5 to +0.9, greens +1.1 to +1.5, sky +0.4, blues −1.0
to −2.4, violet and purple ~0, magenta-reds +0.7 to +2.2. Slope round the
circle 0.84–1.09. A lab's own rendering (the Noritsu roll's skin and greens)
belongs in a Film Look fitted on that lab, not here.
