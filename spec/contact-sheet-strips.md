# Contact sheet: the roll as strips of film

The user, 2026-11-20: "Can there be an option to: create what looks like one
single strip of film for each horizontal or vertical (selectable; default
according to film format - as for formats like 645 - film should run
vertical. This would mean that images may need to rotate)". Drafts signed
off the same day, with these answers: frames of the other shape turn **top
to the left**; an option for a **square gate** or the **camera's own**
(uncropped) edge; **never sprocket holes on 120**; the sheet's holes are its
**own option**, not the main toolbar's sprocket tool. Light leaks on the
film's edges were asked for too: drafted separately for sign-off (below).

## Goals

- **Layout…** in the sheet's bar: **Grid** (the sheet as it was) or **Film
  strips**: each strip a piece of film as cut, with the frames on it.
- Strips run **across** or **down**; **By format** (default) runs 6x4.5
  down (its picture's long side lies across the film, so its frames read the
  right way up) and everything else across.
- A frame whose shape is the slot's other way (a portrait 35 mm frame, a
  landscape 645 frame on a strip run across) is turned a quarter, **its top
  to the left**, to lie along the film.
- **Gate**: **Square** (each frame as its crop keeps it, clean edges) or
  **Camera's own** (a little of the scan past the crop too, so the camera's
  own gate edge shows as shot).
- **Film edge** colour: Black (default), Dark grey, Brown-black, Custom….
- **White sprocket holes** (35 mm film only; default on): holes white as the
  film-border tool shows them, or dark as converted. 120 has none, ever.
- Live like the grid: one frame's change redraws only its piece of strip.

## Non-goals

- Synthesising light leaks (drafted for sign-off first; see below).
- Reading the film's real edges from the scan (a full-rebate scanning setup
  is the user's later goal; "Camera's own" already shows what the scan has
  just past the crop).
- Sheet film (4x5, 8x10): not cut into strips; the sheet stays a grid.
- File names, formats and crop status under each frame: a strip carries the
  film's own edge print (frame number, the film's name); the others show in
  the grid (greyed in Text… while strips show).

## The film (`core/film_formats.STRIPS`, mm)

| format | along x across | gap | film | holes | runs |
|---|---|---|---|---|---|
| 35mm | 36 x 24 | 2 | 35 | yes | across |
| half | 18 x 24 | 1 | 35 | yes | across |
| xpan | 65 x 24 | 2.5 | 35 | yes | across |
| 645 | 41.5 x 56 | 4 | 61.5 | no | down |
| 6x6 / 6x7 / 6x8 / 6x9 / 6x12 / 6x17 | 56 … 168 x 56 | 4 | 61.5 | no | across |

Frames to a strip: `film_formats.strip_length` (6, 6, 3, 4, 3, 3, 3, 2, 2, 1).
`strip_geometry(name)` → `StripGeometry` (pitch = along + gap, rebate =
(film − across) / 2) or None. 35 mm perforations: pitch 4.75, 2.8 along x
2.0 across, 2.0 from the edge (ISO 491), one every 4.75 mm from the strip's
start, never one cut by a strip's end.

The strips' format is the frames' most common (`frame_format`: Crop panel
format, auto crop's, crop proportions), else the roll's (the film menu's
Format, `ContactSheet.set_roll_format`, set from `MainWindow._update_title`),
else 35mm; sheet film → the grid.

## Layout (`StripLayout`)

- Scale: the frame's slot is as wide on screen as a grid cell (480 px, 360
  for rolls over 48 frames).
- A frame's CELL is its **piece** of strip: from the middle of the gap
  before it to the middle of the gap after, the film's whole width, in whole
  pixels; drawn clipped to itself, so one frame redraws without touching its
  neighbours and pieces meet with no seam.
