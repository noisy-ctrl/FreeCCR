# Golden Highlights (trial)

A slider, above Colour Organics, that gives sunlit highlights the creamy gold
of a good lab (Noritsu) scan without turning them yellow, keeping shadows
neutral and leaving strong colours (sky, foliage) alone.

## Why a tool of its own

- **Not Colour Organics.** Organics turns the hue of colours that are already
  there. A near-white highlight has almost no colour to turn; gold has to be
  *added* to it.
- **Not White Balance.** A flat warm shift moves every tone (warm shadows)
  and every colour (green-grey skies), and it keeps adding colour right up to
  white, so whites go yellow.

## What a lab scan does (measured)

Near-neutral tones (OKLab chroma < 0.08) of the reference Noritsu frame,
by OKLab lightness L:

| L | 0.40–0.55 | 0.55–0.70 | 0.70–0.80 | 0.80–0.88 | 0.88–0.94 | 0.94–1 |
|---|---|---|---|---|---|---|
| chroma | 0.046 | 0.040 | 0.032 | 0.033 | 0.020 | 0.008 |
| hue | 64° | 70° | 75° | 73° | 72° | 87° |

So: **gold is a hue of about 72° in OKLab** (between orange, ~55°, and yellow,
~110° — a push towards yellow is what reads "yellow, not golden"); it is
**strongest through the upper midtones and bright tones and fades almost to
neutral at the top**, which is what makes white read as cream; the lab
scans of mixed scenes show the same, hue 68–85°, with very bright skies left
near-neutral.

## Goals / non-goals

- Golden Highlights 0..100 (default 0); about 60 matches the reference.
- Right-click the label: **Hue** (Amber ↔ Yellow, OKLab 50..95°, default 72°),
  **Start** (where it begins, L 0.20..0.70, default 0.40), **Clean whites**
  (how much the brightest tones fade back to neutral, 0..100, default 60).
- Not a substitute for the film-profiling / Film Look route: it is a trial
  of the one effect.

## Processing (`core/golden.py`)

On display values, sRGB-encoded, in OKLab:

- tone weight `w = s((L − start)/0.25) · (1 − clean·s((L − 0.85)/0.15))`,
  `s` = smoothstep: nothing below the start (shadows stay as they are),
  full through the bright tones, easing back at the top;
- colour weight `c = 1 / (1 + (C/C0)²)` with
  `C0 = 0.015 + 0.085·((1 + cos(h − hue))/2)^1.5`: near-neutrals take the full
  amount whatever their hue; colours already near gold accept more (C0 up to
  0.10), cool colours very little (0.015) — a pale blue sky stays blue;
- add `amount/100 · 0.035 · w · c` of chroma along the gold hue to (a, b) at
  **unchanged L**, so highlights stay as bright as they were ("bright gold",
  not a darker yellow);
- back to RGB; a pixel that would leave 0..1 gets the largest fraction of its
  shift that stays inside (8-step bisection) — no clipped channels, no hue
  twist.

Every weight is a smooth function of colour, so gradients cannot band.

## Pipeline

The last colour stage: after the per-colour bands, before the final clamp,
in `adjust_image`, and on the OpenCL path on the kernel's result (numpy),
so both paths run the same function. It finishes the look rather than being
pushed around by the saturation sliders after it. `golden_highlights`,
`golden_shape` are appended LAST to `adjust_image` / `adjust_image_opencl`.

## Data model / UI

- `golden_highlights` in `ADJUSTMENT_KEYS` (created right before Colour
  Organics), Saturation sync group, paste row, catalog, area layers.
- `adjustment_settings["golden_shape"]` `{"hue", "start", "clean"}`, None when
  default (`normalize_golden_shape`), plumbed like `organics_shape`.
- Popup (`widgets/golden_popup.py`): the three sliders, a swatch strip of a
  grey ramp before / after at the current amount, Reset.

## Tests (`tests/test_golden.py`)

0 is a byte-identical no-op; tones below the start are untouched; near-white
greys gain chroma at the gold hue with L unchanged (±0.002); a pale sky blue
gains under a fifth of what a grey does; output stays in 0..1; a grey ramp
stays monotone in L and its chroma changes smoothly; Clean whites 100 keeps
white neutral; GPU == CPU; panel slider placement, popup writes the shape (one
undo), paste row, copy, sync with the Saturation group.
