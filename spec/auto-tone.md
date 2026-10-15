# Auto tone (keep shadow and highlight detail)

## 1. Problem

"Shadows are often mega crushed. Whites are still blown out. Some images
turn out beautiful … I assume these images are nice because the stuff that's
clipping (under and over) is relatively unimportant details. Can we optimise
a bit more generally to preserve highlights and shadows, especially if
there's a lot of the image going too far?"

Measured on 30 converted frames (with auto white point):

- **Highlights.** On a windowed base the display value is density over the
  white point's density, so anything denser than the white point sits above
  1 and is cut by the window clamp. Typically 1–3 % of a picture, up to
  12 % (a bright sky), had a channel above 1.
- **Below black.** Up to 15 % of a picture sat *below* 0: clearer than the
  measured film base. The base is read off the rebate at the frame edge, and
  light falls off there, or a frame used the roll's base. That content is
  flat black however the sliders are set.
- **Near black.** The negative look's brightness baseline is a power curve
  (x^1.3), whose slope at black is zero, so the lowest densities pile up
  just above black: 5–35 % of some pictures render at or below 12/255.

## 2. Goals / non-goals

- Per frame and automatic: a frame that loses little at either end is left
  as it is (the "beautiful" ones); one that loses a lot gets its shadows and
  highlights rolled into the range instead of clipped.
- Black stays black and white stays white: this is a toe and a shoulder,
  not a contrast reduction.
- Measured once, when the frame is converted, so preview, zoom and export
  agree and dragging a slider never re-measures.
- Settings → General → "Auto tone (keep shadow and highlight detail)",
  default on (`adjust/auto_tone`). Turning it off re-renders; nothing is
  re-converted.

Non-goals: changing the default look's curve or the black/white points;
local (spatial) tone mapping; positive mode.

## 3. Measurement (`core/auto_tone.measure`)

On the converted base's display values (de-windowed, times the Auto Gain the
render applies), inside the picture area recorded at conversion
(`conversion_inputs["picture"]`, else the crop / auto crop result; with none,
nothing is measured, since the rebate and holder would read as crushed
shadows and blown highlights). Up to 1 M pixels (strided).

- **lift**: if the picture's 0.5th luminance percentile `p` is below 0,
  `lift = min(-p, 0.15)`; values map `(x + lift) / (1 + lift)`. A density
  offset that puts the clearest picture content at black, white kept.
- **toe**: `dark` = share of the picture with luminance under 0.09 (about
  12/255 once the x^1.3 baseline is applied). If over 3 %,
  `toe = min(1, (dark - 0.03) * 4)`.
- **shoulder**: `over` = share with any channel above 1. If over 0.5 %:
  `strength = min(1, (over - 0.005) / 0.035)`;
  `top = 1 + (min(p99.7(max channel), 1.3) - 1) * strength` (lamps and glints
  beyond 1.3 are let clip, and a few small highlights get a gentle shoulder);
  `knee = clip(1 - 1.25 * (top - 1), 0.7, 0.9)`.

Stored as `conversion_inputs["auto_tone"] = {lift?, toe?, knee, top, sigma}`,
or absent when nothing is needed.

## 4. The curves (`core/auto_tone.apply`)

On display values, the same curve on each channel:

1. lift as above;
2. toe: `x + toe * x * (1 - x / 0.35)^2` on `[0, 0.35]`, so the slope at
   black is `1 + toe` and it blends into identity at 0.35 (monotone for
   toe < 3); below black the same slope carries on, keeping the margin;
3. shoulder above the knee: with `X = (x - knee) / (1 - knee)` and
   `u = (top - knee) / (1 - knee)`, `Y = sigma * (1 - exp(-X / sigma))`
   where `sigma * (1 - exp(-u / sigma)) = 1`: slope 1 at the knee (no kink)
   and `top` lands exactly on white.

## 5. Pipeline position

In `_apply_working_space_recovery`, after every gain (Channel Levels,
Balance, Master Gain with Auto Gain, White Balance, White Point, exposure)
and immediately before the window clamp it softens. The curve is fixed per
frame, so a Master Gain or White Point move pushes content into or out of it,
like exposing onto paper. `auto_tone` is appended at the end of
`adjust_image` / `adjust_image_opencl` / `_apply_working_space_recovery`
(positional callers), and the OpenCL path gets it through the shared numpy
pre-stage, so GPU/CPU parity holds. Not applied with a Film Look (it owns its
tone), on non-windowed bases, or to area layers (they grade the clamped
result).

Measured in: Convert Current, Convert All (both paths), and again after the
density-mode reprocess. A slice inherits its parent's; un-slicing keeps it.
A converted frame from before this has none until converted again.

## 6. Results (same 30 frames)

Clipped highlights (any channel at 254+, share of picture): 11.9 → 0.8 %,
6.9 → 0.5 %, 6.7 → 0.4 %; frames under 1 % unchanged. Shadows at or below
12/255: 35 → 20 % (a back-lit silhouette, rightly still dark), 19 → 13 %,
16 → 12 %; frames with little near black unchanged.

## 7. Tests

`tests/test_auto_tone.py`: a frame with nothing at either end measures
None and renders unchanged; content below black is lifted and white stays;
the toe keeps 0 at 0, raises the slope there, is monotone and identity above
0.35; the shoulder is continuous with slope 1 at the knee, maps `top` to 1
and is monotone; strength follows the share above white; lamps beyond 1.3 do
not drag the knee down; nothing is measured without a picture area, and
re-measuring a base that no longer needs it drops it; the render passes it
only while the setting is on, and leaves values below the knee untouched.
