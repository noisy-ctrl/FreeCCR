# Scopes / histogram: sample the reference frame

## Goal

A toggle that points the Scopes (RGB parade + vectorscope) **and** the
sliders-panel histogram at the red reference frame (right-drag on the image)
instead of the whole image, for judging one area such as a grey card or a
face.

## Non-goals

- No new selection tool: the existing reference frame is reused.
- No effect on conversion. The frame's rectangle is only **read**.

## UX

- A checkable **Ref frame** button in the Scopes header, persisted in
  QSettings (`scopes/sample_reference`, default off). It works with the scopes
  collapsed, because the histogram follows it too.
- While sampling the frame, the header reads `Scopes · reference frame` and
  the histogram shows a small "Ref frame" caption. With the toggle on but no
  frame drawn (or under a rotated crop, where the frame isn't displayed), both
  fall back to the whole image and the header says `whole image (no frame)`.
- The hover probe's readout still works everywhere; its scope markers only
  show while the cursor is inside the sampled area.

## Processing

`ImagePreview._reference_sample_region()` returns the frame's scene rectangle
clipped to the image. `_capture_display_image(region=…, max_w=…)` renders only
that rectangle of the displayed item, so the sample is exactly what is
visible inside the box (fine rotation, crop, orientation and the hi-res swap
included). The histogram is built from the same capture (≤512 px wide) with
`np.bincount`; whole-image mode keeps the image's own crop-aware histogram.

## Safety

The frame doubles as the legacy reference-frame conversion input. B/W point
conversions bake their anchors and never read it, so drawing a frame to sample
does not change them. Two existing behaviours to be aware of: the toolbar's
legacy **Convert** uses the frame, and **Auto** only auto-frames (and converts)
images that have no frame yet.

## Tests

`tests/test_scope_sample_area.py`.
