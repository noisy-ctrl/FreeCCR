# Right-click Crop → Reset Crop

The user (2026-10-31): "Can you be able to right click crop and click reset."

## UX
- Right-click (or press and hold is not needed: a plain context menu) on the
  panel's **Crop** button opens a menu:
  - **Reset Crop** — the frame on screen goes back to no crop.
  - **Reset Crop on the N Selected Frames** — shown only when more than one
    thumbnail is selected.
  Both are disabled when nothing they'd act on has a crop.
- In crop mode the canvas's own right-click still clears the crop, as before.

## What reset does (`ImagePreview.reset_crops(indices)`)
Per frame with a crop (`crop_rect` set or `crop_angle` non-zero): one undo
snapshot (`push_undo_state`), then `crop_rect = None`, `crop_angle = 0.0`,
`auto_crop_rect = None`, `auto_crop_example = None` — so a later Auto Crop /
Auto Crop All / crop-after-convert may crop it again (a Done-confirmed crop
was otherwise never touched again, `CCRBackend.is_crop_candidate`). Learned
crops in the crop memory are left alone. If crop mode is open it is left
first (`cancel_crop_mode`). Changed frames re-render (the histogram is the
crop's), the preview and thumbnails refresh, the catalogue is saved, and the
hint says "Crop reset" / "Crop reset on N frames".

## Tests
Reset on the current frame clears the four fields and undo brings the crop
back; a frame without a crop is untouched (no undo entry); the selection
entry resets each selected cropped frame; a Done-confirmed frame becomes an
auto-crop candidate again; the menu shows the selection entry only with
more than one selected.
