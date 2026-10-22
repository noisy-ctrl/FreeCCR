# Shadow Neutralise

A slider, directly under Golden Highlights, that takes the colour cast out of
the shadows so they read as a neutral, inky black, the way a good lab
(Noritsu) scan renders them, while strongly coloured dark things (a red car,
deep foliage, a navy coat) keep their colour. Right-click for options.

Revision 2 (refined before implementing): gate shape, defaults and the
overlay settled from real conversions (below); open questions resolved.
Revision 3 (during implementation): the per-pixel gate stretched colour
grain by up to 1.2–1.3× in places on lab scans, so how much the tool acts
is now DECIDED on a denoised, edge-preserving view of the picture (the
"decision map", shared with Golden Highlights) and only APPLIED per pixel.

## Why a tool of its own

- **Not White Balance / Temperature-Tint.** Those move every tone. A
  conversion's shadows often carry a cast the mid-tones and highlights don't
  (crossover, a base that isn't quite neutral, scan flare), so balancing the
  shadows unbalances the rest.
- **Not Channel Balance.** That is the right tool to correct the cast at its
  source (tone-weighted, before the decode), and it stays the first choice for
  a big crossover. But it can't tell a cast from a coloured object: it shifts
  everything at that tone. Shadow Neutralise only takes out *low* colour, so
  dark colours survive.
- **Not Saturation / Vibrance.** They act on every tone, and they scale a cast
  and a dark colour alike.

## Measured on real conversions

Eight of the user's scans converted the way Convert All does them (auto black
point), OKLab on the rendered display values:

| frame | shadow band | median chroma | mean a, b |
|---|---|---|---|
| cathedral interior (1070) | L 0.15–0.25 (31 % of frame) | 0.032 | −0.028, −0.008 (teal) |
| street (1104) | L 0.15–0.35 | 0.015–0.020 | −0.010, −0.012 (blue) |
| market (1107) | L 0–0.25 | 0.013–0.014 | −0.005, +0.006 |

So a shadow cast is **chroma ~0.01–0.035** in OKLab. Dark colours have less
OKLab chroma than they look: deep foliage (0.08, 0.16, 0.04) is 0.058, a
dark plum 0.078, navy 0.091, a dark red 0.097, skin in shade (0.30, 0.20,
0.15) 0.043.

## Goals / non-goals

- **Shadow Neutralise** 0..100 (default 0), key `shadow_neutralise`, created
  right after Golden Highlights (so right before Colour Organics); Saturation
  sync group.
- Right-click the label: **Range** (how far up the tones it reaches, OKLab
  L 0.20..0.70, default 0.50, "Deep shadows" ↔ "Into midtones"), **Reach**
  (how much colour still counts as a cast, chroma 0.010..0.100, default 0.045,
  "Casts only" ↔ "Colours too"), **Depth** (deepen the darkest tones a little,
  0..100, default 0, "None" ↔ "Inky"), **Show where it acts** (a magenta
  overlay while the popup is open), Reset, and a before/after strip of a
  cast shadow ramp.
- Non-goals: correcting a cast that runs through every tone (that is White
  Balance / Channel Balance); local or spatial masking (see Rejected).

## Processing (`core/shadow_neutral.py`)

On display values (sRGB-encoded, 0..1) in OKLab, float32 throughout (shared
helpers in `core/oklab.py`, also used by Golden Highlights).

**Decide** (the strength map, `strength_map`): on the *decision view* of the
picture, `core.oklab.decision_lab` — the render box-downsampled to the
preview's resolution (long side 1080) if it is bigger, in OKLab, then a
bilateral filter (σ space 1.5 px at 1080, σ colour 0.05 OKLab): grain is
averaged out, real edges (an OKLab step well over 0.05) stay sharp. There:

