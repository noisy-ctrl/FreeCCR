# The rotate slider straightens inside the crop; crop mode's grid and red slider

The user, 2026-11-19: "The rotate slider at the bottom needs to rotate the
image within the confines of crop (changing the crop), while keeping the
image straight. At the moment, it's just rotating the image, which isn't
helpful. In the crop window however, can the grid be visible, can the same
long rotate slider turn red or similar."

## 1. Outside crop mode: straighten within the crop

On a converted frame (or any frame in Positive mode: where the crop is shown),
the long slider under the picture no longer turns the whole frame (the image
fine rotation, which showed black corners). It sets the **crop's angle**:

- The picture turns inside a frame that stays upright, as an export gives
  it. Right turns the picture clockwise, as before (`crop_angle = −value /
  100`, the sign `crop_aspect.folded_crop_angle` already uses).
- The crop keeps its centre and its shape and is made **as large as fits**:
  at each angle the box is the starting box scaled by the largest factor ≤ 1
  that keeps all four turned corners inside the picture
  (`core/straighten.fit_box`, exact, per corner and axis). Going back
  towards the starting angle grows it back to the starting box: the starting
  box is remembered for as long as the crop is the one the slider made
  (`img._straighten_memo`, in memory).
- A frame with **no crop** starts from the whole frame, so straightening it
  crops it just enough to hide the corners; back at 0 it has no crop again.
- An old image-level fine rotation is folded into the crop the first time
  (as the Crop panel already does), so the two never stack.
- One undo step per drag (the slider's existing burst); when the drag ends
  the frame re-renders (Glow reads the crop) and the thumbnail and catalogue
  follow.
- An **unconverted** negative (whose crop isn't shown) keeps the old fine
  rotation.
- The slider shows the frame's straighten: `fine − crop_angle × 100`.
  Setting it programmatically never writes back.

## 2. Crop mode: the same slider, red; a grid in the box

- The long slider stays enabled in crop mode, **red** (groove, fill and
  handle in the danger red) so it reads as the crop's tool, and moves the
  crop box's angle like the Crop panel's Straighten slider and the rotate
  knob, all three kept in step (`_draw_crop_overlay` sets it, signals
  blocked).
- A **grid** inside the crop box, turning with it: thirds plainly, twelfths
  faintly, so what will be level in the result can be lined up.

## Tests

- `fit_box`: 0° keeps the box; any angle keeps all four corners inside the
  picture and the centre and shape; the largest such (a corner touches an
  edge); a small box in the middle doesn't shrink.
- Slider on a cropped converted frame: crop angle set, crop inside the
  picture, one undo step for a drag, back to the start restores the box;
  on a frame with no crop it crops, and back at 0 clears it; a folded fine
  rotation is cleared; an unconverted frame still uses fine rotation.
- Switching frames sets the slider without changing the crop.
- Crop mode: the slider is enabled and red, moves the pending angle, follows
  the knob / panel; the grid items are drawn in the box.

## 3. Rotate by dragging outside the box (the user, 2026-11-19)

"In the crop window, can we also be able to rotate when the cursor goes
outside of the crop box and isn't on a crop line. This feels like a standard
ui/ux thing."

- With a box, a drag starting **outside it and off its lines and handles**
  turns it (the knob's rotation, from wherever the drag starts), with the
  rotate cursor on hover (`ImagePreview._crop_mode_at`).
- A new box is still drawn by dragging when there is none (Reset clears it),
  or anywhere outside with **Alt / Option** held.

Tests: a drag outside the box turns it and keeps its size; Alt-drag outside
draws a new box; the hover cursor outside is the rotate cursor.
