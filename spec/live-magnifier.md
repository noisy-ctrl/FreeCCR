# The camera's magnifier in the Live Capture Monitor

The user (2026-10-31) asked whether libgphoto2 can drive the GFX 50S II's
focus magnifier. Test results (`gfx_test_through_zoom.py`, his camera):

- The camera lists `/other/d01b` "ThroughImageZoom" (choices 1-12). Setting
  it magnifies the USB live view. SIFT on his frames: **2 = 2.49x, 4 = 3.99x,
  5 ≈ 8.3x**; 3 is the same as 2; 7-10 read back 6, 12 reads back 11, and
  6 / 11 give the same, softest picture (the camera's maximum, assumed 16x;
  too soft to match).
- It is a real sensor read-out, not an enlargement: against the whole-frame
  view enlarged by the same factor, fine detail (Laplacian variance) is 1.5x
  at 2.5x and 2.9x at 4x. At 8x one live view pixel is about one sensor pixel
  (8256 / 8 ≈ 1032 across a 1024 view): the level to focus at. Beyond it
  nothing is gained.
- It zooms on the camera's focus point (about 60 % across, 36 % down in his
  test), the same spot at every level; the joystick moves it while tethered.
  The setting is refused while live view runs, like every other setting.

He plans a stepper motor on the focusing rail later: the magnifier at 8x is
what an autofocus sweep would read.

## UX
- Capture's camera row: the existing camera-magnification combo
  (`cam_zoom_combo`, already used for Canon's `eoszoom`) lists **Whole, 2.5x,
  4x, 8x, Max** for a Fujifilm that has `d01b`.
- **Z** (monitor shortcut) steps to the next level, wrapping to Whole.
- The combo follows the camera: the 3 s readout also reads `d01b`, so the
  camera's own zoom button, or a shot that drops the zoom, shows.
- While magnified:
  - the canvas shows a badge "Magnified 8x · Z steps, Whole goes back";
  - things placed in whole-frame positions are hidden and inert: the frame
    guide (no dragging), the alignment line, the measured-patch outline;
  - Measure and the Negative Preview picks refuse with "Back to the whole
    frame first (Z or the magnifier list)": they record whole-frame positions;
  - the sharpness meter keeps working on what is shown; its best resets on
    every magnification change (a different picture scale).
- Back at Whole, the area last magnified is outlined for 5 s ("Last
  magnified here"), so you see which part of the negative you were judging:
  found by matching the magnified picture against the last whole-frame one
  (`core/zoom_spot.locate`, SIFT + RANSAC on a background thread; accepted
  only with ≥ 12 inliers and a scale within 30 % of the level's).

## Backend
`Gphoto2Backend`:
- `FUJI_ZOOM_KEY = "d01b"`, `FUJI_ZOOM = (("1", "1"), ("2.5", "2"), ("4",
  "4"), ("8", "5"), ("16", "6"))` (label, value).
- `zoom_levels()`: on a Fujifilm (the same "fuji" test as `_capture_call`)
  reads `d01b` ONCE as a single setting (never the whole configuration, ~150
  properties on a Fujifilm); else the Canon path as before.
- `set_zoom(label)`: sets the value directly; if refused while live view was
  asked for, `_reconnect()` (as `set_exposure`) and again. Returns the label
  the camera reads back (or None).
- `zoom_now()`: the label for the camera's current `d01b` (values 2-3 → 2.5,
  6 and up → 16), None when it has none.
`UsbCamera`: `zoom_changed = Signal(str)`; `set_zoom` is latest-wins
(`_want_zoom`) and applied only when no shot is pending, like exposure; the
3 s readout emits `zoom_changed` when `zoom_now()` changes.

## Tests
Fake gphoto2 camera with `d01b`: levels listed only on a Fujifilm; a set
while live view runs reconnects once and reads back; `zoom_now` maps 3 →
2.5 and 9 → 16; worker emits `zoom_changed` after a set and when the camera
changes it by itself; monitor: Z steps the combo, badge + guide hidden +
Measure refused while magnified, best reset; `zoom_spot.locate` on a
synthetic texture finds a known centre and scale and rejects a mismatch.
