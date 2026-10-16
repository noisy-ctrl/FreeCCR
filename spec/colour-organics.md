# Colour Organics

## Goal

Lab scanners render colour for print: neighbouring hues are drawn into a
few families (warm skin/wood/brick, foliage, sky/water, blue-violet) and the
transitions between families open up, which reads as cohesive and natural.
A neutral digital rendering keeps hues evenly spread, which can feel
"digital". Colour Organics does the grouping, with one amount slider.

The hue families are an informed guess, not a measurement of Noritsu: they
follow the structure measured in the measured base rendering (HALD export,
2026-10-16), which compresses hue differences around warm (0-55 degrees),
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
