# Vibrance slider

## Goal

Add a **Vibrance** slider directly below **Saturation**: a saturation control
that boosts muted colours strongly, leaves already-saturated colours almost
alone, never touches neutrals, and partly protects skin tones. This is the
familiar Lightroom/Photoshop pairing: Saturation is uniform(ish), Vibrance is
selective.

## Non-goals

- No change to the existing Saturation or Subtracted Sat maths.
- No new sync group or catalog schema: `vibrance` is an ordinary adjustment
  key stored in `adjustment_settings` like every other slider. Catalogs written
  before this feature simply lack the key, which defaults to 0 (identity).

## UX

- Range −100…+100, default 0, double-click resets (standard `create_slider`).
- Placed between Saturation and Subtracted Sat.
- Syncs and copy/pastes with the **Saturation** group (`"sat"`).
- Works on area layers too (same settings dict).

## Maths

Per pixel, on normalised `[0,1]` RGB:

```
mx, mn   = max(R,G,B), min(R,G,B)
sat      = (mx − mn) / mx                     # HSV saturation, 0 for neutrals
hue      = HSV hue in degrees
d        = circular distance(hue, 25°)
protect  = 1 − 0.5 · exp(−(d / 20°)²)         # skin band keeps ≥ 50% of the effect
weight   = (1 − sat) · protect
scale    = 1 + (v / 100) · weight
gray     = 0.299 R + 0.587 G + 0.114 B        # same luma axis as Saturation
out      = clip(gray + scale · (rgb − gray), 0, 1)
```

At +100 a nearly neutral colour roughly doubles its chroma; a fully saturated
one is unchanged. Negative values pull muted colours towards grey faster than
saturated ones. Constants (`VIBRANCE_STRENGTH`, `VIBRANCE_SKIN_HUE`,
`VIBRANCE_SKIN_WIDTH`, `VIBRANCE_SKIN_PROTECT`) live in `ccr_processor.py` and
are textually substituted into the OpenCL kernel so the two paths cannot drift.

## Pipeline position

Immediately after Saturation, before Subtracted Sat and the colour bands, in
both `adjust_image` (numpy) and the OpenCL kernel.

## Integration points

- `adjust_image` / `adjust_image_opencl`: `vibrance` appended at the **end**
  of both signatures (they are called positionally in tests).
- OpenCL `params[25]`, after the band enable flag at `params[24]`, so no
  existing index moves. Both CPU fallbacks forward `vibrance`.
- `CCRImage.apply_adjustments` and `_adjust_for_area` pass
  `vibrance=s.get('vibrance', 0)`.
- `SlidersPanel.ADJUSTMENT_KEYS`: `"vibrance"` directly after `"saturation"`,
  with the matching `create_slider("Vibrance")` call directly after
  Saturation's (positional zip).
- `SYNC_GROUPS["sat"]` gains `"vibrance"` (keeps the partition test green).

## Test plan

`tests/test_vibrance.py`: identity at 0; neutrals, black and white unmoved;
muted colours gain more chroma than saturated ones; negative reduces chroma;
skin hue gains less than a non-skin hue of equal saturation; CPU/GPU parity
alone and combined with Saturation, Subtracted Sat and Contrast; key ordering
and sync-group membership.
