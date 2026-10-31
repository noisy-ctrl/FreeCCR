# Details (sharpening) defaults: 30 / 2 / 0, set in Settings

The user (2026-10-31): "Can we make 30/2/0 defaults for details (changeable
in settings)".

## What changes
- `core/detail_defaults.py` holds the values an image starts with:
  `FACTORY` = Amount 30, Radius 2, Masking 0 (was 25 / 25 / 0 in
  `SlidersPanel.SLIDER_DEFAULTS`). Settings → General → **Details**:
  "Sharpening starts at Amount [ ] Radius [ ] Masking [ ]" (0-100 each),
  saved as `adjust/detail_defaults` ("30,2,0"), restored at start-up.
- The panel's `_default_for` reads them (slider values for an image with no
  sharpen keys, double-click reset, paste rows' "differs from default").
- **The render reads them too.** Before, an image whose adjustments had no
  sharpen_* keys (a new frame, or one only converted / auto white-balanced)
  rendered UNSHARPENED while the panel showed 25, and only picked the 25 up
  when any slider moved. Now `CCRImage.apply_adjustments` falls back to the
  same defaults, so what the panel shows is what renders and exports.
- **Saving writes them down**: `catalog._adjustments_to_json` →
  `detail_defaults.written_down`: an edited dict with no sharpen keys is saved
  with the defaults it rendered with, so a reopened frame looks the same even
  after the defaults change, and the old-catalogue rule (no sharpen keys →
  sharpening off, spec/sharpening.md) only meets catalogues from before the
  Details section. An empty (untouched) dict stays empty and keeps following
  the defaults; `_is_pristine` is unaffected.
- Changing the defaults in Settings re-renders every open image that hasn't
  set its own sharpening (busy cursor), and the sliders follow.

Radius 2 is 0.08 px at full size (25 = 1 px): the Settings note says to
judge it at 100 %.

## Tests
`tests/test_details_defaults.py` (clean/encode/decode; written_down; an
untouched image renders with the defaults and one with its own keeps them),
plus the sharpening, catalogue, paste and duplicate tests updated for 30/2/0
and the written-down keys.
