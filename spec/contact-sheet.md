# Contact sheet: the whole roll as one image, live

## Goal

A contact sheet in the lab sense: ONE image of the roll, every frame
uncropped and in order, with each crop overlaid, sitting at the top of the
thumbnail column like another frame. It checks auto crop (and your own crops)
at a glance, and pairs with Auto Crop All and learning
(spec/auto-crop-learning.md): Auto Crop All, look over the sheet, fix the odd
frame, Auto Crop All again.

(The first version was a scrolling gallery of tiles. It was replaced: the
point is one picture of the roll, zoomable like a frame and exportable as it
is.)

## Non-goals

- Not a file in your folder: the image lives in memory and is redrawn as
  things change. **Export…** writes it when asked.
- Not a scan: never in the image list, Convert All, Auto Crop All, the
  catalog, or the frame export; the per-frame controls are greyed while it is
  showing.
- Not an editor: crops are changed in the Crop panel.

## The image

- Frames in roll order on near-black, in rows cut like the film's strips:
  6 for 35mm, 4 for 6x4.5, 3 for 6x6 / 6x7, 2 for 6x9 (the roll's most common
  format; see `spec/film-formats.md`). With nothing to go on, 6 for more than
  12 frames, else 4.
- Each frame **uncropped and as scanned** (no rotation or flips: as on the
  film, which also keeps every cell the same shape), fitted in a cell of the
  scans' shape, 480 px wide (360 px for rolls over 48 frames).
- **Crop overlay**: outside the crop dimmed, and a line round it (rotated when
  straightened), coloured by where the crop came from:
  green confirmed by you, amber auto crop, blue drawn by hand; no crop, no
  line; a red dashed outline round a frame auto crop looked at and wasn't sure
  of.
- **Edge print** under each frame: its number in a warm edge-print tone, the
  file name, its film format ("120: 6x4.5") and the source (with auto crop's
  confidence, or why it wasn't sure). Lettering scales with the cell so it
  reads with the sheet fitted.
- A footer: frame count, the roll's film formats, date, and the colour key.
- Frames show as they currently are (negative before converting, positive
  after). **Negatives** shows every frame as its scan: an unconverted frame's
  preview is remembered when seen; a converted frame's scan is otherwise read
  in the background (a 0.4–0.5 s half-size decode, once per session).

## Live

`ContactSheet.update()` runs on a timer (every 0.4 s while showing, 1.5 s
otherwise, for the thumbnail). Per frame it compares a signature (render
epoch, crop, angle, source, name, Negatives state; no pixels) and redraws
only the changed cells into the composite; a different frame count or layout
rebuilds it. So a crop, Auto Crop (All), an undo, a slider move on a frame or
a conversion shows up by itself.

## UX

- **Entry**: the sheet's thumbnail pinned at the top of the thumbnail column
  (not a list item, so rows stay == image indices), labelled "Contact sheet",
  with a dashed edge-print-amber border (solid when showing), sized to the
  sheet's shape. Hidden with no frames. Click toggles the sheet. Refused (with
  a hint) while cropping or removing dust.
- **Showing** replaces the main image view (side panels stay). A bar: title,
  "Double-click a frame to open it", **Negatives**, **Fit**, **100%**,
  **Export…**, **Back to frame**. Wheel zooms (to 4x), drag pans; fitted on
  open and on resize until you zoom.
- **Double-click** a frame: open it. **Right-click**: Open frame n, Open in
  Crop panel, Auto Crop this frame, Export contact sheet…
- **Esc**, Back to frame, the entry, or picking a thumbnail: back to the
  single view (the frame you pick, or the one you were on), its controls
  restored.
- While showing, the sliders, Convert Current and Auto Crop (this frame) are
  greyed: the sheet is not a frame. **Convert All** and **Auto Crop All** stay
  available and their results appear on the sheet as they land.
- **Export…**: JPEG (quality 92) or TIFF, default `contact_sheet.jpg` next to
  the first frame. With Negatives on, scans still being read are read first.

## Data

- Cells come from `img.resized_preview` (the uncropped 1080 px render every
  frame has); nothing is rendered for the sheet.
- Crop provenance on the image: `auto_crop_rect` (the box auto crop last set)
  and `auto_crop_example` (set when Done taught it the frame), saved in the
  catalog (additive keys; absent = unknown, shown as your own crop).

## Integration

- `widgets/contact_sheet.py`: `crop_source`, `crop_polygon`, `draw_frame`,
  `film_columns`, `SheetLayout`, `ContactSheet` (model, `changed` signal,
  `update`, `frame_at`, `thumbnail`, `save`), `ContactSheetView`.
