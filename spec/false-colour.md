# False colour (and the HDR glow as dots)

An exposure check like a cinema monitor's false colour: the picture in
greyscale, with the classic cinema-camera signal bands painted in colour. Because
that scale uses pink, the HDR glow view (spec/hdr-gain-map-export.md) moves
from a magenta tint to a pattern of dots, so both can be on at once.

## Goals

- A **False colour** toolbar toggle, after **☀ HDR**, shown as an icon of
  the scale's six band colours (tooltip "False colour …"). A text label
  pushed Export into the toolbar's overflow menu at window widths around
  1500 px, so it is icon-only. Off at startup, not saved.
- While on, the canvas shows the displayed picture in greyscale by signal
  level, with these bands coloured:

  | Colour | Signal level | Meaning |
  |---|---|---|
  | Purple | 0–2.5 % | black clipping |
  | Blue | 2.5–4 % | just above black clipping |
  | Green | 38–42 % | mid grey |
  | Pink | 52–56 % | one stop over grey (skin) |
  | Yellow | 97–99 % | just below white clipping |
  | Red | 99–100 % | white clipping |

- A legend in the canvas's bottom-left corner while it is on, including a
  line for the HDR dots.
- The HDR glow is drawn as **dots** whether or not False colour is on (the
  user's choice), so there is one look for it.

## Non-goals

- Custom bands, a waveform readout of the level under the cursor.
  (A stop-based scale was added on 2026-10-23: see the last section.)
- Anything in the exports, thumbnails, histogram, scopes or colour probe.

## Processing

`core/false_colour.py`:

- **Signal level** = video luma of the finished display picture, Rec.709
  weights on the display (gamma-encoded) values, 0–1. That is what a
  monitor's false colour reads, and it follows every slider.
- `false_colour(rgb8)`: greyscale by level, each band `[low, high)` painted
  its colour (red includes 100 %).
- `glow_dots_rgba(strength)`: the HDR halftone. A 6 px grid (preview
  pixels); each cell has a 2 × 2 white dot and a 2 × 2 black partner offset
  half a cell, so the pattern reads on white, black and every band colour. A
  cell shows when the glow strength (0–1, from the glow opacity map) reaches
  its 4 × 4 ordered-dither threshold, so stronger glow means denser dots and
  any glow at all shows the sparsest layer.

## Canvas

Both layers are their own `QGraphicsPixmapItem`s, children of the picture
item (so they inherit orientation, fine rotation and the hi-res prescale),
never part of the picture's pixels:

- **False colour** (z 1): computed from the pixmap actually displayed (the
  cropped preview or the hi-res tile), capped at 4096 px on the long side for
  GUI-thread cost, cached on the pixmap's `cacheKey`, scaled to cover it
  exactly, drawn with nearest-neighbour scaling. Refreshed at the end of
  `apply_transformations`, like the glow layer, so it follows every render,
  zoom tile and crop.
- **HDR dots** (z 2): as before (the preview render's glow map, cropped like
  the preview), now built by `glow_dots_rgba` and drawn with nearest-neighbour
  scaling so the dots stay crisp.
- **Legend**: a `QLabel` on the view's viewport, transparent to the mouse,
  placed bottom-left on toggle and on every view resize.

The scopes, colour probe and histogram read the picture (or its own data),
so neither layer reaches them.

## Tests (`tests/test_false_colour.py`)

1. Band mapping at and either side of each edge; greyscale elsewhere.
2. Dots: none without glow; sparse at a faint glow, every cell at full; only
   opaque black/white or transparent pixels; monotonic in strength.
3. Canvas: the toggle shows a child layer covering the picture with exactly
   `false_colour` of the displayed pixmap; scopes capture unchanged; legend
   shown bottom-left, hidden when off; the HDR dots sit above it.

## 2026-10-23: a text button, no brand names

The toolbar button reads **False colour** (it was the scale's colour stripes
as an icon; `theme.false_colour_icon` is gone), like the ☀ HDR button. To keep
the canvas toolbar inside the canvas at the window's minimum width in a
Mac-wide font, "🧹 Dust Removal" became "🧹 Dust" (the panel keeps its full
title), the group spacers went 5 → 3 px and the toolbar's button spacing
2 → 1 px; `test_preview_toolbar_fits_at_the_smallest_window` checks it at
1.06× the test font. No camera maker is named anywhere in the UI or docs.

## 2026-10-23: stop zones

A second scale, picked on the **False colour** button's right-click menu or
in Settings → General → Preview, remembered between launches
(`view/false_colour_scale` = `bands` | `zones`,
`view/false_colour_zone_source` = `negative` | `picture`). Picking a scale from
the menu turns False colour on.

**Zones** (`core/false_colour.ZONES`): 15, in stops from mid grey, `[low,
high)`: −6 (≤ −5.5, black), −5 … −2 whole stops, −1 (−1.5..−0.75), −½
(−0.75..−0.25), 0 (±0.25, grey), +½, +1 (0.75..1.5), +2 … +5, +6 (≥ 5.5,
white); colours after the user's reference (purple, blues, greens either side
of grey; yellows, oranges, reds above). No product or brand is named.

**What they read.** Such a scale needs a scene-referred (log) signal: on a
finished, display-referred picture white is only log2(1/0.18) ≈ +2.47 stops
over 18 % grey, so +3 … +6 can never appear (the same reason a monitor's stop
scale wants a log feed, not a graded one). So by default the zones read the
**negative**: a film negative recorded the scene in log.

- `negative_stops(raw, base)`: the green record's density above the film
  base, `d = log10(base_g / raw_g)` on the unconverted linear scan
  (`CCRBackend.raw_preview`: the live raw before converting, a 1080 px
  re-read after — the conversion is untouched), against a normally exposed
  grey at **0.70** above base with film gamma **0.6**, i.e. one stop =
  0.6·log10 2 = 0.18 density (the Cineon / lab-aim convention). The film base
  itself is ≈ −3.9 stops: a negative holds nothing below it, which is why
  −4 … −6 only show at the base. A 1 px Gaussian on the stops keeps grain
  from speckling the zone edges (a reading only).
- The base: the conversion's black point (`conversion_inputs["bw"][0]`), else
  the sampled black point, else the frame's clearest film (each channel's
  99.9th percentile). The legend says which.
- The map is cached on the image (`_zone_cache`, uint8 zone indices; keyed on
  file, slice ops, profile, conversion and black point), so a converted frame's
  raw is re-read once, and cropped like the preview exactly as the HDR glow
  layer is. The grade does not change it — by design, it is the exposure.
- **The finished picture** (`picture_stops`): its luminance in linear light
  (sRGB decoded, Rec.709 weights) against 18 %. Follows the grade; tops out
  at zone +2. Positive mode always reads the picture.

Legend: the 15 swatches in a row with their labels and one line saying what
is read. Tests: `tests/test_false_colour_zones.py`.
