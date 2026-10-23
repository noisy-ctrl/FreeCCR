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

