# The scopes say when they read part of the picture, and the vectorscope zooms

The user, 2026-11-23: "Can the scopes/waveforms show a red outline around
their boxes to show a changed format" and "Can the circular vectorscope zoom
to show the range of saturation more close up in this skintone mode, with
the scroll wheel. At the moment, it's not very telling if an image is
fairly flat."

## 1. The red outline

While the scopes read only part of the picture (the Sample circle, the Skin
tool's faces, the reference frame with Ref frame ticked), the parade and the
vectorscope are each outlined in red, 1 px (the user, 2026-11-22: 2 px was too heavy), round their rounded boxes
(`_ScopeWidgetBase.set_region`, `REGION_COLOUR` (230, 50, 45)); reading the
whole picture they keep their grey 1 px border. The same test as the
histogram's caption decides it: `ImagePreview._sampling_status()` gives a
caption ("Sample", "Skin", "Ref frame") exactly when a region is read, so
`ScopesPanel.set_sampling_note(note, region=bool(caption))` (the
parameter is new; without it the outline is left as it is). Display only.

## 2. The vectorscope's zoom

- The wheel over the vectorscope zooms into its middle, ×1.25 a notch, from
  ×1 to ×8 (`VectorscopeWidget.set_zoom`, `ZOOM_STEP`, `ZOOM_MAX`); a
  double-click goes back to ×1. Any time, not only with the Skin tool; the
  zoom stays for the session and across frames. The face's corner reads
  "×3.0" while zoomed.
- The trace is recomputed, not enlarged: `scopes.compute_vectorscope(...,
  zoom=)` multiplies Cb/Cr by the zoom and leaves out what then falls off
  the face (not piled on its edge); the panel keeps the last frame
  (`_last_frame`) and recomputes on `zoom_changed`. Past ×1.5 the counts are
  spread by a Gaussian of 0.6 × zoom × (bins / 256) bins: the capture is
  8-bit, so its chroma comes in whole steps that, zoomed, showed as a grid
  of empty bins. The total count is kept (tested within 1 %).
- What moves with it: the 75 % ring (drawn while it is on the face), the
  six targets and their labels (clipped to the face), the probe marker.
  The skin wedge and lines are directions, drawn to the face's edge at any
  zoom. Zoomed, dotted rings at 50 %, 25 % and 12.5 % of full saturation
  are drawn where they fall on the face, labelled on the +Cb axis, so how
  far in you are can be read.

## 3. Tests

`tests/test_scope_outline_zoom.py`: a pixel at Cb 20 lands at Cb 30 at
×1.5, one at Cb 100 drops off, grey stays central at ×8; the zoomed counts
keep their total; the wheel steps ×1.25, caps at ×8 and ×1, a double-click
resets; the panel recomputes the trace on a zoom change and keeps the zoom
for a new frame; `set_sampling_note(region=True)` outlines both scopes red
and False takes it away; the Sample circle drawn outlines them, off doesn't.
