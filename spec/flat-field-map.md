# Flat field map: see what a frame's flat field correction does

The user, 2026-11-20: "Maybe the difference is too subtle to tell but I
can't see the difference in the flat field correction. Is there a way to see
a map overlay (maybe a new tool in top toolbar)". Drafts (wash, contour
lines, both, a before/after split; three icons) answered: "the button
should be left of hdr. 1 shading looks good. Right click option please to be
able to turn up the exaggeration. I think the overlay colour should be pink,
too."

## Goals

- A toolbar tool, **Flat field map**, immediately left of HDR (a picture:
  the signed-off "shading" icon, a frame whose corners are shaded), toggles
  a display layer over the frame on screen: where its flat field
  correction lifts the picture, as a **pink wash** (stronger = more lift)
  with **contour lines** and their values, plus a legend.
- **Right-click**: Exaggeration ×1 / ×2 / ×4 / ×8 (how strongly the map
  draws a given change; the contour lines get finer with it), Contour lines
  on/off. Choosing one turns the map on. Remembered (`view/flat_map_gain`,
  default ×2; `view/flat_map_contours`, default on).
- Display only, like False colour: never in the picture's pixels, so
  scopes, histogram, probe, thumbnails and exports never see it. Off at
  start-up, never saved with a frame.

## Non-goals

- A before/after comparison (a second render without the flat): offered,
  not chosen yet.
- Showing the flat's colour correction as colour: the wash is the mean of
  the three channels; the legend gives each channel's corner figure.

## What it shows

The gain the decode applied (`img._flat_used`, the tracked profile, kept
with `flat_applied` by `_apply_field_correction`, in memory only; the
decode's frame size `_flat_full_hw` and whether it went mono). The map is
the gain rebuilt at the frame's preview geometry:
`core/flat_map.frame_gain(profile, full_hw, source_ops, (w, h), mono)` =
`flat_field.gain_for_size` at the full decode's shape (long side 1600), the
frame's slice chain (`CCRImage.apply_source_op`, so a sliced frame shows
its part), resized to the uncropped preview; then cropped like the
displayed picture (the zone layer's path, now `_crop_layer_like_display`)
and drawn as a child of the picture item (z 1.4: above False colour, under
the tool masks and HDR dots), so it turns and mirrors with the picture.

`ev = mean over channels of log2(gain)`: the flat is normalised to 1 at
its centre, so the corners are lifts (pink) and a place brighter than the
centre (a hot spot) is lowered (blue, the only other colour).

- Wash: alpha = 0.7 · min(1, |ev| · k / 0.5): at ×1 a half-stop change is
  full pink, at ×8 a sixteenth of a stop.
- Contours every 0.1 / 0.05 / 0.02 / 0.01 EV at ×1 / ×2 / ×4 / ×8 (at most
  40), white with a dark edge, labelled where each crosses the line from
  the centre to the strongest corner.
- Legend (bottom-left, above False colour's when both show): the flat's
  label and whether it followed the carrier, "Lifts up to +x EV (R · G · B
  corner figures)", the wash's ramp "0 … +0.5/k EV", the line spacing and the
  exaggeration. A frame with no flat: "No flat field on this frame". A frame
  decoded before this was kept: the flat it would get now, untracked, said
  so.

## Tests (`tests/test_flat_map.py`)

- `ev_map` signs and channel mean; `frame_gain` follows a slice; mono.
- `layer_rgba`: transparent where nothing changes, pink lifts, blue lowers,
  ×2 doubles the alpha below the cap, contour count grows with k.
- Canvas: the layer is a child of the picture at z 1.4, its size the
  shown pixmap's, the displayed pixels untouched; legend text; right-click
  menu choices remembered and turning it on; no flat → legend says so.
- Toolbar: the tool sits right before HDR, a picture with a "Flat field
  map:" tooltip; the toolbar still fits (`tests/test_panel_width.py`).
