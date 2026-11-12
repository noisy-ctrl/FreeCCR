# Instagram export preset

The user, 2026-11-10: Instagram "slightly ruins the colours of some uploads"
of his 2000 × 2000 files; he asked for an Instagram option in Export.

## Why these settings

- Instagram stores feed photos 1080 px wide. A larger upload is shrunk by its
  own (soft) resizer and then recompressed; at exactly 1080 wide it is only
  recompressed, so the sharpening and grain stay ours.
- Portrait posts show at up to 4:5 (1080 × 1350) or, in the newer grid, 3:4
  (1080 × 1440); a taller frame is cropped by Instagram. A 2:3 negative is
  taller than both, so it goes on a canvas rather than being cropped.
- sRGB with the profile embedded (FreeCCR always embeds it): an Adobe RGB,
  ProPhoto or untagged file is converted by Instagram, the usual cause of
  shifted colours.
- A plain JPEG, not the HDR gain-map JPEG (shown differently on different
  phones).
- Quality 95: FreeCCR writes full-resolution colour (4:4:4) at 90 and above;
  Instagram recompresses anyway, so higher only makes the upload bigger.

## UX

- A **Preset** list at the top of the Export window's format rows: "None",
  "Instagram 4:5 (1080 × 1350)", "Instagram 3:4 (1080 × 1440)", "Instagram
  square (1080 × 1080)". Remembered (`export/preset`).
- Choosing an Instagram preset sets and locks: Format JPEG, Colour space
  sRGB, Quality 95, Border on with that canvas, Image long edge = the
  canvas's long side (the frame is as large as fits). Resize is already
  locked by the border. Border colour and the new **Border width** stay
  yours. A hint under the list says what it does.
- The locked values are never saved over your own (as with the linear
  merge): choosing "None" puts your own settings back from QSettings.
- **Border width** (`export/canvas_margin`, px, 0–500, default 0): the
  least border round the frame, for any canvas. With 0 the frame may touch
  the canvas's edges (the old behaviour, byte for byte). An Instagram preset
  starts it at 32 px when it is 0.

## Processing

- `export_canvas.Canvas` gains `margin: int = 0` and
  `sharpen: Tuple[int, int] = (0, 0)` (amount, radius in the Details
  sliders' units), both defaulted, so every existing canvas is unchanged.
- `placed_size` fits the frame inside `(width − 2·margin) × (height −
  2·margin)` as well as at most `long_edge`; never cropped, never 0.
- **Screen sharpening**: `compose` sharpens the frame AFTER its resize to the
  canvas and BEFORE it is placed (so the border edge gets no halo), with
  `apply_sharpening(frame, amount, radius, 0, scale=1)` (luma only). The
  Instagram presets use (25, 35): σ 0.7 output px, measured on the user's
  R0003_002 export at 1080 × 1350: finest detail ×1.15, 1–2 px detail
  ×1.05, largest halo ~23 levels at hard edges. The frame's own Details
  sharpening (at full resolution, `spec/sharpen-like-output.md`) still
  applies before it.
- Nothing else in the export changes: the canvas already runs after the crop
  and orientation and before the colour-space step.

## Tests

- `placed_size` with a margin: a 3:2 landscape and a 2:3 portrait on
  1080 × 1350 with 32 px keep ≥ 32 px on every side and touch the inner box.
- margin 0 and sharpen (0, 0) are the old results exactly.
- `compose` with sharpening: the border is exactly the colour (no halo), the
  frame's fine detail grows.
- The dialog: choosing a preset sets and locks the controls and builds a plan
  with JPEG, sRGB, 95, the canvas (size, margin, sharpen); "None" restores
  the saved custom values; saving with a preset on doesn't overwrite them;
  the preset is remembered.

## Revision 2026-11-12: the long edge is yours

The preset fixes the canvas size but no longer locks **Image long edge**: it
starts as large as fits (the canvas's long side) and what you set is kept per
preset (`export/<preset>/long_edge`), never over your own no-preset value.
Border width stays the least border, so the frame is the smaller of the two;
the Border readout says which one limits it ("the border width limits it" /
"the canvas size limits it").