- **tone weight** `t(L) = 1 − s((L − 0.4·R)/(0.6·R))`, `s` = smoothstep, R =
  Range: fully on below 40 % of R, fading smoothly to 0 at R. At the default
  that is full below L 0.20 (sRGB ~0.085), off from L 0.50 (sRGB ~0.39).
- **colour gate** `g(C) = exp(−(C/c0)²)`, c0 = Reach. At the default (0.045):
  the cathedral's cast 0.032 → g 0.60; a 0.020 cast → 0.82; skin in shade
  (0.30, 0.20, 0.15), C 0.043 → 0.40; deep foliage (0.08, 0.16, 0.04), C
  0.058 → 0.19; dark red (0.30, 0.04, 0.04), C 0.097 → 0.01; navy 0.091 →
  0.02. (Dark colours have little OKLab chroma; a cast in deep shadow is
  ~0.01–0.035.)
- `m = k·t·g` (k = amount/100), bilinearly scaled back to the render's size
  (an area that is exactly 0 stays exactly 0).

**Apply** (per pixel, its own colour):

- **neutralise**: the pixel's own (a, b) scaled by `1 − m` at **unchanged L**.
  Hue is kept (a pure radial move: no atan2, no hue noise), lightness and
  luma grain are exactly the input's.
- **Depth**: a per-pixel tone curve on the pixel's own L,
  `L' = L·(1 − k·0.25·depth/100·t(L))`, with (a, b) scaled by the same factor
  (saturation C/L unchanged by the deepening). Slope at black ≥ 0.75, black
  stays black, nothing above Range moves. (It lifts local contrast a little
  where it fades out, up to ~1.3× at Depth 100: a toe.)
- a pixel the move would take out of 0..1 gets the largest fraction that
  fits (bisection, 12 steps, i.e. under one 16-bit code); pixels with
  strength under 1e-5 (and no Depth) are returned **exactly**.

### Image-quality properties

- **Grain is never stretched.** m is a smooth field ≤ 1 decided off the
  denoised view, so each pixel's colour noise is only ever scaled down and its
  luma noise is untouched. On the user's eight conversions, colour grain where
  the tool acts falls to 0.42–0.89× at 100 (L grain exactly 1.00×); on the
  lab JPEGs a high-pass "grain" measure rises by up to 1.14× at 100 — edges
  (a coloured object beside a neutralised shadow gains a little colour
  contrast), not noise: the per-pixel gate tried first showed up to 1.33×
  there, and on a synthetic noisy patch at the worst-case chroma the colour
  noise now only falls.
- **Monotone along gradients:** for any non-increasing g and k·t ≤ 1,
  `d/dC [C(1 − k·t·g)] = 1 − k·t·g − k·t·C·g' ≥ 1 − k ≥ 0`, so chroma never
  reverses along a smooth gradient (which the decision view follows), and L is
  untouched (or, with Depth, monotone with slope ≥ 0.75). No banding.
- **Bounded stretch of real gradients:** the Gaussian hands colour back with a
  peak slope of 1.45× (at C ≈ 1.2·c0, full amount); a plateau gate tried first
  peaked at 2.3×.
- **Smooth:** every weight is C¹; no thresholds, so no speckle or
  posterisation; the bilateral keeps the decision from bleeding across edges
  (close-ups of wires against sky, foliage against sky and an arm against a
  dark shirt show no halo — only the antialiased edge pixels themselves, which
  are dark and coloured, lose a little colour).
- **Resolution independent:** preview, zoom, slices, area layers and export
  decide at the same resolution; the per-pixel apply is the same function.
- **Cost:** ~200 ms on a 1080 preview in this container (the decision's OKLab
  is reused for the apply at preview size), ~6 s for a 24 MP export.

## Pipeline

The very last colour stage, right after Golden Highlights (UI order = render
order), in `adjust_image` and, on OpenCL, in numpy on the kernel's result
(after Golden), so CPU and GPU run one function. `shadow_neutralise`,
`shadow_shape` are appended LAST to `adjust_image` / `adjust_image_opencl`
(positional callers); `CCRImage` passes them from each layer's settings (both
call sites), so area layers work like any slider.

