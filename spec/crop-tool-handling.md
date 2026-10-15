# Crop tool handling

Two complaints about the manual crop tool: the box sometimes flips between
landscape and portrait while being dragged, and the area that moves the box
as a whole is too small.

## 1. Causes (from the code)

- **Moving.** Only a square of ±14 view px around the centre glyph was the
  move handle. A press anywhere else inside the box fell through to "draw a
  new box", so an attempt to move the box replaced it with a new one shaped by
  the drag. With Free selected that new box is whatever shape the drag was,
  which reads as the box changing shape.
- **Edges** were grabbable only within ±14 px of their midpoint glyph.
- **Orientation.** With a ratio preset (a film format, 3:2, …) the
  Landscape/Portrait toggle is a remembered preference (`crop/orientation`)
  and is not tied to the box. A landscape box on a frame entered with Portrait
  remembered from the last crop kept its shape until the first corner or edge
  drag, which then enforced the portrait ratio: the box snapped to portrait.

## 2. Hit zones

In the box's own (un-rotated) frame, with `tol` = 14 view px:

| Zone | Where |
|---|---|
| rotate | within `tol` of the knob above the top edge (checked first) |
| corner | within `tol` of the corner outside the box, and within `tin` inside |
| edge | within `tol` of the edge line outside, `tin` inside, anywhere along it |
| move | anywhere else inside the box |
| new box | outside the box |

`tin` = min(`tol`, a quarter of the box's width or height), so on a small box
the middle half stays a move zone; its edges and corners are still grabbable
from just outside. The centre glyph stays as a marker.

## 3. Orientation follows the box

`CropPanel.match_box_orientation(w, h, rotation)` sets the toggle to the way
a box of w x h points on screen (a quarter turn swaps them), without
reshaping it, and remembers it. It does nothing for presets with no
orientation (Free, Original, 1:1, 6x6, Custom) or a box within 2 % of square.

It runs:
- when a corner or edge drag starts on an existing box (the case that flipped);
- on entering crop mode with an existing box (`seed_crop_ratio_on_entry`), so
  the panel shows the box's orientation;
- after the panel's Auto puts a detected box in.

Choosing Landscape/Portrait by hand still reshapes the box, as before. Drawing
a new box (outside the old one) uses the toggle.

## 4. Tests

`tests/test_crop_tool_handling.py`: the zone table (inside away from the
centre moves, edges near their ends, just outside an edge, corners, rotate,
outside = new); a small box moves from its middle; an inside drag moves the
box rather than replacing it; a corner drag on a landscape box with Portrait
remembered keeps it landscape at the preset's ratio and updates the toggle;
entering crop with a portrait box sets Portrait without reshaping; a quarter
turn swaps on-screen orientation; square boxes and Free leave the toggle.
