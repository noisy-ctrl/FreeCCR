# Set Black / White Point shows the whole frame

The user, 2026-11-08: "When manually changing black and white point, can the
image temporarily uncrop. Otherwise, once cropped, you have to pick an area
of the negative. It's long-winded to change crop -> pick point -> crop again."

## Behaviour
- While a black or white point pick is armed (`ImagePreview.view.bwpoint_mode`,
  from the panel buttons, their menus or tethering's Black Point…), a cropped
  converted frame is shown WHOLE: `update_preview`'s display-crop gate skips
  the crop, so the rebate and film base outside it can be sampled.
- When the pick ends — a rectangle drawn (whatever its size), Esc (new: Esc
  now abandons a pick), or another mode taking over — the crop shows again.
- The crop itself is never changed; only the display. Sampling already maps
  the drawn rectangle through `map_displayed_to_full`, which handles both.

## Integration
`set_bwpoint_mode` refreshes the view (`_bw_uncrop_refresh`) when the mode
turns on or off and the frame has a crop; the view's release handler calls it
after sampling if the crop isn't back yet; `_on_escape_key` cancels a pick.
Crop, slice, dust and area modes already clear the pick and re-render.

## Tests (`tests/test_dust_crop_display.py::TestBwPointUncrop`)
Arming shows the full 600×400 frame, Esc puts the 300×200 crop back; a pick
drawn on the uncropped view samples the right full-frame area and the crop
returns; a frame without a crop isn't re-rendered.

## Revision 2026-11-23: the sharp view on a Retina screen stayed cropped

The user: "when clicking to manually select the black point, it would be good
for the crop to temporarily undo, to see the film border. Then it crops back"
(asked before; it wasn't working for him). On a high-DPI screen the fitted
view swaps in a hi-res detail render (spec/sharp-fit-preview.md) about 0.35 s
after any refresh, and `_hires_display_pixmap` cut that render to the crop
with no regard for the pick: the uncropped preview showed for a moment, then
the cropped picture came back over it, stretched to the whole frame's place.
The tests run at a device pixel ratio of 1, so it never showed there.

`_hires_display_pixmap` now leaves the crop off while `view.bwpoint_mode` is
armed, exactly like `update_preview`'s gate. Test: a detail render landing
during a pick is shown whole (1200×800 over the 600×400 preview, prescale ½)
and cropped again once the pick ends.
