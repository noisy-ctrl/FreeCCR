# The toolbar's Auto Crop asks, like Convert

The user, 2026-11-19: "For some reason, we can't batch autocrop by
highlighting frames and clicking auto crop. Can we have a similar popup that
says auto crop current, autocrop all, auto crop selected (or whatever mirrors
the convert button's wording)."

## Behaviour

The toolbar's Auto Crop (`ImagePreview.auto_crop_current`) asks first, like
Convert (`spec/convert-button-choice.md`):

> **Auto crop the current frame, the selected frames, or every frame?**
> [Auto Crop Current] [Auto Crop Selected (N)] [Auto Crop All] [Cancel]

- **Auto Crop Selected (N)** is there only with more than one thumbnail
  selected (`ThumbnailList.selected_indices`), and is then the default
  button; otherwise Current is.
- **Current**: the panel's Auto Crop for the frame on screen, as before.
- **Selected**: every selected frame through the same steps as Auto Crop All
  (detect each on its raw scan with what has been learned, the roll check,
  then apply), but on the frames you chose, and, like Auto Crop on one frame,
  it **replaces** their crops whatever they were (each frame its own undo
  step). A progress window with Cancel while it detects; Cancel changes
  nothing. The summary is Auto Crop All's.
- **All**: the panel's Auto Crop All (its own question, which says how many
  hand or confirmed crops it leaves alone), as before.
- `ImagePreview._ask_auto_crop_scope(n_selected)` is the test seam:
  "current" / "selected" / "all" / None.

## Data / integration

- `CCRBackend.auto_crop_all(progress, should_cancel, images=None)`: with
  `images` it works on those frames instead of `auto_crop_candidates()`.
- `SlidersPanel._on_auto_crop_selected(indices)` runs it with the progress
  window and refreshes the list and the frame on screen.

## Tests

- One frame selected: the box offers Current / All only; two or more: also
  Selected (N), the default.
- Selected crops exactly the selected frames, replacing a hand crop, and
  leaves the others alone; Cancel in the box does nothing.
- Current and All call what they did before.
