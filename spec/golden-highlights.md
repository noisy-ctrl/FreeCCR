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

## Revision 3 (2026-10-22): grain must not steer it

Checked against the image-quality rules (CLAUDE.md, "Finishing tools must
keep the picture's quality"): revision 2 decided the gold per pixel, so a
grain speck that happened to be warmer got more gold — colour grain where it
acts rose **1.5–1.7× at 60 and ~2–2.4× at 100** on the lab scans and the
user's conversions. Now:

- how much gold each pixel gets is **decided on the decision view**
  (`core.oklab.decision_lab`, shared with Shadow Neutralise: the render at the
  preview's resolution, OKLab, bilateral) with a wider spatial σ (3 px at
  1080; the gold gate is steep in colour), then scaled back to the render's
  size and **added to each pixel's own colour** — the gold is a smooth field,
  so the grain comes through as it was;
- the grey → sunlit hand-off is widened from C 0.004–0.012 to **0–0.03**: a
  hue read off a near-grey is mostly grain, and the steep hand-off let grain
  decide even on the smoothed view (2–4× on a heavy-grain near-white patch);
- the gamut fit is tested in **linear light** (`fit_move`): the display
  encoding floors negatives at 0, so a channel pushed below black used to be
  clipped silently instead of the move being shortened.

Measured: on flat areas of the five lab scans and two conversions, colour
grain where it acts is now 1.00–1.01× at 60 (a high-pass measure over the
whole acting area reads up to 1.07×: edges, where a gilded surface meets an
ungilded one); a near-white patch with heavy synthetic grain stays ≤ 1.10× at
100. Selection is unchanged (cream 0.31, pale stone 0.50, skin 0, pale sky 0,
plain grey 0.09); a warm near-white takes a little less (0.125 → 0.087). Gold
on pale skin 1 px from a cream edge is ~1 % of the cream's.

Cost: ~0.2–0.5 s per 1080 preview render in the 2-thread test container
(the bilateral and the gamut fit near white dominate).

## Revision 4 (2026-10-23): a smooth matte, gradations first

Feedback: "preserving tonal gradations is key", and the same concern about a
blocky look as for Shadow Neutralise. Shared with it (see
`spec/shadow-neutralise.md` revision 4):

- the decision view is made with **guided filters at the render's own
  resolution** (`core.oklab.decision_lab`, radius 4 px at preview size for
  the gold, scaled with the render) instead of a bilateral at 1080 px whose
  map was scaled up — so no blocks or stair-steps at export size;
- a **Smoothing** option (0..100, default 30, "Crisp" ↔ "Soft") smooths the
  gold matte along the picture's edges (`core.oklab.finish_matte`, guided
  filter on the decision view's lightness);
- the gold is still ADDED to each pixel's own colour at unchanged lightness,
  so every tonal gradation passes through: on a 4320-px sunlit ramp the gold
  added changes by < 1e-4 between neighbours with no kinks (second
  difference < 2e-6), lightness within 0.002; along a slanted cream/sky edge
  at 4320 px it varies by < 0.0002 at a fixed distance from the edge, and the
  sky gets none.

Measured on real frames at 60: colour grain where it acts 1.00× on flat
areas, ≤ 1.03× over the whole acting area (edges; was 1.07×).

The user also wondered about the overall approach (adding gold in OKLab to a
sunlit-tone selection); that is parked for a later conversation.

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
  whites share (C 0) to the sunlit gate (C 0.03) with a smoothstep (revision
  3; it was 0.004–0.012);
- add `amount/100 · 0.06 · w · col` of chroma along the gold hue to (a, b) at
  **unchanged L**, so highlights stay as bright as they were;
- the weights above are read on the decision view (revision 3), the chroma is
  added to each pixel's own colour;
- back to RGB; a pixel that would leave 0..1 gets the largest fraction of its
  shift that stays inside (12-step bisection in linear light): no clipped
  channels, no hue twist, never darker;
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

0 is a byte-identical no-op; shadows and midtones untouched; colour grain on
a noisy sunlit / near-white patch at preview size doesn't grow (≤ 1.10× at
100); sunlit cream
gains chroma towards the gold hue with L unchanged (±0.002); skin (pale,
light, tanned), sky (pale, deep), foliage, red, yellow get under a tenth of
what cream gets; plain greys get the Neutral whites share; output stays in
0..1 with L kept; ramps stay monotone in L with smooth chroma (no banding);
Clean whites eases the top; Start moves the onset; `weight_map` matches;
GPU == CPU; panel placement, popup writes the shape (one undo), paste row,
copy, sync; the mask follows the popup and goes with it; the canvas layer is
clear in the shadows.
