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
