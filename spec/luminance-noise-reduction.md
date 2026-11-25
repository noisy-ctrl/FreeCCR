# Noise Reduction: luminance as well as colour

The user, 2026-11-25: "Could we add a normal noise reduction too, renaming
Chroma noise reduction to Noise reduction".

## Goals

- A luminance noise reduction beside the existing chroma one, in one
  section renamed **Noise Reduction**.
- Built for film scans: it calms the fine, random speckle (the camera's own
  noise in a dense negative's shadows, the finest grain) while keeping
  edges, texture and the grain's character. It is not a smoothing filter.
- Held to the finishing tools' quality rules (CLAUDE.md): an exact no-op at
  0, float inside, smooth weights, no reordering along a gradient, no
  clipping, resolution independent, all pinned in tests with measured
  numbers.

## Non-goals

- Removing grain altogether (a "plastic" look): Detail keeps a floor.
- A GPU twin (it runs where the chroma NR runs, in numpy / OpenCV).
- Changing the chroma NR's maths: its sliders, keys and results stay
  byte for byte.

## UX

The section **Noise Reduction** (was "Chroma Noise Reduction"; same place,
same fold, same hide id `noise`) holds two groups, each under a small muted
caption:

```
Luminance
  Amount   0 ─────────── 100     (default 0 = off)
  Detail   0 ─────●───── 100     (default 50)
Colour
  Amount   0 ─────────── 100     (as before)
  Radius   0 ─────●───── 100     (as before)
[ ] Bypass until export          (now for both)
Calms speckle and colour blotches; edges and texture are kept.
Zoom in to 100% to judge it.
```

- **Luminance Amount**: how much of the measured noise is taken out.
- **Luminance Detail**: how much of the finest texture is always kept, so
  the grain stays; 0 = the most smoothing, 100 = a light touch.
- The bypass box (`chroma_nr_export_only`, key kept) now bypasses both
  until export; its label reads "Noise reduction".
- Sync group **Noise Reduction** (was "Chroma Noise Reduction") carries
  all four; the paste row likewise ("Noise Reduction (luminance 40, detail
  50; colour 30, radius 50)"). Area layers get the luminance amount too,
  like the colour one.

## Data model

`luma_nr` (0..100, default 0) and `luma_nr_detail` (0..100, default 50),
appended LAST to `ADJUSTMENT_KEYS` (the sliders are created last and
inserted at the top of the section, as Lab Tone was). Old catalogues have
neither and render unchanged.

## Method (`core/luma_nr.py`)

1. Split as the chroma NR does: Y = Rec.601 luma of the uint16 render in
   float32 (0..1); B−Y and R−Y are left alone. The result goes back as an
   EQUAL change ΔY to R, G and B, so colour differences are untouched and
   the B&W grey (Rec.601) changes by exactly ΔY.
2. Undecimated wavelet (à trous, B3 spline [1 4 6 4 1]/16, separable,
   `BORDER_REFLECT`) of Y into four detail bands d1..d4 (1, 2, 4, 8 buffer
   pixels) and a residual.
3. Noise per band and tone (`noise_profile`): each band's RESIDUAL (the
   coefficient minus the mean of its neighbours at the band's spacing, so
   smooth structure, which is coherent, all but vanishes: 1e-8 against
   1e-5), in 8 tone bins of a twice-smoothed luma (no single grain steers
   the bin), over the QUIETER half of the residual's local energy (so
   texture and edges don't count), MAD / 0.6745 / `RESID_BIAS`
   (0.68, 0.66, 0.66, 0.66: measured on noise alone, 0.65–0.74 for white
   and grain-like noise; very coarse grain reads low at band 1, so less is
   taken out there, never more). Smoothed across bins, interpolated per
   pixel. Read on every other row and column; on a picture over 4 MP, on
   6 × 6 tiles of 256 px spread over the frame. A global MAD was tried
   first and counted texture and slow curves as noise (a clean picture
   moved by 46 codes; now ≤ 1).
