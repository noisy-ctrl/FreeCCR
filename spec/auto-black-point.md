# Auto black point (per frame)

## Problem

A black-point conversion is anchored to the film base, sampled once by hand
and reused for the roll. That assumes the light is identical for every frame.
It often is not: in one measured roll the blue level of the clear film border
changed by ~70 % between triplets at an identical shutter speed (red/green
within 3 %). With one black point, those frames come out with a colour shift
and a tinted border. The fix is to measure the base on every frame — which is
exactly what a user does by hand when sampling the clear border, so it can be
automated without any learned model.

## Goals

- **Auto black point (per frame)** checkbox in the Film B/W Point section.
  When on, Convert Current and Convert All measure each frame's own film base
  from its clear border and convert with it.
- The strip used is outlined briefly on the preview after Convert Current;
  every conversion reports which edge was used and how sure it is.
- Low confidence never guesses silently: the frame falls back to the sampled
  black point (or, in a batch, the roll's consensus) and the summary says so.
- Manual Set Black Point keeps working; it is the fallback.

## Non-goals

- Detecting borders that are not along the frame edges (unusual holders,
  multi-frame layouts). Those fall back.
- Any trained model.

## Detection (`core/auto_black_point.py`)

Input: the raw (unconverted) preview the manual sampler also reads, `(H, W, 3)`
in 0–65535, array channel order — so values are interchangeable with a
sampled black point.

1. **Edge bands.** For each side, take the luminance profile perpendicular to
   the edge over the central part of that edge, within the outer 6.5 % of the
   frame, and find the brightest line. The band is ±max(2, 0.15 %) lines
   around it, along the central 60 % of the edge. Per channel: median value and
   relative spread (IQR / median).
2. **Validity** of a band:
   - flat: relative spread < 6 % in every channel (an even strip, not image);
   - unclipped: fewer than 25 % of its pixels sit at the frame's ceiling in any
     channel (grain never does that; a clipped border is not a measurement);
   - a step: brighter than the image just inside it (by ≥ 2 % luminance), since
     the clear border is the thinnest film there is;
   - not darker than the interior's 99th percentile ×0.9 in any channel.
3. **Agreement.** Two valid bands agree when every channel is within 8 %.
   - ≥ 2 agreeing → **high**; use the brightest of that group (edge fall-off
     only ever darkens a band, so the brightest agreeing band is the base);
   - exactly one valid band, or valid bands that disagree → **medium**; use the
     brightest valid band;
   - none → **low**; no value.
4. Result: black point (array order, 0–65535), the band's rectangle
   (normalised), side, confidence, a one-line reason.

## Roll check (Convert All)

Detect every frame first. The roll consensus is the per-channel median of the
high/medium results. A frame whose result differs from the consensus by more
than 35 % in **every** channel in the same direction is downgraded to low (a
uniform jump like that is light passing round the film, not a drifting LED —
a drifting light moves one channel, as measured). Low-confidence frames use the
sampled black point if one is set, else the roll consensus, else they are
skipped with a message.

## UX

- Checkbox **Auto black point (per frame)** under the Film Stock row, tooltip
  explaining what it measures and when it falls back. Persisted
  (`convert/auto_black_point`, default off).
- With it on, Convert does not ask for a sampled black point first (the
  no-anchor warning is skipped).
- Convert Current: hint "Auto black point: top border (2 edges agree)"; the
  strip is outlined (dashed orange, 4 s).
- Convert All: summary "Auto black point on 5 frames: 4 high, 1 medium,
  0 fell back".
- The detection is recorded in `conversion_inputs["auto_bp"]` (side,
  confidence) for information; the black point itself goes into
  `conversion_inputs["bw"]` exactly like a sampled one, so every replay site
  (zoom, export, slice) is unchanged.

## Tests

- Synthetic frames: holder + clear border + denser image → exact base, high
  confidence; border on one edge only → medium; tight crop with no border →
  low; a no-film gap brighter than the base on one edge → not chosen; clipped
  border → rejected; edge printing / scratches in the band → median unmoved.
- Roll check: one channel drifting → accepted; all channels jumping → low.
- Real scans (when available): the ten trichrome frames agree with the
  hand-checked bands within 3 %.
- Backend/panel: per-frame black points land in `conversion_inputs`; fallback
  uses the sampled point; the checkbox persists; the no-anchor warning is
  skipped with auto on.

## Revision 2026-10-24: the base may be the frame's maximum

Field report: Convert Current sometimes gave a near-black frame; sampling the
Black Point by hand on that frame fixed it. On the user's trichrome merges
(five frames, "Light balanced film base"), `find_rebate` found the right
border on every frame (two agreeing edges, flat to 0.1-1 %) but called it
**clipped**: the pick was within 2 % of the frame's brightest pixel in a
channel (ratios 0.99), the "2 % anyway" part of the near-ceiling rule. On a
quiet scan, where the holder blocks all other light and there is little
grain, the clear film IS the frame's maximum, so that rule failed every such
frame. Convert then fell back to the sampled black point, taken earlier on
another frame and light, darker than this base, and everything clearer than
it (most of the picture, in the linear two-point map) went to black.

- Clipping is now a PILE at the ceiling: the pick's own pixels with >= 2 %
  at >= 0.999 x the frame's max in a channel (`PILE_SHARE`, `Band.ceil_share`;
  the user's frames: <= 0.07 %), or (as before) within 10 % of the ceiling in
  a channel another edge clipped. Measured on the five frames: 4 high, 1
  medium (one usable edge), each base its own.
- Channel names in the reasons follow the array order, B G R (they said "R"
  for channel 0, which is blue).
- `CCRBackend.resolve_auto_black_point(result, consensus, raw)`: a fallback
  (sampled or roll) that more than 1 % of the picture is clearer than
  (`clearer_share` > `CLEARER_MAX`), while the frame's own low-confidence
  border is not, gives way to that border, source `"own"` (a hint on Convert
  Current, a note in Convert All's summary). A 40 % darker stale black point
  left 23-58 % of the user's pictures clearer than it.
