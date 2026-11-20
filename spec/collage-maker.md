# Collage maker

The user, 2026-11-20: "Can we have another button in top toolbar? A collage
maker. Designed around instagram post and reel/story formats with the option
for specified height and width. Images selected in the left browser are added
but you should be able to right click to remove/add." With six reference
pictures (a 100-frame grid, a torn band over a picture, a two-slide carousel
with frames crossing slides, a tight mosaic, a story with a 35 mm strip over a
background, polaroids and a film strip with see-through edges).

Drafts (mock-ups of the view, the layouts drawn from his lab scans, three
icons) answered:

1. "Left hand side if possible for new button."
2. Carousel: "1 image per frame to swipe. Instagram export defaults sure. I
   think layout variation (including aligned height) could be good."
3. Film borders "from the scan" or generated: "A choice between the two? I
   think the synthesised versions aren't quite there yet though but a
   commendable WIP". Light leaks only on the film's edge, never on a picture.
4. Tear: "Looks alright … maybe a WIP and I might tear bits of card and scan
   those textures to give to you."

## Goals

- A toolbar picture button, **Collage**, the last picture of the LEFT group
  (after Sample circle). Icon: draft A, a grid of four unequal cells (the
  user picked the place, not the picture: A until he says otherwise).
- It shows the **Collage view** in place of the canvas, as the contact sheet
  does (a sibling of `image_preview` in the central layout; the per-frame
  controls greyed while it shows; **Back to Frame** returns).
- The collage holds an ordered list of **members** (frames of the open roll).
  Opening it with more than one thumbnail selected takes the selection; with
  one or none, every frame. The thumbnail list's right-click gets **Add to
  Collage** / **Remove from Collage** (for the selected frames); the view's
  strip of members has **Remove from Collage** on right-click and takes a drag
  to reorder. Selecting in the list while the view shows changes nothing by
  itself (an accidental click must not rebuild a finished collage).
- **Formats**: Post 4:5 1080 × 1350 (default), Square 1080 × 1080, Landscape
  1.91:1 1080 × 566, Story / Reel 9:16 1080 × 1920, Custom (width and height,
  200–8000 px).
