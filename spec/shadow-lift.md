# Shadow Lift (Lab Finish)

Opens up the shadows the way a lab print does: shadow detail comes up while
pure black stays black. A right-click option also lifts black itself, for a
matte, faded-print look. A slider in the Lab Finish section, between Shadow
Neutralise and Glow.

## Goals / non-goals

- **Shadow Lift** 0..100 (default 0), key `shadow_lift`, in the sync group with the other Lab Finish tools
  ("Saturation & Lab Finish", id `sat`).
- Right-click: **Range** (how far up the tones it reaches, "Narrow" ↔
  "Wide"), **Lift black** (0 = black stays black; up to a matte black,
  "None" ↔ "More"), **Show where it acts**, Reset. Stored as
  `adjustment_settings["shadow_lift_shape"]`, None when default.
- Non-goals: local (spatial) shadow recovery; this is a tone curve, the
  same for every pixel of the same tone.

## Processing (`core/shadow_lift.py`)

Per pixel, in OKLab on the display values, float32 (no spatial decision:
a tone curve depends only on the pixel's own tone, so there is nothing for
grain to steer).

1. **Open.** `R = range/100` (OKLab L, default 0.45, limits 0.25–0.65),
   `t = L/R`, `w = (1 − t)²` below R, 0 above (C¹ at R).
   `s = 1 + a·w`, `a = amount/100` (full = 1). `L1 = s·L`, and the colour
   with it: `a, b × s`, so a lifted shadow keeps its saturation
   (`C/L`) instead of greying. Black (L 0) stays 0.
   Monotone: `dL1/dL = 1 + a·(1 − t)(1 − 3t) ≥ 1 − a/3 ≥ 0.67`; steepest at
   black, `1 + a` (2 at full). Peak lift `a·R·4/27` at `L = R/3` (0.067 at
   default).
2. **Lift black** (option `black`, 0..100): `F = a·0.16·black/100` (OKLab L;
   full ≈ 15/255 display), faded out over `Rf = max(R, 0.50)`:
   `L2 = L1 + F·(1 − L1/Rf)²`, colour unchanged (neutral light added, as a
   matte print's black). C¹ at Rf. The fade is `(1 − u)²`, not a
   smoothstep: its flattening is greatest at black, where the opening is
   steepest (slope `1 + a`), so the two don't stack. With a smoothstep the
   flattening peaks mid-span, on top of the opening's own shallowest point
   (`L = 2R/3`), and the combined slope fell to 0.37 at the defaults with
   Lift black at full; with `(1 − u)²` it is ≥ 0.52 at the default Range and
   ≥ 0.39 at the narrowest with every option at full (a strong matte; still
   smooth and monotone).
3. Out of range → the largest fraction of the move that fits
   (`core.oklab.fit_move`); untouched pixels (`w = 0` and no lift there)
   are returned exactly.

Grain: a lift raises the shadows' slope, so their grain comes up with them
(by the same factor as the tones, at most `1 + a`); that is what opening
the shadows means. Nothing decides on grain, and colour noise is scaled
exactly as the colour is, so no extra noise appears.

## Pipeline

After Shadow Neutralise and before Glow (the tones are set before the light
spreads), in `adjust_image`, in the numpy finishing on the OpenCL result,
and in `core/finish_gpu.py` (the `lift_apply` kernel, the same maths) on a
GPU.
`shadow_lift`, `shadow_lift_shape` appended LAST to `adjust_image` /
`adjust_image_opencl`; `CCRImage` passes them from each layer's settings.

## UI / data model

Plumbed exactly like `glow_shape` (load, attach on rebuild, reset, one undo
per popup, the Lab Finish sync group, copy, its own paste row). The popup
(`widgets/shadow_lift_popup.py`) has Range / Lift black, a before/after
strip of a dark gradient, "Show where it acts" (the shared tool-mask layer,
in GREEN), Reset.

## Tests (`tests/test_shadow_lift.py`)

0 is a byte-identical no-op; tones above Range untouched exactly; black
stays black without Lift black and rises to about F with it; the grey axis
is monotone and smooth across Range and Lift black; saturation kept in the
opened shadows (C/L); in range; CPU == GPU; panel: slider in Lab Finish
between Shadow Neutralise and Glow, popup writes the options with one undo,
paste row, copy, sync (Lab Finish), reset, mask follows the popup.