4. Shrink each band by a local Wiener gain on its smoothed energy:
   `E = G_j(d_j²)` (Gaussian at the band's own scale),
   `g = E / (E + (k σ_j w_j)²)`, `k = 3.0 · Amount/100`, then the Detail
   floor `g' = f + (1 − f) g`, `f = 0.6 · Detail/100`. `w_j` is the band's
   weight by its size ON THE FULL-RESOLUTION SCAN (`band_weight`): 1 up to
   4 px (film grain on his 50 MP scans sits at 2–8 px: its measured σ is
   largest in bands 2–3), 0.75 at 8 px, 0.5 at 16, 0 from 64. A preview at
   a fifth of the scan meets its finest band as a 5 px one, so it is
   treated as the output is at that viewing size; no preview-only scale.
   g is C¹ in d (no threshold: no speckle or banding).
5. ΔY = Σ (g'_j − 1) d_j. Shortened per pixel so no channel leaves 0..1
   (an equal add: ΔY ≥ −min(R,G,B), ≤ 1 − max(R,G,B)); |ΔY| < 1e-7 returns
   the pixel exactly. Big pictures run in strips of 1024 rows with 64 rows
   of context (identical to one piece within 1 code).

## Measured

Synthetic (a step, strong texture, grain-like noise σ 0.012 blurred 0.7 px):
noise in flat areas ×0.56 at 50, ×0.40 at 100 (Detail 50); the texture's
amplitude kept 0.90 / 0.80; the step's 10–90 % width + < 0.5 px; colour
differences unchanged within 2.5/65535; a clean picture ≤ 1 code.

His beach frame (060940 #10, the RGB triplet DSCF0050_1 / 0051_1 / 0052,
decoded at full resolution 8280 × 6208, default render with Auto tone),
the high-pass (finer than 8 px) of the luma, Detail 50:

| | Amount 50 | Amount 100 |
|---|---|---|
| Sky (flat) | ×0.70 | ×0.53 |
| Sand (texture) | ×0.97 | ×0.91 |

(The first weights, 1 / 0.8 / 0.6 / 0.4 by size with k = 2.5, gave only
×0.81 / ×0.62 on the sky: his grain sits in bands 2–3, which they treated
lightly.)

The measured σ (×1000) by band and tone bin, dark → light:
band 1 0.8–3.3, band 2 1.1–4.4, band 3 1.1–4.1, band 4 0.9–2.7 (grain
peaks in the mid tones and at 2–4 px).

Speed here (2 cores): 0.2 s at 1080 × 720, 0.4 s at 1620 × 1080.
## Pipeline position

In `CCRImage.apply_adjustments`, right BEFORE the chroma NR at both of its
sites (luminance first, then colour), with the same live / export-only
gate, the same `skip_dust` skip and the same area handling. Unlike the
chroma NR it also runs on a B&W profile (ΔY is the grey's change).

## Tests (`tests/test_luma_nr.py`)

- Amount 0 returns the input object untouched; Amount > 0 with no noise
  (a flat or smooth picture) changes nothing by more than 1 16-bit code.
- Synthetic: a sharp-edged step + texture + grain-like noise: noise in
  flat areas < ×0.65 at 50 and < ×0.45 at 100, texture kept > 0.88 / 0.75,
  the step's 10–90 % width grows by < 0.5 px.
- Detail 100 keeps ≥ 60 % of band 1; Detail 0 keeps less than Detail 50.
- A 16-bit ramp with noise stays monotone after smoothing its own
  noise-free version through it (no reordering), and nothing leaves range.
- Colour differences (B−Y, R−Y) unchanged to float precision.
- Band weights by full-resolution size; a preview (scale 0.2) changes
  less than the full-resolution picture.
- The noise is measured by tone (noisier shadows read > 2.5× the lights).
- Big pictures in strips equal one piece within 1 code.
- Speed reported at 1080 px and at 50 MP.
- Panel: the section is "Noise Reduction", the four sliders in order, the
  bypass label, the sync group name, the paste row text.

## Draft

`noise-reduction-draft.jpg`: three windows of the beach frame at 200 %
(sky, skin, sand), Off / 50 / 100 / 100 with Detail 0, colour noise
reduction off so only the luminance change shows.
