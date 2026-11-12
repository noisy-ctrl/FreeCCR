# Auto Radius (Details → sharpening)

The user's request, 2026-11-12: the best sharpening radius varies between
films, formats and scanning setups; an **Auto** button beside Radius should
measure it.

## Goals
- One click sets **Radius** from the frame itself; Amount and Masking stay
  the user's.
- Measured at FULL resolution (Radius is in full-resolution pixels,
  spec/sharpen-like-output.md; grain is sub-pixel in the 1080 preview).
- Never blocks the window: decode + measure run on a worker thread.
- Says what it measured, so the number is explainable.

## Non-goals (for now)
- Remembering a radius per film stock (a follow-up once measurements prove
  consistent on real scans).
- Setting Amount, or deconvolution.

## What is measured (`core/auto_radius.py`, numpy + OpenCV)
On the frame's RAW scan decoded at full resolution (`read_image(preview=False)`
on a shallow copy, as `contact_sheet.decode_negative` does; linear light, so
blur is measured where the optics made it), luma = mean of R, G, B, inside the
crop (or the middle 80 %), at most a centred 3000 × 3000 px region.

1. **Edge blur σe.** Gradient of the luma smoothed with σ 1 px; candidate
   pixels are the strongest 0.3 % that are the local maximum in a 5 × 5
   window. For each, the profile is sampled along the gradient direction
   (−8 … +8 px, step 0.5, bilinear). Accepted when both plateaus (|t| 5–8 px)
   are flat (std ≤ 12 % of the step), the step is ≥ 8 × the grain's std and
   the profile rises monotonically between them (within 5 % of the step).
   The 10–90 % rise distance d gives σ = d / 2.563 (a Gaussian-blurred step),
   minus the 1 px smoothing in quadrature. **σe = the median of the sharp
   group**, the edges within 1.5 × the 20th percentile: the sharpest edges
   are the scan's own blur and out-of-focus parts of the picture are wider and
   must not pull it up, while single edges spread about the true blur, so a
   low percentile alone reads ~15 % low (measured). Needs ≥ 25 edges.
2. **Grain size σg.** High-pass `L − Gauss(L, 6)`; 64 × 64 tiles; the 15 %
   flattest (least low-pass gradient energy, mean away from clipping, ≥ 12
   tiles) are kept. Each tile's normalised autocorrelation (FFT, Hann window)
   is averaged radially; its half-height distance r½ gives σg = r½ / 1.665
   (Gaussian-correlated noise), the median over tiles, corrected for what the
   high-pass and window take off (`1.21 × measured + 0.07`, fitted on grain of
   σ 0.5–2 px; it read ~20 % low). Below 0.45 px the noise is (sub-)pixel: no
   grain limit.

## The radius
The unsharp mask uses σ = radius / 2 (`apply_sharpening`), so the edge-matched
radius is `2·σe`. Grain limit: beyond a mask σ of 1.5 σg the high-pass carries
whole grain clumps, so `radius ≤ 3·σg`. Radius = the smaller, clamped to
0.5–2.5 px (slider 12–62; the cap guards blurred or out-of-focus frames), in
the slider's units (× `SHARPEN_RADIUS_DIV`, 25 = 1 px), rescaled if the decode
is not the frame's `original_full_size`.

Result: `RadiusResult(radius_px, slider, sigma_edge, sigma_grain, edges,
limited_by, reason)`; `None` slider with a reason when there are too few
edges.

## UI / integration
- An **Auto** button at the end of the Radius row (Details). Click → busy
  cursor, button disabled, `AutoRadiusWorker` (`WorkerThread`) decodes and
  measures; on the GUI thread the Radius slider is set like a user move (one
  undo step: `end_undo_burst`, set, `on_slider_changed`) and a hint says e.g.
  "Auto Radius 41 (1.6 px): edges 0.82 px, grain 0.65 px." A failure says why
  and changes nothing. A frame switch before it finishes drops the result.
- Seam for tests: `SlidersPanel._auto_radius_threaded` (False = run inline),
  `SlidersPanel._measure_radius(img)`.

## Tests (`tests/test_auto_radius.py`)
Synthetic scenes (random steps blurred with a known σe, plus Gaussian-correlated
grain of known σg): σe recovered within 15 %, σg within 25 %; the grain limit
applies when grain is coarse; white noise gives no grain limit; a flat frame
reports too few edges; the slider value rounds radius × 25; an out-of-focus
scene is capped at 2.5 px. Panel: Auto sets Radius with one undo step and
leaves Amount alone.


## Revision 2026-11-12 b: sized to the grain

The user: "auto radius should be optimum sizes to sharpen grain and not other
edges … I want sharpening to resemble lab scans". The first version matched
the radius to the edge blur and only used the grain as a ceiling, the
opposite. Now:

- **Radius = 2 · σg** (`RADIUS_PER_GRAIN`): the unsharp mask's σ (radius / 2)
  equals the grain's own σ, so the high-pass lifts detail at the grain's
  scale (crisp grain) and adds little halo to larger edges. Edges are no
  longer measured; the edge section above no longer applies.
- Grain finer than 0.45 px (not resolved at this scan resolution): the
  smallest useful radius, 0.5 px (slider 12), said so.
- Clamped 0.5–4 px (the slider's own range; the old 2.5 px cap guarded soft
  edges, which no longer decide it); rescaled to full-resolution pixels.
- No smooth area to measure in (fewer than 12 usable tiles): nothing changes.
- `RadiusResult(radius_px, slider, sigma_grain, tiles, limited_by, reason)`,
  limited_by "grain" | "fine" | "cap" | "floor".
- The tooltip says Masking limits sharpening to edges, so keep it low for the
  grain look.

Tests: grain size recovered within 25 % (σ 0.6–1.6 px); radius = 2 σg within
25 % of the truth; the same grain under very different edge blur gives the
same radius (± 3); sub-pixel grain → 0.5 px; very coarse grain → cap; no
smooth area → unchanged; rescaling.

Follow-up (not built): fit Amount and Radius to the user's own lab scans of
the same negatives (the grain's spectrum in the lab scan against FreeCCR's),
so the sharpening matches a lab's, not just the grain's size.
