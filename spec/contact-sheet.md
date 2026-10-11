# Contact sheet: every frame and its crop, live

## Goal

See the whole roll at once with each frame's crop marked, to check auto crop
(and your own crops) at a glance and jump to the ones that need fixing. It
pairs with Auto Crop All and learning (spec/auto-crop-learning.md): Auto Crop
All, look over the sheet, fix the odd frame, Auto Crop All again.

## Non-goals

- Not a file in your folder. It is a live view; **Save as image…** writes a
  JPEG only when asked.
- Not an editor: crops are adjusted in the Crop panel (double-click a tile).
- Not part of the image list: it never appears in exports, the catalog or
  Convert All.

## UX

- **Entry**: a "Contact sheet" button at the top of the thumbnail column,
  above the first frame: grid icon, dashed border (no other thumbnail has
  one), highlighted while the sheet is showing. Clicking it toggles the sheet.
  Disabled with a hint while cropping or removing dust (finish those first).
- **The sheet** replaces the main image view (the side panels stay): a grid of
  every frame, **uncropped**, with its crop drawn as a box (rotated when
  straightened), the outside dimmed. Box colour shows where the crop came
  from:
  - green: confirmed by you (Done in the Crop panel),
  - amber: set by auto crop, not yet confirmed,
  - blue: drawn by hand,
  - no box: no crop; a red dashed outline when auto crop looked at the frame
    and wasn't sure (the caption says why).
- Caption under each tile: number, file name, and the source (with auto
  crop's confidence).
- Frames show as they currently are: the negative before converting, the
  positive after, in the frame's orientation (rotation / flips) like the
  thumbnails. **Negatives** (a switch in the sheet's bar) always shows the
  scans; a converted frame's scan is re-read in the background (a 0.4 s
  half-size decode, once per frame per session) and shown when ready.
- **Size**: Small / Medium / Large tiles (persisted, `sheet/size`); the grid
  reflows with the window.
- **Click** a tile: back to the single view on that frame. **Double-click**:
  that frame in the Crop panel. **Right-click**: Open, Open in Crop panel,
  Auto Crop this frame. **Esc** or the entry button: back to the frame you
  were on. Picking a thumbnail also leaves the sheet.
- **Live**: while showing, the sheet checks every 0.4 s whether any frame's
  crop, angle, orientation, render or source changed (a tuple compare per
  frame, no pixels) and redraws only those tiles. So a crop, an Auto Crop
  (All), an undo, a slider move on the current frame or a conversion shows
  up without doing anything.
- **Save as image…**: the sheet at its current mode, 5 across (fewer for a
  short roll), 480 px tiles, captions and a legend, dark background, JPEG
  quality 92; default name `contact_sheet.jpg` in the first frame's folder.

## Data

- Tiles come from `img.resized_preview` (the uncropped 1080 px render every
  frame already has; the crop is only applied on screen) scaled to the tile:
  nothing is rendered for the sheet.
- Where a crop came from is tracked on the image: `auto_crop_rect` (the box
  auto crop last set) and `auto_crop_example` (set when Done taught it the
  frame). Both are now saved in the catalog (additive keys, absent = unknown)
  so the colours and Auto Crop All's "leave your crops alone" survive a
  restart.

## Integration

- `widgets/contact_sheet.py`: `crop_source(img)`, `compose_tile(...)`,
  `ContactSheetView` (bar + scrollable `SheetCanvas`), `render_sheet_image`.
- `MainWindow.contact_sheet` is a SIBLING of `image_preview` in the central
  layout (not a stack around it), shown in its place, so ImagePreview's
  `parent().parent()` chain stays valid. `show_contact_sheet(on)`,
  `open_frame_from_sheet(idx, crop=False)`.
- `ThumbnailList.sheet_btn`; selecting a thumbnail leaves the sheet.
- Catalog: `auto_crop_rect`, `auto_crop_example`.

## Tests

- `crop_source` for each case; a tile's box lands where the crop is, in the
  source colour, rotated and through a 90° orientation.
- The grid reflows with width; a click maps to the right frame.
- Live: changing a crop changes that tile's signature and only it.
- Negatives mode uses the cached scan for a converted frame.
- Save as image writes a JPEG of the expected size.
- Main window: the entry toggles the sheet, a thumbnail pick leaves it, a
  double-click opens the Crop panel; the entry is refused in crop mode.
- Catalog round trip of the two provenance keys.
