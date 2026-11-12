# Auto crop cuts to the format's proportions

## Why
Auto crop finds each frame's own edges, so 35mm frames came out between about
1.49 and 1.52. Exported on a canvas at a fixed long edge, the short side
follows each crop's shape, so the border on that side varied frame to frame
(spec/export-canvas-border.md, revision 2026-11-12).

## Behaviour
After detection, the crop is cut to an exact ratio: the largest box of that
ratio inside the box found, centred on it, at its angle
(`auto_crop.fit_ratio`). The ratio is long/short, so each frame keeps its own
orientation (the Crop panel's Landscape/Portrait toggle is not used).

Which ratio (`CCRBackend.crop_ratio_for`), first match wins:
1. **Capture's format** for the shot (the guide's Format list, carried on
   `img.capture_guide["format"]`), while *Crop to Capture's frame guide* is on.
   With it off Capture is ignored entirely, as before.
2. **The Crop panel's ratio** (`crop/aspect_key`, custom W:H included), via
   `crop_aspect.saved_ratio`.
3. Free / Original / nothing: the frame as found (unchanged behaviour).

A fit that would cut more than 8% off a side (`RATIO_MAX_TRIM`) is not
applied: the ratio is most likely the wrong format for that film (e.g. 3:2
left selected while scanning 6x7). The crop's reason says so.

## Integration
Applied in `detect_frame_crop` (so Auto Crop, Auto Crop All, Find Frame Auto,
crop after converting and the guided crop all get it), and again in the roll
check after `snap_soft_sides` re-places a frame's sides. Crops already set are
not changed; run Auto Crop again on them.

## Tests
`tests/test_auto_crop_ratio.py`: exact 3:2 from several detections, only
ever trims, centred, angle kept, portrait kept, wrong format left alone,
`saved_ratio`, Capture first / setting off falls back to the Crop panel.
