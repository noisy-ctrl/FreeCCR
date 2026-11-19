# Capture on the main toolbar

The user, 2026-11-19: "Can we create a 'Capture' button to the left of Auto
Crop on the main screen. If already open, can the button unhide the capture
window."

- A **camera picture** button (tooltip "Capture"), just left of Auto Crop
  on the canvas toolbar (`ImagePreview.capture_action` → `open_capture` →
  `MainWindow.open_live_monitor`, the same as File → Capture…).
- Not open: opens Capture. Open but minimised: restored (`showNormal`).
  Open behind other windows or hidden: shown, raised and activated.
- A picture, not the word: the word was 60 px over the canvas's minimum
  width in a Mac-wide font (the user chose the picture). To fit the picture
  too, `TEXT_BUTTON_PAD` went 8 → 6 and `EXPORT_GAP_PX` 32 → 24
  (`tests/test_panel_width.py`).
- Icon: `src/icons/camera-icon.png`, a black glyph tinted by the theme like
  the rotate / flip icons (the `icons/` folder ships whole with builds).

## Tests

- The button sits immediately left of Auto Crop and has the camera icon.
- It calls `open_live_monitor`; a minimised monitor is restored, a hidden
  one shown and raised; with none open one is made.
- The toolbar still fits the smallest canvas.

## Revision 2026-11-19: in the middle

Capture now sits centred between the picture buttons and the words
(spec/toolbar-icons.md); the padding change above is kept.