- `strip_transform(k)`: strip coordinates (x along from the strip's start, y
  across from the edge carrying the film's name) → composite. Down = turned a
  quarter clockwise (the name's edge on the right, numbers on the left,
  lettering reading downwards, as film held that way).
- The last strip is cut after its last frame; strip ends are rounded 0.6 mm.
- `cell`, `image_rect` (the slot, axis-aligned on screen), `frame_at`,
  `key` (format, direction, count, cell width), `width`/`height` (footer
  below, as in the grid).

## A piece

1. The film: background, then the strip's rounded rectangle in the edge
   colour (clipped to the piece).
2. 35 mm: the holes, white or the background's colour.
3. The frame: `draw_frame(..., respect_crop=True, turn=lie_along(...),
   bleed=...)`: the crop (with its angle, the frame's turns and mirrors), then
   turned −90° when its shape is the slot's other way (`TURN_SLACK` 2 %), fitted
   inside the slot (a different shape leaves film colour round it: a gate
   the picture's shape). With Overlay the crop's outline in its source
   colour, as Respect crop.
4. Edge print, in Text…'s colours: the frame number at the frame's start and
   "NA ▸" at its middle, in the band between the holes and the edge (35 mm)
   or the outer 1.6 mm of the rebate (120), lettering 1.5 mm (120: 0.85 of
   its band); the film (Text… → Film) at each strip's start on the far edge.

**Camera's own**: `bleed` = min(40 % of the gap, 1.5 mm, the rebate not
taken by edge print and holes) of scan past the crop (35 mm 0.8 mm, 120
1.2 mm), so a sliver of gap always stays and the edge print is never
covered. Past the scan's own edge nothing is drawn (film colour).

## UI

- **Layout…** before Text… in the bar (as wide as its words; Text…, Fit,
  100% and Export… too, so the bar still fits 900 px:
  `tests/test_panel_width.py`, 858 px). **Respect crop** moved from the bar
  into Layout…, under Grid (it is the grid's: strips always show the crop).
- The menu (`ContactSheetView.layout_menu`, the seam) is built from Text…'s
  rows (real radio buttons / check boxes, `_menu_row`), so it stays open
  while choosing: Layout (Grid, Respect crop indented, Film strips), Strips
  run (By format (down for 6x4.5), Across, Down), Gate (Square, Camera's
  own), Film edge (Black, Dark grey, Brown-black, Custom…), White sprocket
  holes. Strip rows grey in the grid, Respect crop greys in strips, the holes
  row greys on 120 and Film strips greys on sheet film, live as you choose.
- Remembered: `sheet/layout` (grid | strips), `sheet/strip_dir` (auto |
  across | down), `sheet/gate` (square | camera), `sheet/film_edge` (black |
  grey | brown | #rrggbb), `sheet/holes_white`.
- Export writes what is shown.

## Light leaks (waiting for sign-off)

"maybe options to add film edges (colour changeable) with slight light leaks
on the borders (only)". To be drafted from the app's own drawing and shown
before it goes in: warm fog on the film's edges only, never on a picture,
fixed per strip (the same every redraw), with an amount.

## Tests (`tests/test_contact_sheet_strips.py`)

- Geometry per format; sheet film none; auto crop's names map.
- Strip format: frames, then the roll, then 35mm; sheet film → grid.
- 35mm across 6 a strip, pieces meeting exactly, the next strip below, the
  slot 3:2; 645 down with landscape slots; direction override.
- A portrait picture in a landscape slot lies top to the left; a landscape
  645 picture in a down strip stays upright; `lie_along`.
- Square shows only the crop; Camera's own shows the scan just past it, no
  further, and a sliver of gap stays.
- Holes white / dark; edge colour named, custom, refused; none on 120.
- Edge print: numbers on every frame, the film only at each strip's start;
  off with Text….
- Live: one piece redraws, its neighbours' pixels unchanged.
- Settings remembered; roll format reaches the sheet; Export size.
- Layout… menu rows, greying, stays open while ticking; Text… greys name /
  format / status in strips; Layout… sits before Text….

## Revision 2026-11-20: 35 mm perforations as they are

The user: "fix the proportions a little for 35mm, looking at reference
images of the dimensions and crucially - actual scanned full height 35mm
film". The holes were drawn the wrong way round (2.8 along x 2.0 across:
wide and short). 135 film carries KS perforations, 0.078 x 0.110 in = 1.98 mm
ALONG the film x 2.79 mm ACROSS it, corner radius 0.51 mm, pitch 4.75 mm,
2.01 mm from the edge (ISO 1007; SMPTE KS). Measured on the user's own
full-width scans (half-size decodes, holes found as clear blobs, the pitch as
the scale): 1.98-2.01 mm along x 2.77-2.80 mm across on three rolls, and
25.35 mm between the two rows' inner edges (KS gives 25.37). That leaves
~0.7 mm of rebate between a hole and the 24 mm picture, and the 2.01 mm
band between the holes and the edge is where the edge print sits, as on the
film. `HOLE_ALONG`, `HOLE_ACROSS`, `HOLE_EDGE`, `HOLE_RADIUS` in
`widgets/contact_sheet.py`; a test pins them.

## Revision 2026-11-20 b: 120's edge print as the film's

The user's reference (a full-border 6x7 Portra 400 scan; "this is a
horizontal format like 6x7, unlike 645"): 120 carries no "NA" half numbers.
Its bottom edge has arrows, the frame's number after the second; its top edge
the film's name, at both ends. Strips without holes now print that way: "►"
at a quarter of the frame and "► N" at three quarters along the bottom, the
film (when Text… has Film and the roll's film is known) at the frame's start
and end along the top. 35 mm is unchanged. The 6x7 rebate measured on the
reference, ~2.3-2.75 mm of a 61.5 mm film, matches the geometry
(`STRIPS["6x7"]`, 2.75 mm), so no size changed.

## Light leaks (signed off 2026-11-20)

Layout… → Light leaks: Off / Slight / Strong (`sheet/leaks`). Only ever on the
film's edge (the user, 2026-11-20: "never extend to the images … otherwise
it's unrealistic that the images aren't affected"): a leak fades in from the
edge and is gone by `leak_reach` (35 mm: the holes' outer 60 %; 120: 80 % of
the rebate, ≥ 0.5 mm clear of the picture), drawn under the holes and the
edge print. 35 mm: patches with narrow tongues, some edges clean, the roll's
first strip fogged at its start (light at the cassette's lip). 120 (the
user's two references: a roll-film leak runs as one long wavy streak along
an edge, coming and going): `leak_streaks` / `streak_field`, a soft-edged
band whose strength and depth wander along the film (two slow waves), one
edge usually, sometimes both. Fixed per strip, so the same on every redraw.

Revision (the user, 2026-11-20: "there should be fewer"): a 35 mm edge has a
patch in 30 % of strips' edges (was 1-2 on most), a 120 strip a streak in
40 % (one edge, gaps along it more often); the roll's start is still fogged.

## A drawn gate isn't perfectly square (DRAFT, same branch)

The user's references (a 6x6 and a 35 mm print with their gates): the
corners are rounded, the edges wander a little and are soft. With Gate
"Square" (the drawn gate; "Camera's own" shows the scan's real one) the film's
colour is laid over each frame's outermost edge (`gate_edge`): a rounded
rectangle (r 0.5 mm on 35 mm, 1.0 mm on 120), inset 0.10 mm, its sides moved
in by two octaves of smooth noise (steps of 4 mm and 1.2 mm, ±0.07 mm),
fixed per frame, softened by σ 0.05 mm (the gate sits just off the film
plane).

How often (the user, signing off: "quite rare on 35mm except near beginning
and end. On 120 film, they frequently [appear] top and bottom - I'd say less
frequently though"): 35 mm mid-roll strips have one small patch on
`LEAK_35_MIDDLE` (12 %) of them, the roll's first strip is fogged at its
start and its last at its end; 120 has a streak on `LEAK_120_STRIP` (45 %)
of the strips, half of those on both edges.
