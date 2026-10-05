# Chroma Noise Reduction

## Goal

Reduce colour noise (red/green/blue blotches in shadows and flat areas,
common in film scans after saturation boosts) without softening the image.
Luminance (and so sharpness and grain structure) is never touched.

## UX

New collapsible section **Chroma Noise Reduction** at the bottom of the
right-hand panel, after Subtractive Saturations and a separator:

- **Amount** 0–100, default 0 (off, zero cost).
- **Radius** 0–100, default 50: smoothing size, relative to the image.
- Muted hint: judge it zoomed in to 100%.

Double-click resets each slider to its own default (this also fixes
Feather, which used to reset to 0 instead of 10). Syncs as its own
**Chroma Noise Reduction** group; pastes as one tick (amount + radius,
offered only when Amount ≠ 0). Works on area layers as extra NR inside the
mask.

## Method

1. RGB → Y, B−Y, R−Y (Rec.601 luma, as Saturation uses).
2. Fast guided filter (He & Sun 2015) on B−Y and R−Y with Y as the guide,
   coefficients solved on a grid subsampled by ≥2 and upsampled, so cost is
   ~linear in pixels at any radius. Colour stays put on brightness edges.
3. **Edge protection** for colour edges of similar lightness (invisible to
   the luma guide): the per-image noise level is estimated as a robust MAD of
   the proposed change on a sparse grid; changes beyond
   `CHROMA_NR_EDGE_K` (3) × that level are faded out with a Gaussian, so real
   colour edges are kept while noise-sized changes go through. A clean image
   is therefore left essentially untouched.
4. Blend by Amount, recombine with the original Y, clip.

Radius in pixels = `long_side × lerp(0.0005, 0.006, radius/100)`, so the
preview, the zoom hi-res render and a full-size export apply the same
physical smoothing. Below 1 px it is skipped.

## Pipeline position

`CCRImage.apply_adjustments`: after the slider pass, gamma and Curves (so it
cleans what Saturation/Vibrance/bands amplified), **before** area layers (so
they grade the cleaned base) and the Black & White collapse. Skipped in B&W
(it never changes luma) and with `skip_dust` (the neutral solves render
detached sample patches, where a spatial filter is meaningless).
`_adjust_for_area` applies an area's own amount on its render.

All render paths (preview, thumbnails, zoom hi-res, export) process whole
images, never tiles, so there are no seams.

## Performance

Measured on one 2.1 GHz core: about +40–45% over a typical slider pass
(≈65 ms at preview size, ≈2 s at 24 MP). Built from multithreaded OpenCV
calls, so multi-core machines are proportionally faster. Zero cost at
Amount 0.

## Tests

`tests/test_chroma_noise_reduction.py`.
