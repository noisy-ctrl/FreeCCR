# Glow (Lab Finish)

A soft highlight glow: bright areas bloom gently into their surroundings,
like a diffusion filter on the enlarger or the soft spread of light round
highlights in a good print. A slider in the Lab Finish section, under
Shadow Neutralise, with right-click options.

## Goals / non-goals

- **Glow** 0..100 (default 0), key `glow`, "sat" sync group (with the other
  Lab Finish tools), created right after Shadow Neutralise.
- Right-click: **Size** (how far the light spreads, "Tight" ↔ "Wide"),
  **Start** (how bright a tone must be to glow, "Lower" ↔ "Higher"),
  **Show where it acts** (the glow field as an overlay), Reset. Stored as
  `adjustment_settings["glow_shape"]`, None when default.
- Non-goals: halation (a red-orange fringe from the film base) and the HDR
  export's gain-map glow, which is a different thing (it is not in the
  picture's pixels).

## Processing (`core/glow.py`)

On display values (sRGB-encoded 0..1), float32 throughout, light in LINEAR
light (glow is light adding up):

1. **Sources.** `b = lin · s(L)`: each pixel's linear RGB times a soft knee on
   its lightness, `s = smoothstep((L − (start − 0.10)) / 0.20)` (OKLab L of
   the decision view, so grain can't switch a pixel on and off). Only tones
   round and above Start give light; it keeps their colour, so warm sunlight
   glows warm.
2. **Spread.** The sources are box-averaged down (energy-conserving) to a
   size where the narrow kernel is still ≥ 4 px, blurred with two Gaussians
   — σ and 3σ, weighted 0.65 / 0.35, a natural core with a longer tail like
   real diffusion — and scaled back up bilinearly. σ = Size as a fraction of
   the picture's long side (0.3 % at 0 to 4 % at 100, exponential; default 40
   → about 0.9 %), so the glow is the same at every render size. The field is
   smooth by construction (blurred far below its sampling), so scaling it up
   leaves no steps; a test checks second differences at export size.
3. **Hold the highlights' own detail.** `g = k · glow · (1 − h(L_h))`,
   `h = smoothstep((L_h − (start − 0.20)) / 0.40)`: a bright area takes none
   of its own glow (from Start + 0.2 up) while the tones round it get the
   spread. A wider ramp than the source knee, so the result stays monotone
   along a ramp. `L_h` is the decision lightness smoothed along edges at
   half the glow's size (guided filter, radius 0.5 σ, ε 0.1²): texture
   inside a highlight can't modulate the hold, outlines stay put. *Revision
   1 read the hold per pixel at 0.75 and kept only 0.78 of a big
   highlight's texture (darker texture took more glow, flattening it); now
   0.94 is kept (high-pass, test pinned at > 0.93).*
4. **Combine without clipping.** Screen in linear light,
   `out = 1 − (1 − lin)(1 − g)`: never above 1, never darker, and the local
   slope `(1 − g)` ≤ 1, so grain is never stretched (it is gently compressed
   where the glow is strong).
5. Pixels where `g < 1e-6` are returned exactly.

k = amount/100 · 0.6.

## Pipeline

The very last colour stage, after Shadow Neutralise (light spreading over
the finished picture, as a diffusion filter would; neutralised shadows next to
a light take its warm spill), in `adjust_image` and on the OpenCL result in
numpy. `glow`, `glow_shape` appended LAST to `adjust_image` /
`adjust_image_opencl`; `CCRImage` passes them from each layer's settings.

## Measured

- Grain where it acts (16 px blocks lifted > 0.01, high-pass σ 1.5), 13
  real frames (5 Noritsu lab scans, 8 camera scans) at 100: median 0.75–0.89,
  worst block 0.999 — it never grows (the lift compresses it slightly, as a
  veil of light does).
- Spreading on the reduced copy vs a full-size Gaussian (2160 px): max
  difference 2e-3 (half an 8-bit code, at a light's edge), mean 3e-5.
- Speed (2-core container, slower than the user's Mac): 1080 px preview
  150–220 ms; 12 MP export 3.4 s (linearise 0.5, decision L 0.7, hold 0.6,
  encode 0.6, spread 0.2).

## UI / data model

Plumbed exactly like `shadow_shape` (load / attach on rebuild / reset / one
undo per popup / "sat" sync group / copy / its own paste row). The overlay
shares the Golden/Shadow layer (`ImagePreview.set_glow_mask`), drawn in amber;
its map (`glow.weight_map`) is the display lift at 100, × 4 (a lift of a
quarter of the range reads full). The popup's before/after strip is the
middle third of a picture three times wider (Size is a share of the long
side; a strip rendered as a whole picture would show the glow 3× smaller
than on a frame), drawn at 2× and scaled down.

## Tests (`tests/test_glow.py`)

0 is a byte-identical no-op; far from any highlight the picture is returned
exactly; a bright spot glows into its dark surroundings, falling off with
distance; the brightest area keeps its own detail (a texture inside a big
highlight changes little); output in 0..1, never darker than the input; a
ramp stays monotone; grain is never stretched; the glow field is smooth at
4320 px (no steps); the same picture at preview and export size glows alike
(the export downsampled matches the preview); Size widens it, Start moves
where it begins; options normalise; GPU == CPU; panel: slider in Lab Finish
under Shadow Neutralise, popup writes `glow_shape` with one undo, paste row,
copy, sync, the mask follows the popup.

## Revision 2 (2026-10-23): only light inside the crop glows

Renders are of the whole frame and the crop comes after, so light OUTSIDE a
crop (the white holder a negative scan turns into, sprocket holes, light
round the film) bloomed into the picture's edges. Measured on a dark picture
in a white surround at 100: +116 8-bit levels at the edge, +36 at 20 px in.

Now `CCRImage` passes the crop (`glow_area` = normalised box + angle,
appended LAST to `adjust_image` / `adjust_image_opencl`, and to
`FinishGPU.finish`). The sources are multiplied by the crop box's coverage
`m` (analytic, one-pixel soft edge, `glow.crop_mask`; the GPU's `glow_mask`
computes the same) and the glow is the NORMALISED spread
`spread(b·m) / max(spread(m), 1e-3)`: inside the crop every pixel sees the
average light of the part of its neighbourhood that is picture, so the edge
neither takes light from outside nor dims (a bright field running into the
edge glows the dark beside it as in the middle, within 15 %). Outside the
crop the values don't matter (cropped away). No crop, or a crop of the
whole unrotated frame (`normalize_glow_area` → None): the old path, byte
for byte. After: 0 levels of lift from the surround (< 2e-6).