- `MainWindow.contact_sheet_model` / `contact_sheet` (a SIBLING of
  `image_preview` in the central layout, shown in its place, so
  ImagePreview's `parent().parent()` chain stays valid), `_sheet_timer`,
  `show_contact_sheet(on)`, `open_frame_from_sheet(idx, crop)`.
- `ThumbnailList.sheet_btn`, `set_sheet_icon`; picking a thumbnail leaves the
  sheet.

## Tests

- `crop_source` for each case; the overlay line on the crop in the source
  colour, the outside dimmed, the rotation; the dashed outline.
- 6 across for 35mm, 4 for 645; frames in order; `frame_at`; the overlay
  lands in the composite.
- Live: only the changed frame is redrawn; nothing when nothing changed.
- Thumbnail; an empty roll clears it.
- Negatives from the remembered scan and from a background read.
- Export to JPEG and TIFF at the composite's size.
- Main window: the entry appears with frames, toggles the sheet, greys the
  per-frame controls and restores them, double-click opens a frame, refused
  while cropping.
- Catalog round trip of the provenance keys, and an old record without them.

## Overlay checkbox (2026-10-23)

An **Overlay** checkbox sits right after Negatives in the sheet's bar (on by
default, remembered as `sheet/overlay`). Off draws every frame as it is: no
dimmed outside, no crop outline, no dashed "unsure" box (`draw_frame(...,
overlay=False)`); the captions under each frame keep the number, name and
format. Revision 2026-10-23: the crop status ("Confirmed", "Auto crop", ...)
and the footer's colour legend go with the overlay too: they name the
outline's colours, so with no outline they only cluttered the sheet. It is part of each frame's
signature, so toggling redraws every cell, and Export writes what is shown.

## Revision 2026-11-19: the right way round, Respect crop, text, Back to frame

The user: "In the contact sheet, can we make sure the frames are the way
round of the crop in the main window. Can we have a 'respect crop' box that
crops thumbnails. Can we have the option to change text colour and info. Can
the back to frame button stand out more."

- **Turned as in the main window.** Every frame is drawn with its quarter
  turns, fine rotation and mirrors (`rotation_angle`, `fine_rotation_angle`,
  `horizontal_mirrored`, `vertical_mirrored`), in the canvas's order: the
  turns about the centre, the mirrors last, in screen space
  (`ccr_processor.apply_orientation`). The crop overlay turns with it. This
  replaces "as scanned": a roll scanned sideways and turned now reads
  upright. Cells take the median shape of the frames as shown, so turned
  frames get upright cells.
- **Respect crop** (a box after Overlay, `sheet/respect_crop`, default off):
  each cropped frame shows only what its crop keeps (the crop box, its own
  angle, then the turns and mirrors: what an export gives); a frame with no
  crop shows whole. Overlay then draws a thin line round the frame in its
  crop's colour (there is nothing outside to dim); "not sure" keeps its
  dashed box.
- **Text…** (a button with a menu in the bar): **Colour** — Edge print (the
  old look: warm numbers, light grey names), White, Light grey, Mid grey,
  Warm (everything in the edge-print tone), Custom… (a colour picker);
  **Show** — Frame number, File name, Film (the frame's film preset, else the
  roll's film), Film format, Crop status. Remembered as `sheet/text_colour`
  ("edge" | "white" | "light" | "mid" | "warm" | "#rrggbb") and
  `sheet/text_info` (the shown keys). The colour is used for every caption
  and the footer's words; the crop status keeps its source colours (it names
  the outline's colour) and still shows only with Overlay on. Line 1 is the
  number and name, line 2 film · format · status, each only when ticked.
- **Back to Frame** stands out: a light button with dark, bold text (the
  `inverse` button role in `ui/theme.py`), the only light thing in the bar.
- The frame signature gains the orientation, Respect crop, the text colour,
  the shown info and the film name, so any of them redraws the cell.

### Tests

- A frame turned 90° is drawn upright: its scan's left edge ends up at the
  cell's top; a mirrored frame is mirrored; the crop overlay follows the
  turn.
- Respect crop shows only the crop's content, turned; a frame with no crop
  whole.
- The cell shape follows the turned frames.
- The text colour and each info switch change the caption (and are
  remembered); custom colours are kept as #rrggbb.
- Back to Frame has the inverse role.

## Revision 2026-11-20: Text… stays open while you tick

The user: "In this contact sheet menu, can we have little check boxes. At
the moment, you need to reopen the menu after every click." Text…'s menu
now holds real radio buttons (Colour) and check boxes (Show) in its rows
(`QWidgetAction`, `ContactSheetView._menu_row`), so a click changes the
sheet and the menu stays open; click away when done. The whole row takes
the click (`_RowCheck` / `_RowRadio.hitButton`, `_MenuRow`): a click a
button ignored used to reach the menu, which closed. Custom… is still an
ordinary item (it opens the colour picker). Each row's action keeps its
label as text and its button as `action.box` (the tests use them).
