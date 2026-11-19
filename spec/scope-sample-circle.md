# Scope sample circle

The user, 2026-11-19: "Can we add another button for the top menu: A
circular dotted line with a ruler in it. When toggled, this allows you to
draw a circle sample for the waveforms to see (only). It doesn't perform what
the area button does. It simply points the waveforms and scopes to one area
(so you can refine skintones and more)." Later: "The c round dots icon looks
ok but the ruler maybe should run straight up", and "The new sample area
meter button should give the '- Scopes' label an added description that
indicates it's looking at a specific area. I'm not sure how we did that
before but it seemed to work."

## Goals / non-goals

- Point the scopes (waveform / parade and vectorscope) and the histogram at
  one round area of the picture, to judge skin tones, a grey card, a sky.
- A measuring tool only: nothing is stored on the frame, nothing renders,
  exports or converts differently (unlike Area, which is an adjustment
  layer).

## UX

- A toolbar toggle after False colour: a circle of round dots with an upright
  ruler (`src/icons/scope-sample-icon.png`, drawn by
  `tools/make_toolbar_icons.scope_sample`); tooltip starts "Sample circle".
- **On**: the cursor is a cross over the picture; **drag** draws a circle
  from its centre (where you press) out to the pointer. Drag **inside** the
  circle moves it; drag on its **rim** resizes it. A tiny drag (< 4 px) is a
  click: it moves the circle's centre there.
- The circle is drawn as a dotted line (white over a dark dash, so it reads
  on light and dark), never in the picture's pixels.
- While a circle is drawn, the scopes and the histogram read only the
  picture inside it; the Scopes header reads **"- Scopes · sample circle"**
  (the same note the old reference-frame metering used) and the histogram's
  caption "Sample". With no circle yet the note reads "sample circle (drag
  to draw)" and the scopes show the whole picture.
- **Off**: the circle is hidden, the scopes and histogram go back to the
  whole picture. Turning it on again brings the same circle back.
- The circle is kept as a share of the displayed picture (centre as shares
  of its width and height, radius as a share of its width), so it stays at
  the same place relative to the frame when you switch frames, zoom or pan.
  Session only.
- Crop, Dust, Area and Slice take the mouse while they are active (the
  circle stays shown and still drives the scopes); the white-balance picker
  and black / white point picks too.

## Integration

- `ImagePreview.sample_action` (checkable), `set_sample_mode(on)`,
  `sample_mode`, `sample_circle` ((cx, cy, r) shares or None),
  `sample_press/move/release(scene_pos)`, `_sample_circle_scene()` (centre and
  radius in scene coordinates from the picture item's scene bounds),
  `_refresh_sample_item()` (re-placed whenever the picture is redrawn, end of
  `apply_transformations`).
- `_reference_sample_region()` returns the circle's bounding square while a
  circle is active (it wins over the old reference frame), and
  `_capture_display_image(..., circle=True)` keeps only the pixels inside the
  inscribed circle in its mask; `_sampling_status` gives ("Sample", "sample
  circle").
- `GraphicsImageView` passes left-button press / move / release to the sample
  handlers when sample mode is on and no other mode has the mouse.

## Tests

- The toggle sits after False colour with the icon; on/off flips
  `sample_mode` and the Scopes note.
- A drag draws a circle (centre, radius); inside moves it; the rim resizes
  it; a click moves the centre.
- The capture with the circle masks out the corners of its square; the
  scopes and histogram are fed the circle's pixels (a red disc in a blue
  picture: the histogram has red only).
- Off: the whole picture again; on: the same circle.
- The circle follows the picture's place (shares).
