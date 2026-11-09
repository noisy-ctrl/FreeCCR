# Clean Lights

A slider at the top of Lab Finish that takes colour out of the light tones
at unchanged lightness, the way lab scans keep pale skin, walls, clouds and
whites cleaner while the colour stays strong lower down (the user,
2026-11-09: "It still does feel like something is missing", after the
comparison below).

## What the labs do (measured)

FreeCCR's default render of 33 Portra 400 pairs from three labs (two TIFF
labs, one Noritsu JPEG roll), each frame's six Channel Levels numbers fitted
to its lab scan, compared in OKLab: the lab's chroma as a share of ours, by
OKLab lightness L (median per lab):

| L | Lab A | Lab B | Noritsu | Mean |
|---|---|---|---|---|
| 0.40–0.75 | 0.97–1.02 | 0.92–0.98 | 0.93–1.01 | ~0.98 |
| 0.75–0.80 | 0.93 | 0.99 | 0.97 | 0.96 |
| 0.80–0.85 | 0.85 | 0.96 | 0.96 | 0.92 |
| 0.85–0.90 | 0.82 | 0.90 | 0.93 | 0.88 |
| 0.90–0.95 (unclipped samples) | 0.60 | 0.57 | 0.63 | 0.60 |

- Darks and mids match FreeCCR within a few per cent; the difference is the
  light tones, steepening towards white.
- In the light tones the share is the same for weak and strong colours
  (0.89 / 0.90 / 0.90 for chroma 0.015–0.04 / 0.04–0.08 / 0.08–0.2), so the
  move is a proportional chroma scale, not a chroma gate.
- Above L 0.95 the lab samples are clipped; the curve is extended there,
  not fitted.

The lab amount fitted to those points (RMS 0.035):
`r_lab(L) = 0.574 · smoothstep((L − 0.70) / 0.29)³`: 0 below L 0.70,
4 % at 0.83, 16 % at 0.88, 38 % at 0.93, 56 % near white.

## The tool

- Slider **Clean Lights**, 0..100, key `clean_lights`, "sat" sync group,
  first in the Lab Finish section. **50 = the labs' measured amount**,
  100 = twice it, as a power so it never overshoots:
  chroma × `(1 − r_lab(L))^(amount/50)` (at 100 the near-white share is
  0.19, never negative).
- No options in this version (a fixed, measured shape; Smoothing at the
  finishing tools' default 30). Options and Show where it acts can follow if
  wanted.
- Explanation in the label's tooltip.

## Processing (the finishing tools' quality rules)

- On the display values in OKLab, float32. **Decided** on
  `core.oklab.decision_lab` (guided filters at the render's own resolution,
  radius `DENOISE_R`), the matte `m = 1 − (1 − r_lab(L_s))^(amount/50)`
  finished along edges by `core.oklab.finish_matte` (Smoothing 30);
  **applied** per pixel: the pixel's own (a, b) × (1 − m) at its own L. So
  luma is untouched, hue is untouched, colour grain only ever falls.
- Out-of-range moves are shortened with `fit_move`; `m < 1e-5` returns the
  pixel exactly (so below L ~0.70 nothing changes, bit for bit), 0 = the
  input itself.
- Weight is C¹ in L (smoothstep cubed), lightness never changes, so tones
  can't reorder.
- Render order: **before** Golden Highlights (so the gold the user adds is
  not cleaned away), i.e. Clean Lights → Golden → Shadow Neutralise →
  Shadow Lift → Glow, in `adjust_image`, the OpenCL numpy fallback and
  `FinishGPU` (its own weight kernel, then Shadow Neutralise's apply kernel
  with Depth off: the same maths).
- `clean_lights` is appended LAST to `adjust_image` / `adjust_image_opencl`
  / `FinishGPU.finish` (positional callers).

## Tests

- 0 is the input; pixels below L 0.70 come back exactly; lightness and hue
  unchanged; chroma scaled by the measured share at 50 (± matte smoothing
  on a flat patch); 100 = the square of 50's share.
- A 16-bit-fine grey-to-colour ramp stays monotone in L and smooth; in
  range; a noisy light patch's colour grain doesn't grow.
- CPU == GPU within the finishing tools' tolerance.
- The slider zips with its key; the sat sync group carries it.
