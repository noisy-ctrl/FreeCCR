# Dust removal: a click fits itself to the speck

The user, 2026-11-19 (B&W XP2 frames, screenshots): "Sometimes dust removal
doesn't work, despite the dotted line showing the boundary. It often needs a
bigger brush to remove."

## Why (measured)

A synthetic grainy grey frame (6000 × 4500, grain σ ≈ 5 levels) with a
bright speck (core σ 3 px, +0.40; halo σ 8 px, +0.12), planned at the
1080 px preview and replayed at full size exactly as the app does. Speck
left at its centre (8-bit levels over the background):

| Brush radius (full-size px) | 6 | 9 | 12 | 18 | 30 |
|---|---|---|---|---|---|
| Left | 15.9 | 3.6 | 5.1 | 0.6 | −4.8 |

Feather 0 and forcing the defect rejection on changed nothing. The clone
source must touch the hole (the heal's rule), so for a brush covering only
the core it lies inside the halo: the source patch itself sat 10.5 levels
above the background for the 6 px brush. The halo outside the brush is not
healed at all. A bigger brush works because it swallows the halo.

## The change

- On release, a **dab** (every point within the brush radius of the first)
  is fitted: `core/dust_fit.fit_radius(gray, cx, cy, r)` grows its radius
  while rings around it still stand above the local background (median of
  an annulus past 3 r; a ring counts while its mean excess is above 12 % of
  the dab's and 2 standard errors of the ring, counting a ring of n px as n/4 independent
  samples because grain is correlated over a few px), plus 1 px; never smaller
  than the brush, never more than 3 × it; unchanged when nothing distinct is
  under the dab (< 3 standard errors, or < ½ a grain MAD). Bright or dark
  specks alike.
- It is measured on the picture on screen (`current_pixmap`, Rec.601 grey),
  the click and radius mapped into its pixels through the display crop, and
  the result written into the spot's `r` (normalised as before), so the
  dotted outline shows what is healed and preview, zoom and export all heal
  the same dab. Strokes (hairs, scratches) are left as painted.
- Dust panel: **Fit each click to the speck** (`dust/fit_spot`, default on).

Measured with the fit (same synthetic speck, the fit read at 100 % / 50 % /
the 1080 preview): brushes of 6, 9 and 12 px grew to 14–22 px and left
0.1–5 levels (the spread is which clean patch was copied); a 4 px brush
reached the 3 × cap (12 px) and left 5. On clean grain a 9 px dab stays 9.

## Tests

- `fit_radius`: grows over a synthetic speck's halo; stays put on clean
  grain; capped at 3 ×; never shrinks; dark specks too.
- The preview: a released dab over a speck in the displayed pixmap gets a
  larger `r`; with the setting off, or for a stroke, `r` is the brush's.
