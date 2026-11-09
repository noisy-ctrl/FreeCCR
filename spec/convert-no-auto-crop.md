# Convert no longer auto crops

The user, 2026-11-09: "Lets change convert to not have auto crop. The latter
is getting it wrong a lot."

## Change
- Settings → Conversion → "Crop each frame after converting it"
  (`ccr_backend.crop_after_convert`, `convert/crop_after`) is now OFF by
  default. A stored "on" (the old default) is turned off once at start
  (`convert/crop_after_off_2026_11`); ticking it again afterwards sticks.
- Convert Current, Convert All and tethered captures therefore leave the crop
  alone. Auto Crop / Auto Crop All (toolbar and panel) are unchanged.
- Exception: a frame carrying Capture's frame guide (`img.capture_guide`) is
  still cropped to its guide at conversion while "Crop to Capture's frame
  guide" (`crop_from_guide`) is on. That box is one the user placed, not one
  auto crop guessed. `CCRBackend.crops_at_conversion(img)` is the one test:
  `is_crop_candidate(img)` and (`crop_after_convert` or (`crop_from_guide`
  and a capture guide)); used by `plan_frame_conversion`,
  `crop_after_conversion` and `_convert_all_crops`.
- The measurement area for auto tone / white point still comes from
  `frame_picture_rect` (unchanged); only the crop itself is no longer set.

## Tests
`tests/test_convert_crop_and_clip.py`: default off; with it off a plain frame
converts uncropped and a frame with a capture guide is cropped to it; the
migration turns a stored True off once and leaves a later choice alone.
