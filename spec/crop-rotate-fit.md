# Turning the crop box keeps it inside the picture; one grid

The user, 2026-11-22: "Rotate needs work within the crop tool. Now has 2
grids? One white and green. One doesn't move. Not super helpful. Also, the
rotate tool doesn't stay within the confines of the crop. Black borders
encroach."

## Goals
- Turning the crop box in Crop mode (the long slider under the picture, the
  panel's Straighten, the knob, a drag outside the box) never lets the box's
  corners leave the picture: no black corners after Done.
- One grid while turning in Crop mode: the box's own (white, turning with
  it). The fixed green grid is for straightening the picture outside Crop
  mode, where the picture turns and the screen is the level reference.

## Non-goals
- Moving or resizing a turned box is not reshaped while it happens (the
  commit's safety net below covers it).
- Straightening outside Crop mode is unchanged (it already fits:
  spec/straighten-in-crop.md).

## Behaviour
- **Fit while turning.** The box at angle a is the box it started from,
  scaled about its centre by the largest factor <= 1 that keeps it, turned
  by a, inside the picture (`core.straighten.fit_box`, the same function the
  straighten slider uses). Its centre and shape stay. The starting box is
  remembered while the box is only being turned (`_crop_turn_memo` = (box as
  fitted, box it started from)), so turning back grows it back; any move,
  resize, new box, Auto or Reset makes the box as it now is the new start.
- **Commit safety net.** Done with a turned box fits it once more (only ever
  smaller), so a turned box moved against an edge can't commit corners
  outside the picture.
- **Grid.** `ImageView.drawForeground` draws the fixed grid only outside
  Crop mode.

## Tests (`tests/test_crop_rotate_fit.py`)
- Turning the pending box by 10° shrinks it to fit (all four corners inside
  the pixmap, within 1e-6), keeps its centre and aspect; turning back to 0
  restores the starting box exactly.
- The knob drag, the slider and the panel's Straighten all fit.
- A move after turning starts from the moved box.
- Done with a turned box pushed against an edge commits a box whose corners
  are inside the picture.
- The fixed grid is not drawn in Crop mode while the slider is pressed, and is
  outside it.
