# Golden Highlights (trial)

A slider, above Colour Organics, that gives **sunlit** highlights more of the
creamy gold of a good lab (Noritsu) scan without turning them yellow, and
leaves everything else alone: shadows, midtones, skin, sky, foliage and every
strongly coloured area.

## Revision 2 (2026-10-22): sunlit tones only

The first version added gold to every bright, low-colour tone, including
greys, pale skies and skin (a near-neutral of any hue took the full amount).
Feedback: "I want the skin tones and sky (and more) to remain untouched. I
just want sunny highlights to have more of that gold colour." So the colour
weight is now a **sunlit gate**: the tool acts only on bright tones that are
already a little warm and low in colour, which is what sunlight on a pale
surface looks like.

| tone (display RGB) | OKLab L / C / hue | weight |
|---|---|---|
| sunlit cream (0.93, 0.89, 0.80) | 0.92 / 0.032 / 87° | 0.31 |
| pale stone (0.85, 0.82, 0.74) | 0.86 / 0.029 / 90° | 0.50 |
| lit fabric (0.95, 0.92, 0.85) | 0.94 / 0.025 / 88° | 0.37 |
| pale skin (0.94, 0.84, 0.78) | 0.89 / 0.035 / 53° | 0.00 |
| light skin (0.86, 0.68, 0.58) | 0.78 / 0.064 / 51° | 0.00 |
| pale blue sky (0.75, 0.82, 0.90) | | 0.00 |
| plain grey (0.85, 0.85, 0.85) | | 0.09 (Neutral whites 10) |

Skin and sunlit cream are separated by **hue** (skin 45–62°, sunlit surfaces
85–90°) and by **chroma** (skin has more colour), not by lightness.

**Known limit:** a warm hazy sky near a sunset horizon has the same colour as
a sunlit surface (measured on a lab scan: L 0.76–0.89, C 0.01–0.036, hue
73–99°), so by colour alone it takes some gold. A spatial "is this smooth
sky?" test was tried and **rejected**: any texture/smoothness gate puts gold
halos round wires and branches against the sky, which is worse than the limit.
Where it matters, raise Start or lower the amount; "Show where it acts" shows
exactly what is affected.

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

So **gold is a hue of about 72° in OKLab** (between orange, ~55°, and yellow,
~110°; a push towards yellow is what reads "yellow, not golden"); it is
strongest through the bright tones and fades almost to neutral at the top,
which is what makes white read as cream.

## Goals / non-goals

- Golden Highlights 0..100 (default 0).
- Right-click the label: **Hue** (Amber ↔ Yellow, OKLab 50..95°, default 72°),
  **Start** (where it begins, L 0.30..0.85, default 0.55), **Clean whites**
  (how much the brightest tones fade back to neutral, 0..100, default 60),
  **Neutral whites** (how much plain greys take, 0..100, default 10), and
  **Show where it acts** (a cyan overlay on the picture while the popup is
  open).
- Not a substitute for the film-profiling / Film Look route: it is a trial
  of the one effect.

## Processing (`core/golden.py`)

On display values, sRGB-encoded, in OKLab (float32 throughout):

- tone weight `w = s((L − start)/0.20) · (1 − clean·s((L − 0.85)/0.15))`,
  `s` = smoothstep: nothing below Start, full through the bright tones,
  easing back at the top;
- sunlit gate `g = hue(h) · exp(−(C/0.035)²)`, `hue(h)` a raised cosine
  centred on 88° with half-width 30° (0.5 at ±15°, 0 beyond ±30°): warm,
  low-colour tones only;
- a grey has no reliable hue, so the colour weight blends from the Neutral
  whites share (C < 0.004) to the sunlit gate (C > 0.012) with a smoothstep,
  so near-greys can't speckle;
- add `amount/100 · 0.06 · w · col` of chroma along the gold hue to (a, b) at
  **unchanged L**, so highlights stay as bright as they were;
- back to RGB; a pixel that would leave 0..1 gets the largest fraction of its
  shift that stays inside (bisection): no clipped channels, no hue twist,
  never darker;
- pixels with zero weight are returned **exactly** (no OKLab round trip).

Every weight is a smooth function of colour, so gradients cannot band.

## Pipeline

The last colour stage: after the per-colour bands, before the final clamp,
in `adjust_image`, and on the OpenCL path on the kernel's result (numpy),
so both paths run the same function. `golden_highlights`, `golden_shape` are
appended LAST to `adjust_image` / `adjust_image_opencl`.

## Data model / UI

- `golden_highlights` in `ADJUSTMENT_KEYS` (created right before Colour
  Organics), Saturation sync group, paste row, catalog, area layers.
- `adjustment_settings["golden_shape"]` `{"hue", "start", "clean", "neutral"}`,
  None when default (`normalize_golden_shape`), plumbed like `organics_shape`.
  An old three-key shape normalises with `neutral` at its default.
- Popup (`widgets/golden_popup.py`): the four sliders, "Show where it acts",
  a swatch strip of a sunlit-cream ramp before / after, Reset.
- Overlay: `ImagePreview.set_gold_mask(shape | None)` draws `weight_map` of
  the displayed pixmap as a cyan child item of the picture (z 1.5, capped at
  1200 px, cached), never in the picture's pixels; the panel turns it off
  when the popup closes.

## Tests (`tests/test_golden.py`)

0 is a byte-identical no-op; shadows and midtones untouched; sunlit cream
gains chroma towards the gold hue with L unchanged (±0.002); skin (pale,
light, tanned), sky (pale, deep), foliage, red, yellow get under a tenth of
what cream gets; plain greys get the Neutral whites share; output stays in
0..1 with L kept; ramps stay monotone in L with smooth chroma (no banding);
Clean whites eases the top; Start moves the onset; `weight_map` matches;
GPU == CPU; panel placement, popup writes the shape (one undo), paste row,
copy, sync; the mask follows the popup and goes with it; the canvas layer is
clear in the shadows.
