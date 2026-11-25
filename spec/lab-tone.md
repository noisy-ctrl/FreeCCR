# Lab Tone: the labs' tone curve on one slider

The user, 2026-11-25, on the study's tone finding ("build it as drafted, on a
slider … Yes if that's best"), after the drafts `lab-tone-drafts.jpg`
(Organics v2 + Organic Saturation + Clean Lights at 50, the same plus this at
50, the Noritsu scan, on eight of his frames).

## Goals

- The Noritsu labs' tone rendering on one slider: a soft lifted black, lower
  mids a touch deeper, livelier mids, a soft top that holds white back.
- Measured on the same 50 pairs as Colour Organics v2, so the two add up.
- Lightness only: hue and chroma kept.

## Non-goals

- A per-lab choice. The labs differ (below); the slider's strength is the
  user's. Per-lab curves could come later as options.
- Per-channel (RGB) curves: the colour side is Organics v2's.
- Replacing Shadow Lift, Contrast L ⅃ or Auto tone; they stay as they are.

## Measured

Per frame, FreeCCR's render (default render, the frame's six Channel Levels
numbers fitted to the lab scan, FreeCCR's own conversion-time Auto tone
measured as Convert does, then Organics v2 50 / Organic Saturation 50 /
Clean Lights 50) and the lab scan over the frame's registered area: 100
quantiles of OKLab L each (0.5 .. 99.5 %), matched, so the mapping is the
tone curve without the regression to the middle a "median lab L given our L"
estimate suffers. Per lab the median of its frames' mappings at each L
(inside each frame's own range), the three labs averaged, smoothed (5-point
binomial on a 0.01 grid), sampled at 14 knots (`core/lab_tone_data.py`,
written by `tools/fit_lab_tone.py`).

Curve at 50 (our L -> L): 0 -> 0.113 (about 6/255), 0.16 -> 0.182, 0.32 ->
0.297, 0.48 -> 0.455, 0.64 -> 0.647, 0.80 -> 0.815, 0.94 -> 0.932, 1 ->
0.979 (about 250/255). Fitted without any one lab the knots move by <= 0.04
at black, <= 0.01 elsewhere.

Revision 1 (same day): the first fit and drafts left out Auto tone, so
sunlit areas clipped before the curve (the user: "something weird is going
on with the paving"); with Auto tone in the render, as Convert applies it,
the paving keeps its texture and the refitted curve holds white higher
(0.954 -> 0.979) and black lower (0.136 -> 0.113).

Mean colour difference to the lab scan (OKLab x100, median over frames):

| lab | Auto tone + v2 tools | + Lab Tone 50 | curve without that lab | Lab Tone 25 |
|---|---|---|---|---|
| 060940 (30) | 2.48 | 2.04 | 2.04 | 2.14 |
| 14868425 (6) | 3.12 | 2.49 | 2.56 | 2.64 |
| 00003875 (14) | 1.50 | 2.39 | 2.91 | 2.10 |

All three hold white back (their brightest 0.5 % at L 0.93-0.97); their
black end and lower mids differ, and 00003875 was already close to
FreeCCR's tone, so it is better off low. The 060940 and 14868425 files
were saved through Photoshop at the lab: part of their tone may be the
operator's.

## UX

**Lab Tone** in Lab Finish, right under Clean Lights, 0..100, default 0;
50 = the labs' curve, 100 stronger. "sat" sync group (with the other Lab
Finish tools), tooltip on the label. Shown in B&W too (it is lightness only).

## Processing (`core/lab_tone.py`)

- The curve: a Fritsch-Carlson monotone cubic through the knots (C1, never
  turning back), tabulated on 4097 points.
- Amount k = slider/50: k = 1 is the curve; otherwise its slope raised to
  the power k, integrated, between ends moved linearly (black = k . 0.113,
  white = 1 + k (0.979 - 1)). 0 is the identity, every amount monotone
  (every slope > 0). Curves cached per
  half step.
- Per pixel: OKLab L -> curve(L), a and b kept; `oklab.fit_move` shortens a
  move that would leave 0..1; a pixel moved < 1e-6 comes back exactly.
- Runs right after Clean Lights (where it was measured), before Golden
  Highlights, in `adjust_image` and the OpenCL numpy finishing chain; with
  it on, `_finish_on_gpu` is skipped like Organics v2's (no GPU twin yet).

Quality: no-op at 0; float32; C1 curve; a 65536-step grey ramp stays in
order at 50 and 100; in range; resolution independent (per pixel); grain
scales with the slope like any tone curve (x0.22 in the deepest toe, up to
x1.22 in the mids at 50) and nothing decides on grain.

## Integration

`lab_tone` appended LAST to `adjust_image` / `adjust_image_opencl` and their
fallbacks; `CCRImage` passes `s.get('lab_tone', 0)` at both render sites;
`SlidersPanel.ADJUSTMENT_KEYS` ends with it (created last, inserted into the
Lab Finish section after Clean Lights); `_BASIC_LABELS`; "sat" sync group.

## Tests (`tests/test_lab_tone.py`)

No-op at 0; the curve at 50 through the knots; every amount monotone; 0 the
identity; black and white land at the knots' ends; greys stay grey; hue and
chroma kept; a 65536-step ramp stays in order at 50 and 100; in range and
float32; a pixel on the curve's crossing comes back; `adjust_image` no-op
and moves; the key last.

## Other ways it could have been done (answered for the user)

1. Only what all three labs agree on: the held-back white (a shoulder).
2. One curve per lab, picked from a list.
3. Separate Toe / Mids / Shoulder sliders from the same measurement.
4. Per-channel RGB curves like a print's, which change colour too.
5. Per frame: match each frame's own histogram to the labs' typical one,
   as Auto tone does at conversion.
6. Through the existing tools (Shadow Lift for the black, Contrast L ⅃ for
   the mids, Highlights ⅃ for the top).