- **Layouts**, all arranged automatically:
  - **Mosaic**: justified rows filling the canvas (the split chosen so the
    rows fill the height with rows of similar height, no lone giant).
  - **Carousel**: N posts side by side (2–10, default 3), each exported as
    its own picture to swipe. Styles: **Varied** (columns of one or two
    frames, sizes and heights varied, white space kept; a frame crosses from
    post to post only with ≥ 30 % of it on each side, else it moves clear of
    the edge), **Aligned height** (one band of equal-height frames running
    through the posts, the same seam rule), **Full height** (the user,
    signing the mock-ups off: "the option for full height or near full height
    frames - so each photo occupies something like 95% of the full height and
    are aligned, but are split between swipes still": every frame the same
    height, **Height** 80–100 % of the post, default 95 %, centred, in one
    row through the posts with the gap between them; frames are split
    between swipes freely, but never leave a sliver: a frame showing less
    than 12 % of its width, or under 60 px, on a post moves to start at the
    edge instead), **Each post its own** (a mosaic per post). The number of
    posts follows the frames in Full height (as many as their row needs,
    rounded up; the last post's spare room spreads the gaps), or is set by
    hand (2–10) in the others. Coloured outlines show the posts on screen
    only.
  - **Triptych** (2–6 frames, default 3): in a row or a column, on the
    background colour or over one frame (blurred or sharp), plain or on a
    film strip.
  - **Film strip**: the frames on a generated strip over the background, with
    **See-through film edges** (the rebate and holes let the background show,
    the pictures stay solid).
  - **Tear** (draft look): a band of one frame torn out of paper over another
    frame. Marked "draft" in its tooltip until the user's scanned card
    textures replace the drawn edge.
- **Respect each frame's crop**: on = every frame keeps its crop's shape (a
  mosaic may leave slack, centred); off = a crop may change by at most
  **At most N %** (0–30, default 6) so rows fill; nothing is ever squashed,
  only trimmed.
- **Film borders**: None / **Generated** (35 mm with KS holes at their real
  size and place and a clean gate edge, 120 plain with the gate measured on
  the user's 6x6 reference, `spec/camera-gate.md`; the corrected geometry of
  `spec/contact-sheet-strips.md`) / **From the scan** (each frame's own scan
  around its crop, up to the film's edge where the scan shows it, else the
  generated border; "Camera's own" gate as on the contact sheet). Light leaks
  are not part of the collage until the contact sheet's draft is signed off.
- **Border and background**: colour (a swatch; white default), the **Gap**
  between frames and to the edge (px at the output size), background = the
  colour / a frame blurred / a frame sharp.
- **Shuffle** with **◀ ▶**: every layout is a function of (members, settings,
  seed). Shuffle draws a new seed and appends it to the history; ◀ / ▶ walk
  the history ("arrangement 3 of 5"). A setting change keeps the seed.
- **Export Collage…**: a folder and a name; JPEG, sRGB, quality 95, 4:4:4
  (`jpeg_params`), Instagram's screen sharpening per frame after its resize
  (`Canvas.sharpen` (25, 35)); one file per carousel post (`name_1.jpg` …
  `name_N.jpg`), otherwise one file. The Instagram export defaults, as the
  user asked; no other format in v1.

## Non-goals (v1)

- Saving collages with the catalogue (members, settings and seed are for the
  session; the settings themselves are remembered in `collage/*`).
- Free placement by hand (drag a frame to a cell is a later step).
- Text, stickers, polaroid frames.
- The synthesised film border's finer look (edge print, light leaks): WIP.

## Data model

```
CollageState: members [CCRImage refs], fmt (key, W, H), layout, style,
  posts, frames (triptych), direction, respect (bool), variance (0..30 %),
  borders ("none"|"generated"|"scan"), see_through (bool), colour (#rrggbb),
  gap (px), background ("colour"|"blur"|"sharp"), tear ("none"|"band"|"rows"),
  seeds [int], seed_at (index)
```

QSettings `collage/*` for every setting but members and seeds.

## Processing

- `core/collage.py`, Qt-free and pure: each layout takes the members' shown
  aspect ratios (after crop and turns; with film borders the border's outer
  shape), the canvas size, the settings and the seed, and returns **cells**:
  (member index, rect in output px, rotation, crop trim (x, y, w, h shares)
  for Respect-off variance). Seeded with `random.Random(seed)` only, so a
  seed always gives the same arrangement. Justified rows enumerate every
  contiguous split for ≤ 12 frames (greedy beyond) and score fit, row
  balance and lone rows; the seed picks among the near-best. The carousel
  seam rule and Aligned height are post-passes on the cells.
- Drawing (`widgets/collage_render.py`, QtGui): the same function draws the
  preview and the export, from different pixel sources:
  - **preview**: each member's displayed picture as the contact sheet takes
    it (`resized_preview` + crop + orientation via `contact_sheet.draw_frame`
    with `respect_crop=True`), at screen size; live as frames are edited
    (the sheet's per-frame signature: `_render_epoch`, crop, turns).
  - **export**: each member rendered through Export's own pipeline
    (`CCRBackend.export_image_by_index` to a temporary 16-bit sRGB TIFF, at
    the long side its cell needs, crop and turns included), read back and
    drawn into an output-size 16-bit buffer; so a frame in a collage is
    exactly what Export would write. On a worker thread (`WorkerThread`),
    with a progress dialog.
- Generated film borders and the see-through edge use one geometry module
  (the KS constants move from `widgets/contact_sheet.py` to
  `core/film_formats.py`, re-exported where they were).

## Integration

- `ImagePreview.collage_action` (picture, `src/icons/collage-icon.png`
  drawn by `tools/make_toolbar_icons.py`), placed after `sample_action`;
  `_balance_toolbar` keeps Film + Capture centred with the extra picture.
  Room: measured, the toolbar has 3 px to spare at the 900 px minimum in the
  stressed (Mac-wide) font, and the picture costs 31 px. Paid for by
  `ICON_BUTTON_PAD` 6 → 5 (−17 over the 17 pictures, still clear of each
  other) and the narrowest gaps at their minimum only (`AUTO_CROP_GAP_PX`
  12 → 8, `ZOOM_GAP_PX` 8 → 4, `CONVERT_GAP_PX` 8 → 4): −29, measured again
  by `tests/test_panel_width.py`. At ordinary widths the gaps are stretches
  and nothing looks tighter.
- `MainWindow.collage_view` (`widgets/collage_view.CollageView`) beside
  `contact_sheet`; showing one hides the other; `show_collage(on)` mirrors
  `show_contact_sheet`.
- `ThumbnailList.show_context_menu`: Add to Collage / Remove from Collage.

## Tests (`tests/test_collage.py`)

- Layouts: every cell inside the canvas, no two cells overlapping (except
  Tear's band), gaps ≥ the gap setting, Respect on keeps each shape to 0.5 %,
  off keeps the change ≤ the cap; the same seed gives the same cells; ◀ ▶
  walk the history; carousel seam rule (no frame crosses an edge by < 30 %);
  Aligned height: one height for all; Full height: every frame 95 % of the
  post's height (or the Height set), centred, no sliver under 12 % / 60 px on
  a post, the posts' count following the row; each post its own: no
  crossing.
- Film border geometry: 35 mm holes 1.98 × 2.79 mm, 2.01 from the edge.
- Members: opening takes the selection / everything; Add / Remove from the
  list's menu; the strip's Remove; reorder.
- Export: one file per post, 1080 × 1350 each for a 4:5 carousel, JPEG sRGB
  4:4:4 at 95; the frame's pixels match Export's own file for that frame at
  that size.
- Toolbar: Collage is the last picture on the left; Film + Capture still dead
  centre; the toolbar fits 900 px.

## Signed off (2026-11-20)

"Your mockups look good", with the Full height carousel added above. The
icon stays draft A (a grid) at the end of the left-hand pictures unless the
user picks another.
