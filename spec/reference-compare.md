# Compare with a reference picture

The user, 2026-11-25: "to properly compare these it would be good to have a
feature where you could compare a frame from the software to a reference
frame (jpg, already converted etc). Maybe another tool for the toolbar...
Right click and you can change the line to vertical/horizontal etc. be able
to click and drag the line or right click and lock."

The reference is usually a lab's scan of the same negative (his archive:
Noritsu TIFFs and JPEGs), but any finished picture will do. Then, mid-way
through the spec: "Could we have the option to choose a file from finder or
another image in opened catalogue too" — so a reference is either a FILE or
another FRAME open in the session (another scan of the same negative, a
version with other settings, a lab JPEG opened as a frame).

## Goals

- Show the frame on screen and a reference picture of the same frame side by
  side across one line, the reference LINED UP with the frame (its own crop,
  turn, mirror and size found automatically), so the same spot is compared
  at the line.
- Drag the line; right-click it to make it vertical or horizontal, swap
  sides, centre it or lock it.
- Show the reference in its true colours: its embedded colour profile is
  converted to the display's working space (sRGB) as the frame's preview is,
  so the comparison is fair.
- A display layer like False colour and the HDR dots: never in the picture's
  pixels, so scopes, probe, histogram, thumbnails and exports never see it.

## Non-goals (this version)

- Finding each frame's lab scan by itself in a folder (a "reference folder"
  matched by picture, as was done by hand for the Noritsu study). Possible
  later; the matching code would be this tool's.
- Scopes / histogram / probe reading the reference side.
- Any change to the frame's settings from the reference (that is what the
  fitters in tools/ are for).
- Side-by-side panes, a diagonal line, a wipe animation.

## UX

### The toolbar picture

**Reference** (`compare_action`), a checkable picture among the display
tools, after False colour: a frame cut by an upright line, the right half
shaded (draft A, the user's pick, 2026-11-25; `src/icons/reference-icon.png`
drawn by `tools/make_toolbar_icons.reference`). Tooltip: "Reference:
compare this frame with a finished picture of it (a lab scan, a JPEG you
made elsewhere), lined up with it, either side of a line you can drag.
Right-click the line or this button to turn it horizontal, swap sides or
lock it. Display only."

- Turning it on for a frame with no reference asks where it comes from
  (`_ask_reference()`, the seam): **Choose a File…** (an open-file dialog:
  JPEG, TIFF, PNG; starts in the folder last used, `view/compare_dir`),
  **Choose an Open Frame…** (a small window listing the session's other
  frames as thumbnails with their names, `widgets/reference_picker.py`;
  double-click or OK) or Cancel, which turns it off again.
- The frame list's right-click gains **Compare the Frame on Screen with
  This** on any frame but the one on screen (turns Reference on).
- A frame used as the reference is shown AS IT IS SHOWN in the main window:
  its own crop, turn, mirror and grade (`contact_sheet.draw_frame(...,
  respect_crop=True, orient=orientation_of(img))` of its `resized_preview`),
  and it follows edits to it (its `_render_epoch` is in the signature).
- It stays on across frames. A frame with no reference shows the frame
  alone and a hint over the canvas: "No reference for this frame: right-click
  Reference to choose one." A frame whose reference file has gone: "The
  reference … isn't there any more."
- Off: the layer goes; nothing else changes. Esc doesn't turn it off (it is
  a view, like False colour).

**Room on the toolbar**: the toolbar fit its 900 px minimum with 4 px to
spare at the Mac-wide font, and a picture needs 27. The user, at sign-off:
"Hide collage in the top menu for now" — Collage left the toolbar and is
the File menu's **Collage** (after Export…), the same checkable
`collage_action` (icon hidden in the menu), so the toolbar's width is
unchanged.

### The line

- In the VIEW, not the picture: a fraction of the canvas's visible width
  (upright line) or height (level line), so it never leaves the screen when
  zoomed or panned; the picture moves under it. Default upright, at the
  middle, the frame on the left (top) and the reference on the right
  (bottom).