## Data model / UI

- `shadow_neutralise` in `ADJUSTMENT_KEYS` right after `golden_highlights`;
  label "Shadow Neutralise"; "sat" sync group.
- `adjustment_settings["shadow_shape"]` `{"range", "reach", "depth"}`, None
  when default (`normalize_shadow_shape`), plumbed exactly like
  `golden_shape`: load/attach on rebuild, reset, one undo step per popup,
  Saturation sync group, copy, its own paste row ("Shadow Neutralise
  options"), catalog (it is part of the settings dict).
- Label tooltip explains the tool; underlined when options are custom.
- Popup `widgets/shadow_popup.py` (`ShadowPopup`): the three sliders with end
  labels and tooltips, "Show where it acts", the cast-ramp strip (a teal-cast
  shadow ramp before / after at the current amount, or at 60 when the slider
  is 0), Reset ("back to the defaults").
- Overlay: `ImagePreview.set_shadow_mask(shape | None)`, sharing the Golden
  layer (`_gm_item`, z 1.5) — only one popup can be open at a time; a `None`
  from the tool whose map is not showing is ignored, so closing one popup
  can't hide the other's map. Magenta (a cyan wash read as the teal cast or
  blue sky it sat beside), alpha = 0.85 × weight (the gold map's 1.6 gain
  over-states partial weights here; the neutralising weight is often 1 in the
  deep shadows).

## Rejected

- **Estimating the frame's shadow cast and subtracting it** (a shadow-only
  white balance). It keeps dark colours' relative differences, but the
  estimate depends on what is in the frame — the rebate and holder are the
  darkest, most cast-coloured areas of an uncropped conversion — and it would
  move with every slider during a live drag. A local rule is stable and
  identical at every resolution.
- **Deciding per pixel** (revision 2): simplest, but a gate that reads each
  pixel's noisy colour turns colour grain into more grain where it hands
  colour back (up to 1.33× measured).
- **A plain Gaussian blur for the decision**: no grain stretch either, but it
  bleeds across edges — the same halo problem that sank Golden Highlights'
  smooth-sky gate. The bilateral keeps edges.
- **A plateau gate** (fully neutral up to half of Reach): stronger cast
  removal at the same Reach, but a 2.3× chroma stretch where it hands back.

## Tests (`tests/test_shadow_neutralise.py`)

0 is a byte-identical no-op; tones above Range are returned exactly; a teal
cast in the deep shadows loses ≥ 60 % of its chroma at 100 with L unchanged
(±1e-4) and hue kept; dark saturated colours keep their chroma (red, navy
≥ 95 %, plum ≥ 90 %, deep foliage ≥ 80 %); skin in shadow keeps more than a
cast does; Reach widens it;
Range moves where it ends; Depth deepens black-ish tones monotonically, keeps
black at 0 and leaves tones above Range alone; output stays in 0..1; a
16-bit-fine dark ramp (with a cast) stays monotone in L, with chroma
monotone along it and no steps; the chroma stretch of a smooth gradient never
exceeds 1.5; colour grain on a noisy patch (at the worst-case chroma, ±) only
falls and luma grain is kept; a render bigger than the decision resolution
decides like the preview (strengths within 0.002, monotone over 25-row
spans); options normalise; `weight_map` matches; GPU == CPU (numpy fallback path) via
`adjust_image_opencl` when available; panel: slider sits right under Golden
Highlights, popup writes `shadow_shape` with one undo step, paste row, copy,
sync with the Saturation group, reset clears the options, the mask follows
the popup and goes with it, a gold-map `None` does not hide the shadow map;
the canvas layer is magenta in the shadows and clear above Range; the label
gutter fits every slider label (the gutter grew 100 → 124 px and the panel
340 → 364 px for "Shadow Neutralise", so slider tracks kept their length).
