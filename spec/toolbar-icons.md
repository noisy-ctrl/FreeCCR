# Toolbar icons

The user, 2026-11-19, with a mock-up: the canvas toolbar's buttons as
pictures, "more monochrome to fit with existing UI", new rotate buttons that
don't look like undo and redo, no dot by the camera, no rotate squiggle on
crop and no specks by the dust blower, and a Crop button. Drafts were signed
off with: Crop after the flip buttons, the camera Capture already had, a
coloured False colour, and Capture in the middle.

## What the toolbar is now

Rotate Left, Rotate Right, Mirror Vertical, Mirror Horizontal, **Crop**,
Sprocket holes | Area, Gradient, Dust removal | HDR, False colour …… Capture
(centred: a stretch either side, `capture_push` / `export_push`) …… Auto Crop,
Un-convert, Convert, Export, zoom. Area, Gradient, Dust removal, HDR and False
colour were words; they are pictures now, so each tooltip starts with the
button's name ("Area: …"). Auto Crop, Un-convert, Convert and Export stay
words. The row needs ~764 px of the canvas's 900 at the smallest window
(was ~890).

- **Crop** (`ImagePreview.crop_action` → `open_crop`) does what the panel's
  Crop button does, and is enabled with it (the sliders' gate, like Dust).
- Icons are drawn by `tools/make_toolbar_icons.py` into `src/icons/` (256 px,
  black on transparency, shading by alpha, tinted by
  `theme.load_tinted_icon`); the rotate, mirror and sprocket files keep their
  old names. Mirror: one triangle filled, its reflection outlined.
- **False colour** is the one coloured icon: a ring in its bands' own colours
  (`core.false_colour.BANDS`, a little softened), loaded with
  `theme.load_colour_icon` (as drawn; a flat grey glyph when disabled).
- Capture keeps `camera-icon.png` (the user's choice).

## Tests

`tests/test_toolbar_icons.py`: order and pictures, tooltips lead with the
name, the words stay words, False colour coloured and grey when disabled,
Crop follows the panel's button. `test_panel_width` still guards the fit.

## Revision 2026-11-19: the film canister

A **Film Presets** button, a 35 mm cassette with its leader out
(`film-canister-icon.png`, square corners, a real cassette's proportions
after the first draft "looked weird"), just left of Capture in the middle:
`film_presets_action` → `open_film_presets` → Settings at Film Presets.

## Revision 2026-11-20: Flat field map

**Flat field map** (`spec/flat-field-map.md`) sits right before HDR: a frame
whose corners are shaded (`flat-field-icon.png`, `tools/make_toolbar_icons.py`
`flat_field`, the user's pick of three drafts, "1 shading").
