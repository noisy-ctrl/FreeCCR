# Shadow Lift (Lab Finish)

Revision 2 (2026-10-23). Lifts black a little, the way a lab print's black
is a deep grey rather than pure black: the deepest shadows come up as they
are, grain and all, and the lift rolls off smoothly into the shadows. A
slider in the Lab Finish section, between Shadow Neutralise and Glow.

Revision 1 opened the shadows (a tone curve lifting shadow detail, black
fixed) with lifting black as an option. The user's direction: the Shadows
and Black Point sliders already do the opening; what is wanted is the quick
lift of black alone, with a good roll-off, nothing sudden.

## Goals / non-goals

- **Shadow Lift** 0..100 (default 0), key `shadow_lift`, in the sync group
  with the other Lab Finish tools ("Saturation & Lab Finish", id `sat`).
  At 100 black sits at 0.10 display (25.5 / 255); linear in the slider
  (40 = 10 / 255).
- Right-click: **Roll-off** (display level the lift has faded out by, 35–80 %,
  default 50 %, "Shorter" ↔ "Longer"), **Show where it acts** (green), Reset.
  Stored as `adjustment_settings["shadow_lift_shape"]` = `{"rolloff": n}`,
  None when default. Revision 1's `range` / `black` keys are ignored.
- Non-goals: opening shadow detail (Shadows / Black Point / Shadows L do
  that), any colour change beyond the neutral lift, local recovery.

## Processing (`core/shadow_lift.py`)

On the display values (sRGB-encoded 0..1), float32, per pixel; a tone curve
on the pixel's own luma, so there is no decision for grain to steer:

    Y    = Rec.709 luma of the display values
    lift = v0 · (1 − smoothstep(Y / Vf)),  v0 = amount/100 · 0.10,  Vf = Roll-off
    out  = rgb + min(lift, 1 − max(rgb))   (the same on R, G and B)

- Neutral: the same amount on all three channels, as raising a curve's
  black point does, so a dark colour keeps its hue and loses a little
  saturation, the matte look.
- Along grey the slope is `1 − v0·S'(u)/Vf`: exactly 1 at black (so the
  deepest shadows and their grain move up unchanged; a noisy patch at 2 %
  keeps its grain to within 1 %), never below `1 − 1.5·v0/Vf` (0.70 at full
  lift and the default Roll-off; 0.57 at the shortest, 0.81 at the longest;
  ≥ 0.87 at 30), and 1 again where the lift ends: C¹, nothing sudden.
- A pixel whose brightest channel would pass 1 takes only the lift that
  fits (a saturated blue at the top of its channel is left as it is).
- Above the Roll-off (`Y ≥ Vf`) the pixel comes back exactly.

## Pipeline

After Shadow Neutralise and before Glow, in `adjust_image`, in the numpy
finishing on the OpenCL result, and in `core/finish_gpu.py` (the
`lift_apply` kernel: the same smoothstep and add) on a GPU. `shadow_lift`,
`shadow_lift_shape` appended LAST to `adjust_image` / `adjust_image_opencl`
(unchanged from revision 1); `CCRImage` passes them from each layer.

## UI / data model

Plumbed exactly like `glow_shape` (load, attach on rebuild, reset, one undo
per popup, the Lab Finish sync group, copy, its own paste row). The popup
(`widgets/shadow_lift_popup.py`) has Roll-off, a before / after strip of a
dark gradient, Show where it acts (the shared tool-mask layer, GREEN: the
share of black's lift each pixel gets), Reset.

## Tests (`tests/test_shadow_lift.py`)

0 is a byte-identical no-op; tones above the Roll-off untouched exactly;
black goes to 25.5 / 10.2 / 2.55 levels at 100 / 40 / 10, neutrally, never
darker; the grey axis is monotone and C¹ with slope 1 at black and the floor
above; a noisy near-black patch keeps its grain; colours at the top of a
channel take only what fits; options normalised (old keys ignored); CPU ==
GPU within one 16-bit code; panel: slider in Lab Finish between Shadow
Neutralise and Glow, popup writes the option with one undo, paste row, copy,
sync, reset, mask follows the popup.