- Drawn 1 device pixel white over a 3 px dark outline across the picture
  only, with a round knob (14 px) at its middle and two small tags at its
  top (left) end: "Frame" and "Reference", each on its own side, in the
  canvas's caption style. Tags and knob keep their size at any zoom.
- Drag: the pointer within 6 px of the line (or on the knob) shows the split
  cursor; a press there drags the line (the picture doesn't pan). Anywhere
  else the mouse does what it does now (pan, crop, dust, sample circle...).
  A drag clamps to the picture's visible extent.
- **Lock Line**: the line can't be dragged and the knob shows a small
  padlock; the cursor no longer changes near it. Right-click still works.
- Where the reference has no picture (a lab scan cropped tighter than the
  frame), its side is a flat dark grey (#3a3a3a), so a gap can't be mistaken
  for the frame.

### Right-click (on the line or knob, or on the toolbar button)

```
Upright Line            (radio)
Level Line              (radio)
Swap Sides
Centre the Line
Lock Line               (check)
---------------
Line Up with the Frame  (check, default on)
Line Up Again
---------------
Choose a File…
Choose an Open Frame…
Show the Reference File      (a file reference only)
Remove Reference
```

- Off "Line Up with the Frame" shows the whole reference fitted to the
  picture on screen, upright, aspect kept, centred: for a picture that isn't
  of this frame, or when lining up fails.
- "Line Up Again" re-runs the match (after the crop moved a lot, say).
- "Show the Reference File" reveals it in the Finder / Explorer.
- A frame reference that is closed (or not open this session) gives the
  hint "The reference frame … isn't open"; the reference is kept.
- "Remove Reference" forgets the file for this frame (the file is never
  touched).
- Right-clicking the picture away from the line keeps whatever the active
  tool does there (dust erase, skin remove...).

### Messages

A line under the tags while it works / when it couldn't: "Lining up…",
"Couldn't line it up: shown whole (right-click → Line Up Again)". On success
nothing is shown.

## Data model

- `img.compare_ref`: None, `{"file": absolute path}` or `{"frame":
  [catalogue key, display name]}` (the other frame's identity in the
  catalogue, `catalog._catalog_key_for_image` + `display_name`). Catalogue
  key `compare_ref` (additive; written only when set). Resolved to an open
  image by `CCRBackend.find_image(key, name)`.
- Session only, on `ImagePreview`: on/off (`_compare_on`), line orientation
  (`view/compare_line` "upright"|"level" in QSettings), fraction (0.5 at
  start), sides swapped (`view/compare_swap`), locked (session), line up on
  (`view/compare_lineup`, default true).
- Caches (memory only): the loaded reference per (path, mtime, size) or
  (frame, its signature), two entries; the line-up per (reference signature,
  geometry signature), eight entries. A frame reference's signature =
  (its id, `_render_epoch`, crop, angle, orientation).

## Processing

`core/reference_compare.py`, Qt-free.

### Reading

`load_reference(path, max_side=3000) -> Reference(rgb uint8 HxWx3 sRGB,
full_size, profile_name, bits)`:

- TIFF via tifffile (8 or 16 bit, RGB or grey, planar or not; alpha
  dropped; the ICC profile from tag 34675; the orientation tag applied),
  everything else via OpenCV from a buffer (any file name works on Windows;
  a JPEG's EXIF orientation is applied by OpenCV), the ICC profile parsed
  from the JPEG's APP2 segments or the PNG's iCCP chunk. No Pillow (it isn't
  one of the app's requirements).
- Reduced to `max_side` with INTER_AREA in float32 before any rounding
  (16-bit data stays 16-bit through the reduction).
- Colour: no profile, or an sRGB one = unchanged. Otherwise Little-CMS
  (imagecodecs' `cms_transform`, already a requirement) converts the
  profile to sRGB, relative colorimetric, in float32 and unbounded, then
  it is clipped and rounded to 8 bits once (the display is 8-bit sRGB on
  both sides). Without Little-CMS a matrix-shaper profile (sRGB, Adobe RGB,
  Display P3, ProPhoto: what labs and editors write) is done by hand the
  same way; a profile that can't be applied leaves the values as sRGB and
  `Reference.converted` False (the hint says so).
- Grey files become three equal channels.

### Lining up

`line_up(ref_rgb, shown_rgb) -> LineUp(H, mirrored, inliers, error_px) | None`
maps reference pixels to the shown picture's pixels (both at their full
given sizes):

- Both to Rec.709 luma (8-bit display values; a frame still shown as a
  negative, not converted and not in Positive mode, is inverted first), each reduced to 1200 px on its
  long side, then CLAHE (clip 2, 8×8) so a darker or flatter rendering of the
  same scene still matches. SIFT (4000 features), ratio test 0.75, RANSAC
  homography (3 px at 1200 px, 5000 iterations).
- Turns of any angle come out of SIFT; a mirror does not, so the reference is
  matched as it is and mirrored left-right, and the one with more inliers is
  kept (a lab scan of the emulsion side, a carrier the other way round).
- Accepted when ≥ 25 inliers, ≥ 25 % of the ratio-test matches are inliers,
  the median reprojection error ≤ 2.5 px at 1200 px, and the homography is
  close to a similarity: the reference's corners land as a convex quad whose
  two scales differ by < 25 % and whose corner angles are 90° ± 10°. A
  homography that fails the shape test is retried as a partial affine
  (`estimateAffinePartial2D`), accepted on the same counts.
- The result is stored normalised (reference fractions → shown fractions), so
  the hi-res pixmap, which has the same geometry at another size, reuses it.

The match runs on a Python thread (no QThread: nothing in it uses OpenBLAS),
one at a time, newest request wins; the answer is applied only if the frame,
reference and geometry are still the ones asked about.

**What it is matched against**: the frame's preview pixmap as displayed
(`current_pixmap`: cropped, not yet turned or mirrored — orientation is the
picture item's transform). Geometry signature = (image id, crop_rect,
crop_angle, slice) + the reference's signature. A frame reference is
re-matched only when ITS geometry changes, not its grade (its pixels are
refreshed, the stored line-up reused). Turning or mirroring the frame
needs no new match: the layer is a child of the picture item and turns with
it. Grading never needs a new match.

## Display

- `_ref_clip`: a `QGraphicsPathItem` child of `pixmap_item`, no pen or brush,
  `ItemClipsChildrenToShape`, z 3: above every display aid (False colour 1,
  flat map 1.4, tool masks 1.5, HDR dots 2, clip overlay 2.5), which
  describe the frame and so stay on the frame's side only; its path = the reference's side of the line, as a viewport
  half-rectangle mapped to scene, then into the item's coordinates.
- Children of `_ref_clip`: a `QGraphicsRectItem` filling the picture's rect
  with #3a3a3a (the no-reference grey), and `_ref_item`, a
  `QGraphicsPixmapItem` of the reference (SmoothTransformation), its
  transform = reference pixels → shown pixmap pixels (the stored normalised
  homography scaled to the current pixmap's size), as a `QTransform` (Qt's
  (m11 m12 m13 / m21 m22 m23 / m31 m32 m33) = H transposed).
- Fitted (not lined up): the reference rect fitted upright in the picture's
  scene bounding rect, mapped into item coordinates through the inverse of
  the item's scene transform.
- The line, knob and tags are drawn in the view's foreground
  (`ImageView.drawForeground` in viewport coordinates), so they cost nothing
  to move and are never in any scene render.
- Refreshed at the end of `apply_transformations` (every pixmap swap,
  turn, mirror, crop) and on the view's zoom/scroll changes (the clip
  path only).
- The scopes, histogram and probe read the picture item's own pixmap
  (`_capture_display_image`), so they never see it; a test pins this.

## Integration

- `widgets/compare_layer.CompareLayer`, owned by the canvas as
  `ImagePreview.compare`: `set_on`, `refresh` (end of
  `apply_transformations`), `update_clip` (zoom, fit, resize, the scroll
  bars), `reset_items` (where the scene's other layers are reset),
  `paint` (the view's `drawForeground`), `hit` / `press` / `drag_to` /
  `release`, `build_menu` / `show_menu`, `use_frame`, `remove_reference`.
  Seams: `ask_source`, `ask_file`, `ask_frame`; `store` (QSettings-like,
  a dict stand-in in tests); `threaded` (False in tests).
- `ImagePreview.compare_action`, `set_compare(on)`.
- `GraphicsImageView`: when no editing tool (dust, crop, area, slice, the
  white-balance pick, the black/white point pick) has the mouse
  (`_compare_free`), a press on the line or knob drags it (left) or opens
  the menu (right), before the Skin, sample circle and reference-frame
  handling; hovering it shows the split cursor.
- `widgets/reference_picker.py`: Choose an Open Frame….
- Catalogue: `compare_ref` saved and restored per frame.
- Frame switch: the layer follows the new frame (its reference or the hint).
- CLAUDE.md gets a paragraph.

## Tests (`tests/test_reference_compare.py`)

Core:
- A frame reference renders as the main window shows it (cropped, turned)
  and a grade change refreshes it without a new match.
- `load_reference` on an 8-bit JPEG with no profile = the pixels; on a 16-bit
  TIFF = the reduction of the 16-bit data to 8 bits (±1); on a TIFF tagged
  with a wide-gamut profile (Pillow builds one): a saturated red comes out
  as littleCMS's sRGB red, and a grey stays grey (±1); EXIF orientation is
  applied; grey files give three equal channels.
- `line_up` on a synthetic textured picture against a version of it cropped,
  turned 7°, scaled 1.6× and mirrored, re-graded darker: found, mirrored,
  corner error < 1.5 % of the size; against an unrelated picture: None.
- The QTransform built from H maps points as H does (to 1e-6).

UI (offscreen):
- Turning on with no reference asks (seam); cancel turns it off.
- With a reference: the clip item exists as a child of the picture item, its
  path covers the reference side; Upright ↔ Level and Swap change the path;
  a drag of the line moves the fraction; a locked line doesn't move.
- `_capture_display_image` is identical with the layer on and off.
- `compare_ref` round-trips through the catalogue, both kinds.
- Choose an Open Frame… (seam) and the frame list's menu entry set a frame
  reference; the entry isn't offered on the frame on screen.
- The toolbar still fits (`tests/test_panel_width.py`).

## Refinement (before building)

- Display aids and the reference: the reference's side shows the reference
  only (z 3 over every aid), so False colour, the HDR dots, the flat field
  map, the tool masks and the clipping overlay read as the frame's, on its
  side. Settled rather than giving each aid its own clip.
- An unconverted negative is matched inverted (above), so the tool works
  before Convert too, though comparing then is of little use.
- Esc is left alone (above).
- The tags say "Frame" and "Reference" (no product names).
- No Pillow: the app doesn't require it, so the reading is tifffile +
  OpenCV + imagecodecs' Little-CMS (measured: a ProPhoto grey of 128 comes
  out 146 in sRGB, as Little-CMS's own 8-bit path gives).
- Measured on three real frames (yours against the lab's rendering, the lab
  side turned 90°, mirrored, scaled 1.3× and cropped): found every time,
  910–1515 inliers, 0.4–0.5 px error, centre within 0.15 % of the picture,
  0.85–0.92 s each here.
- Built: the pixels of the shown picture are COPIED out of their QImage
  (`compare_layer._rgb_of`): with no row padding, numpy's slice was already
  contiguous and `ascontiguousarray` returned a view into memory Qt freed,
  which matched garbage (found by the canvas test).
- The clip path is the reference's side ∩ the picture's rect, so a
  reference never shows past the picture's edges.
