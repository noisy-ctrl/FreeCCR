# Saturation Roll-off

## Goal

A soft ceiling on strong colours: the most saturated colours are eased back
so they don't go neon or clip, while muted colours and greys are untouched.
This is what the measured render calls "chroma compression" (measured from Miles's HALD
exports, 2026-10-16: a knee on saturation, colours under about half
saturation unchanged, lightness kept); FreeCCR had no equivalent (Vibrance
is the opposite shape: it moves muted colours and leaves strong ones).

## Non-goals

- No expansion (negative values): FreeCCR has no default compression to
  release, so the slider is 0..100.
- Not hue-selective: that is what the per-colour bands are for.

## UX

- Slider **Saturation Roll-off**, 0..100, default 0, directly under
  Saturation (above Vibrance). Double-click resets.
- Tooltip: "Eases back the strongest colours so they don't go neon or clip.
  Muted colours and greys are untouched; brightness is kept."
- Syncs and pastes with the **Saturation** group; works on area layers.

## Maths

Per pixel on display values `x` in [0,1]:

- `s = (max - min) / max`, HSV saturation of the display values (the
  perceptual measure the knee is defined on).
- Amount `a = slider / 100`. Knee `K = 1 - 0.5 a`, ceiling `M = 1 - 0.25 a`.
- For `s <= K` nothing changes. Above it,
  `s' = K + (M - K) (1 - exp(-(s - K) / (M - K)))`: slope 1 at the knee (no
  kink), approaching `M`; at `a = 1` a fully saturated colour lands at 0.72
  (the measured default compression measured about 0.71).
- Two exact steps. (1) Lower the saturation to `s'` with the brightest
  channel and the hue fixed: `c = max - (max - x) s' / s` (raises the other
  channels, so `c >= x`). (2) Scale the whole colour, which keeps hue and
  saturation, back to the original **linear-light luminance**:
  `f = (Y(x) / Y(c))^(1/2.2)` with `Y = 0.2126 R + 0.7152 G + 0.0722 B` on
  `value^2.2`, `x' = f c`. Because `c >= x`, `f <= 1`: never clips.
  Rejected while building it: desaturating towards a luma-on-display-values
  grey (a strong blue goes nearly black) and mixing with the same-luminance
  grey in linear light (shifts the display hue by up to 3.5 degrees).

## Pipeline position

After Saturation, Vibrance and Subtracted Sat (so it also catches what they
push up) and after Colour Organics when that exists; before the per-colour
bands (a user's deliberate per-colour boost wins). Same place in the OpenCL
kernel (`params[26]`, the next free slot), constants substituted textually
like Vibrance's. Guarded on `max > 1e-6` (black has no saturation).

## Integration points

- `adjust_image` / `adjust_image_opencl`: `sat_rolloff` appended at the END
  (positional callers), passed through both CPU fallbacks.
- `CCRImage.apply_adjustments` and the area path pass
  `sat_rolloff=s.get('sat_rolloff', 0)`.
- `SlidersPanel.ADJUSTMENT_KEYS`: `"sat_rolloff"` right after `"saturation"`,
  with its `create_slider` call right after Saturation's; `SYNC_GROUPS`
  "sat"; `_BASIC_LABELS`.

## Tests

- 0 is the identity; greys and colours below the knee are untouched.
- A fully saturated colour lands on the curve's value (HSV saturation of the
  result), monotone in input saturation, never clips, hue kept.
- Linear-light luminance kept (pure blue does not go dark).
- numpy and OpenCL agree (where OpenCL is available).
- Slider position, range 0..100, sync group, label.

## Revision 2026-10-16: centred, -50..+50

- The slider is **-50..+50 with 0 in the middle**, like the other colour
  sliders. `a = clamp(value / 50, -1, 1)`, so +50 is the old 100.
- **Right (a > 0)**: the roll-off above, unchanged.
- **Left (a < 0)**: strong colours made punchier. Same knee
  `K = 1 - 0.5 |a|`; above it `s' = s + 0.9 |a| (1 - K) u^2 (1 - u)` with
  `u = (s - K) / (1 - K)`: slope 1 at the knee, `1 - 0.9 |a|` (>= 0.1) at
  `s = 1`, which stays fixed, so the curve is monotone with no plateau. The
  largest push is `0.9 |a| (1 - K) 4/27`, about 0.07 at full: gentle by
  design, since strong colours already sit near clipping. Realised by the
  same two steps; lowering the other channels raises the luminance factor
  `f` above 1, so it is capped at `1 / max`: hue and saturation exact,
  luminance exact unless the brightest channel would pass 1, never clips.
- Why two-sided makes sense: muted colours and greys are untouched either
  way, so it is the strong-colour counterpart of Vibrance (which works on
  muted ones), and the measured chroma compression is two-sided the same way.
- Values set while the slider was 0..100 now act twice as strong (anything
  above 50 clamps to 50).
- `params[26]` carries the signed value; `ROLLOFF_SCALE`/`ROLLOFF_PUNCH` are
  substituted into the kernel like the other constants.

Tests: the left curve is monotone, untouched below the knee, fixed at 1 and
no stronger than 0.067; hue kept, never clips, luminance kept unless capped;
out-of-range values clamp; OpenCL matches on both sides; the slider range is
-50..+50 at 0.
