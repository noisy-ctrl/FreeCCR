# Spec: Live Capture Monitor

Status: DRAFT v1 for review (2026-10-24). No code yet.
Owner: FreeCCR
Feature branch: `feature/live-capture-monitor`

## 1. Summary

**File → Live Capture Monitor…** opens a window for camera scanning: the
camera's live HDMI output (through a USB capture device) for framing and
focusing, control of a USB-serial LED backlight (Red, Green, Blue, White,
Infrared) and of the camera shutter it can trigger, and the three-exposure
R → G → B capture that FreeCCR's 3-way merge is made for. It is built in
FreeCCR's own look and conventions: the theme tokens, the canvas toolbar,
slider rows, sections and right-click options popups.

The behaviour reference is the scan page of a separate capture app of the
user's. That app is known not to work perfectly, so it is a list of features,
not code to copy: every behaviour here is re-derived, tested on its own, and
§10 lists faults seen in its code and how this avoids each. Nothing of its
combine page is included, now or later.

## 2. Goals / Non-goals

### Goals
- A separate, non-modal window, opened from the File menu, that can sit on a
  second screen beside the main window.
- A smooth, sharp live view at the feed's own rate, with framing aids (frame
  guide, alignment line), focusing aids (noise reduction, edge amplify,
  peaking, a sharpness meter) and exposure aids (FreeCCR's histogram, clip
  corners and false colour, on the live feed).
- A rough positive of the white-lit negative for judging the frame.
- Light control: connect, the five LED channels, presets, solo keys, idle
  dimming, telemetry.
- Shutter control: single shots and the trichrome sequence, stoppable, with
  timing that can't silently mix colours.
- Works without the light (view only) and without a camera (light only), and
  without the serial package installed (the light section says what's
  missing).

### Non-goals
- Anything from the reference's combine page: merging, alignment, results,
  export, history, and the "last scan" readout (its alignment, film base and
  exposure-advice lines are all combine results).
- Recording the feed or saving stills from it. The camera takes the pictures;
  the feed is for looking.
- Any change to how FreeCCR loads, merges or converts. The live preview's
  numbers are never handed to the main window's conversion (§6.4).
- Detecting other tether apps by process name (the reference does; it only
  proves an app is open).
- Naming the light, its maker or any other software, in the app or the repo.

## 3. Window and layout

A static mockup was shared with this draft (rendered with FreeCCR's own
theme and widgets; the picture in it is synthetic).

FreeCCR's UI is neutral grey so it never biases colour judgement. The
reference's orange accent (checked buttons, the frame guide) and blue
switches are not carried over: checked toggles look like the main toolbar's,
switches are FreeCCR checkboxes, and colour appears only where it means
something (the channel sliders and rings, the overlays: frame guide white
over a dark halo with faint thirds, alignment line cyan).

```
┌ toolbar: Reduce noise  Edge amplify  Peaking  Sharpness  False colour ──┬ side panel ┐
│          Frame guide  Alignment | Negative              [Fit ▾]  Panel │ histogram  │
│                                                                        │ Light      │
│                          live view                                     │ LED        │
│                                                                        │ Presets    │
│  [sharpness readout]                                                   │ Capture    │
├ status: Camera [Camera 1 · 1920×1080 ▾] Find Cameras  59.9 fps · …    │ + Negative │
│                               Tethering: off  [Start Tethering…]      │   Preview  │
└────────────────────────────────────────────────────────────────────────┴────────────┘
```

### 3.1 Opening and closing
- File menu: **Live Capture Monitor…**, after **Tethering…**. One window; the
  action raises it if it is already open. Geometry remembered
  (`live/geometry`).
- The window owns the camera and the serial port while open. Both are
  exclusive: another program can't use them meanwhile. Closing the window
  stops the feed and closes the port; quitting FreeCCR closes the window.
- Closing leaves the light as it was last set (open question 3).

### 3.2 Toolbar (top of the canvas, like the main canvas's)
Text toggles, checkable, styled as the main toolbar's False colour / HDR.
Right-click any of them for its options popup (the Glow / Shadow Lift popup
pattern: slider rows, value labels, Reset).

| Toggle | Default | Right-click options |
|---|---|---|
| Reduce noise | on | Strength |
| Edge amplify | off | Amount, Radius |
| Peaking | off | Threshold, Intensity, Width (open question 4) |
| Sharpness | off | Draw Area…, Whole Frame, Reset Best |
| False colour | off | none (exposure bands only, §5.8) |
| Frame guide | off | Format (FreeCCR's film formats), Free, Size / Position / Rotation nudges, Reset |
| Alignment | off | Place Line… (then click the picture) |
| Negative | off | none (its controls are in the panel) |

Then a stretch, the **zoom** combo (Fit, Fit Guide, 1:1, 2:1) and **Panel**
(hides the side panel for a full-width view; remembered).

**Negative** is disabled, not hidden, while White is at 0, with the tooltip
"Light the White LED to preview the positive". Hiding it (the reference)
made the toolbar jump about.

### 3.3 Canvas
- `theme.CANVAS` behind the picture; messages centred in the canvas text
  colour ("Looking for a camera…", "No camera found" with what to check,
  "Camera access was refused" with where to allow it).
- Zoom: the combo, the wheel about the pointer, drag to pan, double-click
  toggles Fit / 1:1, as on the main canvas. **Fit Guide** zooms so the frame
  guide fills the view (the reference opened at a fixed 1.6× centre crop for
  the same purpose).
- The picture is drawn at the canvas's device pixels (§5.9).
- Readouts sit bottom-left, styled like the false-colour legend (dark
  rounded box, caption text).
- Hovering the histogram's clip corners shows what is clipping on the live
  picture, exactly as on the main canvas (`core.clip_overlay`).

### 3.4 Status strip (under the canvas)
- **Camera** combo ("Camera 1 · 1920×1080"), **Find Cameras**, then muted:
  measured frames per second and the format the device actually gave
  (§5.3).
- Right: **Tethering: off / on → folder, N captured** from the main window's
  own Tethering, and **Start Tethering… / Stop Tethering** (calls
  `MainWindow.toggle_tethering`). The camera's tether app writes the frames;
  FreeCCR's Tethering imports them.

### 3.5 Side panel (`theme.PANEL_W`, scrolls, FreeCCR headings and separators)
1. **Histogram**: `HistogramWidget`, caption "Live", from the feed about four
   times a second, with its clip corners.
2. **Light**: status dot, port combo (likely USB ports first), **Rescan**,
   **Connect / Disconnect**; one muted line: "Responding · 5.1 V · 38 °C",
   "Port open but silent: another program may have it", "Not connected", or
   "Light control needs the pyserial package".
3. **LED**: Red, Green, Blue, White, Infrared. FreeCCR slider rows (label
   gutter, slider, value gutter "82.0 %"), each groove a gradient from dark
   to the channel's colour, double-click resets to 0. A caption says that
   Red/Green/Blue and White/Infrared exclude each other.
4. **Presets**: combo, **Save As…**, **Save** (overwrites, asks first); the
   combo's right-click has **Rename…**, **Delete…** and **Import…** (§9).
   Under it the four solo rings R, G, B, W with their keys 1–4, each ring's
   glow showing that channel's value in the preset.
5. **Capture**: **Trichrome (R → G → B)** checkbox, **Timing…** (popup:
   Focus, Pulse, Hold, Period), a large primary **Shoot (S)** button that
   reads **Stop (Esc)** during a sequence, and a caption with the cycle time
   and the Hold check (§8.3).
6. **Negative Preview** (collapsible, collapsed): **Film Base**, **Highlight**
   and **Mid Grey** (eyedroppers) and **Auto**; Temperature, Tint,
   Brightness; Black & white.

Disabled states say why in their tooltips (no light connected, no camera).

### 3.6 Keys
Window-scoped `QShortcut`s (`Qt.WindowShortcut`), so they do nothing while
the main window is in front: **1 2 3 4** solo, **S** shoot, **Esc** stops a
sequence, **F** Fit / 1:1. Qt doesn't fire a printable-key shortcut while a
text field or spin box has focus, so typing a number never lights a channel
(the reference's app-wide filter only spared plain text fields). Open
question 6: should S and 1–4 also work from the main window?

### 3.7 Settings (Settings → General → Live capture)
- Dim the light when idle after N minutes (0 = never; default 5).
- Video format: Automatic (default) / Prefer uncompressed / Prefer MJPEG.

## 4. Data model

QSettings only (FreeCCR's convention), under `live/`:

| Key | Value |
|---|---|
| `live/geometry`, `live/panel_hidden` | window state |
| `live/camera_index` | last working camera index |
| `live/video_format` | `auto` / `raw` / `mjpeg` |
| `live/toggles` | JSON: which toolbar toggles were on |
| `live/denoise`, `live/edge_amp`, `live/peaking` | JSON options dicts |
| `live/guide` | JSON `{cx, cy, w, h, angle, format}` in sensor fractions |
| `live/align_x` | sensor fraction 0..1 |
| `live/port` | last connected port name |
| `live/presets` | JSON `{"presets": [{name, r, g, b, w, ir}], "selected": i}` |
| `live/timing` | JSON `{focus_ms, pulse_ms, hold_ms, period_ms}` |
| `live/idle_minutes` | int |

Nothing goes in catalogs or image settings. Negative Preview values are
session-only (they describe one roll on one evening).

## 5. Live video

### 5.1 Threads
- Capture runs on a `utils.qt_threads.WorkerThread` (16 MB stack, FreeCCR's
  rule for every QThread). It loops on one blocking `read()` per frame, so the
  device sets the pace: no timer, and no extra grabs.
- The worker processes the newest frame (§5.5) and hands a `QImage` (owning
  its pixels) to the GUI through a queued signal. A one-slot mailbox keeps
  only the latest: if the GUI is behind, older frames are dropped, never
  queued (the same latest-wins rule as the live render).
- The GUI thread only paints and handles input. Settings changes reach the
  worker as one small, lock-guarded parameters object read once per frame.

### 5.2 Camera access on macOS
- Permission is asked on the main thread with Qt's `QCameraPermission`
  (Qt 6.5+, in PySide6 6.7) before the first open. The worker then opens the
  device with `OPENCV_AVFOUNDATION_SKIP_AUTH=1`, because OpenCV can't ask
  from another thread: it prints "can not spin main run loop from other
  thread" and the camera fails to start.
- Refused: the canvas says so and points to System Settings → Privacy &
  Security → Camera. Running from source, the permission belongs to the
  terminal app that started FreeCCR.
- The macOS bundle's `Info.plist` gains `NSCameraUsageDescription`; without
  it macOS refuses camera access to a bundled app.

### 5.3 Finding cameras and formats
- **Find Cameras** (and the first open) probes indices 0..5 on the worker,
  one at a time, each released before the next, the remembered index first;
  cancelled if the window closes. A device counts only if it delivers a
  frame. Labels are "Camera N · W×H" (index-to-name matching isn't reliable
  across macOS APIs, so no guessed names).
- Requested 1920×1080 at 60 fps. The format is not forced by default: the
  reference forced MJPEG, whose compression blocks can hide or fake fine
  detail while focusing; Settings can prefer one. What the device actually
  gave (size, measured fps over the last second, FOURCC when reported) is
  shown in the status strip, measured, not assumed.

### 5.4 Coordinates
Everything placed on the picture (frame guide, alignment line, sharpness
area, eyedropper samples) is stored in **sensor fractions** of the full frame
and mapped through the current zoom and pan, so it stays on the same spot of
film whatever the zoom, window size or panel state. A position outside the
current view is not drawn (rather than drawn at the edge).

### 5.5 Per-frame processing (worker)
In order, at sensor resolution on the visible part only:
1. Crop to the zoom / pan view (plus a margin for the filters).
2. **Reduce noise** (§5.6) → the "steady" frame. Eyedroppers, the histogram
   and the Negative preview read this; the sharpness meter reads the fresh
   frame (§5.7).
3. **Negative** LUT (§6), one `cv2.LUT` per channel.
4. **Edge amplify** (§5.6) and **Peaking** (§5.6).
5. Resize to the canvas's device pixels: `INTER_AREA` when shrinking,
   `INTER_LINEAR` when enlarging past 1:1.
6. **False colour** bands on the displayed result (§5.8).

Budget at 1080p60 is 16 ms; OpenCV releases the GIL, and the measured
per-frame time is logged with `FREECCR_LIVE_TIMING=1`.

### 5.6 View filters
- **Reduce noise**: a running average, `acc += s·(frame − acc)`, strength
  `s` 0.05–1 (default 0.35). It **resets itself when the picture changes**:
  the mean absolute difference of a 64×36 thumbnail against the last one,
  above a threshold, restarts the average, so moving the film doesn't smear
  ghosts across the view and the eyedroppers never read where the film used
  to be. Also reset on zoom, pan, camera or format change.
- **Edge amplify**: an unsharp mask on **luma only** (Rec.709 of the display
  values), the change added equally to R, G and B, so colour noise isn't
  amplified (the reference sharpened each channel). Amount 0–300 %
  (default 150), radius 0.5–3 px (default 1.2).
- **Peaking**: a white halo on edges: `|Laplacian(luma)|` through a
  smoothstep from Threshold to Threshold × 4, widened by Width (dilation),
  scaled by Intensity and added to the displayed picture. The reference had
  this code with no button; offered as a toggle (open question 4).

### 5.7 Sharpness meter
- Score: variance of the Laplacian of the lightly blurred luma in the area
  (whole view by default, or a drawn area), **divided by the area's mean
  luma squared**. A brighter or dimmer light scales the encoded picture
  roughly multiplicatively, which scales the raw variance by the square;
  dividing it out keeps the meter on focus. In the reference, dimming the LED
  read as losing focus.
- Shown as "% of best" with a bar and a tick at the session best, where
  "best" is the highest score **for this area and zoom**, reset when either
  changes or from the menu. The reference's best decayed with a 17 s
  half-life, so "100 %" drifted to mean "recently".
- Read from the fresh frame (before Reduce noise), so it follows the focus
  ring with no lag.

### 5.8 False colour
FreeCCR's own exposure bands (`core.false_colour.false_colour`) on the
displayed picture, computed at display size, with the main canvas's legend.
Stop zones are not offered: they read a negative's density from the RAW, and
the feed is the camera's rendered picture.

### 5.9 Sharp on high-DPI screens
The worker renders to canvas size × `devicePixelRatio`, and the image is
drawn with that ratio set, so a Retina screen shows the live view at full
sharpness. The reference scaled each frame to logical pixels, i.e. half
resolution on a Retina screen, exactly when the view is used for focusing.

## 6. Negative preview (rough positive)

### 6.1 What was measured
The reference inverts the 8-bit picture and stretches each channel between
`255 − base` and `255 − highlight` (two-point levels in gamma-encoded values),
then applies warmth/tint as channel gains and brightness as an added offset.
Encoded values can't follow the orange mask exactly: between the two sampled
points a neutral drifts when the film's three layers have different contrast
(the reference's own note says the cast grows toward the highlights). A
physically exact alternative, normalising each channel's density above the
base, was tried on a synthetic neutral wedge rendered the way the feed is
(sRGB, 8-bit, noise σ 1.5 levels, 8 frames averaged, 400 px per step):

| Film (layer contrasts R/G/B) | Two-point levels: cast / noise | Density: cast / noise | Three-point levels: cast / noise |
|---|---|---|---|
| mild (0.60 / 0.65 / 0.72) | 6.5 / 1.4 | 5.5 / 4.7 | 4.9 / 1.4 |
| crossed (0.55 / 0.65 / 0.80) | 15.5 / 1.3 | 5.7 / 5.1 | 9.5 / 1.5 |
| equal (0.65 each) | 1.4 / 1.4 | 4.6 / 4.7 | 1.3 / 1.4 |

(Cast: the largest spread between the three channels' means at any step, in
8-bit levels. Noise: the largest per-channel standard deviation.) Density is
exact on ideal data, but the feed gives a negative's dense end only a few
dozen levels (the blue channel of the highlights sat at 18–35 here), and the
logarithm turns that into 3–4× the noise in the highlights, plus a bias that
leaves 5–6 levels of cast anyway. For a preview that is worse, so it is not
used.

### 6.2 The model: levels, with the reference's clipping removed
One 256-entry table per channel, rebuilt only when a control moves, applied
per frame with `cv2.LUT`:
1. Invert and stretch: `t = ((255 − v) − (255 − base)) / ((255 − hi) − (255 − base))`,
   per channel (the reference's two points, kept: they are quiet).
2. **Mid grey** (optional third point): a per-channel gamma `t^g`, solved so a
   sampled neutral mid-tone lands on the same value in all three channels.
   Measured above: the crossed film's cast falls from 15.5 to 9.5 levels with
   no added noise.
3. Temperature and Tint as channel gains **normalised so the largest is 1**:
   in the reference a gain above 1 clipped that channel's highlights before
   the others, so a warm correction turned the bright areas yellow-white.
4. Brightness as a curve that keeps black and white, `t^(2^(−b/50))` for
   b in −100..100, instead of an added offset that clipped the highlights and
   lifted the black.
5. Clipped to 0..255 once, at the end. Black & white: Rec.709 luma of the
   result.

### 6.3 Samples
- **Film Base**: eyedropper on the rebate, a 13×13 px mean of the steady
  frame (§5.5).
- **Highlight**: eyedropper on a neutral highlight in the picture.
- **Mid Grey**: eyedropper on a neutral mid-tone (optional).
- **Auto**: base = the brightest even band near the frame edges (Auto black
  point's rebate search, tried on the feed first), else each channel's
  99.5th percentile; highlight = each channel's 0.5th percentile inside the
  frame guide if it is on, else the middle 80 %.
- Until a base is set, Negative uses Auto.

### 6.4 Not a conversion
This is for judging framing, focus and the frame; FreeCCR converts the RAW.
The feed is the camera's rendered 8-bit picture, so none of its values are
comparable with a RAW's, and nothing here is passed to the main window's
black point, white point or film stock.

## 7. Light (USB serial)

### 7.1 Protocol (`core/capture_light.py`, Qt-free)
Packets: start byte `0xFE`, header, data length, data.
- To the light: set colour (R, G, B, W, IR 0–255, save flag), firmware
  version request, shutter pulse (length in 10 ms units, 0–255), focus
  on/off.
- From the light, about every 200 ms: LED temperature (int32 m°C) and supply
  voltage (int32 mV), big-endian.
- A parser that resynchronises on the start byte and handles packets split
  across reads or several in one read.

### 7.2 One thread owns the port
A `WorkerThread` reads and writes; the GUI queues writes to it and never
touches the port itself. Colour writes while a slider is dragged are
coalesced to at most one per 16 ms, and the last value is always sent.

### 7.3 Connecting
- The port list puts likely USB ports first (`usbmodem`, `usbserial`, …) and
  macOS's own debug and Bluetooth ports last.
- DTR is set before opening (asserted by default), RTS off, then a firmware
  version request is sent. If nothing at all arrives within 2.5 s, the port
  is reopened once with DTR the other way. Still silent: the status line
  says the port is open but silent and another program may have it, and the
  dot turns amber. It never retries in a loop.
- The last connected port is reconnected automatically when it appears
  (checked every 3 s on the worker).
- **Unplugged**: a read or write error closes the port, shows "Disconnected",
  and automatic reconnection resumes. (The reference's reader stopped on an
  error while the window still said connected.)
- No `pyserial`: the Light section says so; the rest of the window works.
  `pyserial` is added to `requirements.txt` and to the build scripts.

### 7.4 Channels
0–255 each, shown as a percentage. Raising any of R, G, B sets W and IR to 0,
and the other way round: the light can't run both groups. A preset holding
both (from older files) keeps RGB, as the light itself would.

### 7.5 Idle dimming
After the set minutes without use (default 5) while connected and lit, the
lit channels breathe slowly between 5 % and 25 % (a 4 s cosine), then return
exactly when used. **Use** includes the pointer moving over the window and
the live picture changing (the §5.6 motion test), not only clicks and keys:
the reference dimmed the light while the lens was being focused by hand.
Never during a capture sequence.

## 8. Capture

### 8.1 One shot
Focus on → wait Focus → pulse for Pulse → wait Pulse + Hold → focus off.
Shots keep at least Period apart (a shot waits, it's never dropped). The
colour is set at least 50 ms before the pulse even with Focus at 0.
Defaults: Focus 200, Pulse 300, Hold 200, Period 1000 ms.

### 8.2 Trichrome
For R, G, then B: only that channel lit at the selected preset's value, then
a shot. Afterwards White at the preset's value, for positioning the next
frame. A state machine on Qt timers (`core/capture_sequence.py`), testable
with a fake clock and a fake light.
- **Stop** (the button or Esc) finishes the pulse in progress, never cuts
  one, then returns to White.
- If the light disconnects mid-sequence, the sequence stops and says so (the
  reference carried on "shooting" into a closed port).
- The active channel's ring shows a sweeping clock hand over that step's
  duration, as in the reference.

### 8.3 Hold must cover the exposure
If Hold is shorter than the camera's shutter time, the next colour lights
during the exposure and the frame mixes two colours. FreeCCR can't read the
camera's setting live, so:
- the Capture caption always shows the cycle time and "Hold must be longer
  than the camera's shutter time";
- with Tethering on, the first frame that arrives after a Shoot is checked:
  if its EXIF exposure time is longer than Hold, the caption turns to a
  warning naming both ("Hold 200 ms is shorter than the 1/4 s exposure").

## 9. Presets
Named R, G, B, W, IR sets. **Save As…** (name), **Save** (overwrite, asks),
**Rename…**, **Delete…** (asks; the last one can't be deleted). Solo lights
one channel at the preset's value; Trichrome uses the preset's R, G, B and
W. **Import…** reads a presets file of the shape
`{"presets": [{"name", "r", "g", "b", "w", "ir"}], "selected_index"}`,
adding the ones whose names are new, so presets made in the reference app
can be brought over by choosing its file.

## 10. Faults in the reference, and what this does instead
Seen in its code; not checked on the hardware, so the ones marked *likely*
are worth confirming against its FPS readout and behaviour.

| # | In the reference | Effect | Here |
|---|---|---|---|
| 1 | Frames read and processed on the GUI thread from a timer; each tick grabs twice before retrieving | *likely*: each grab waits for a new frame, so a displayed frame costs two or three, about half the feed's rate, with the window waiting during every tick | worker thread, one read per frame (§5.1) |
| 2 | Every frame smooth-scaled to logical pixels on the GUI thread | half resolution on a Retina screen; GUI time | device pixels, resized on the worker (§5.9) |
| 3 | Camera probed and opened from a background thread on macOS | OpenCV can't ask for camera permission there | permission on the main thread first (§5.2) |
| 4 | MJPEG forced | compression blocks while judging focus | not forced; actual format shown (§5.3) |
| 5 | Serial reader stops silently on an error | unplugged light still shows connected | disconnect, then reconnect when it returns (§7.3) |
| 6 | Port read on one thread, written from another; settings JSON writes swallow every error | races; lost settings with no sign | one thread owns the port; QSettings (§4, §7.2) |
| 7 | App-wide key filter that only spares plain text fields | typing a digit into a spin box lights a channel; keys act in other windows | window-scoped shortcuts (§3.6) |
| 8 | Idle counted from clicks, keys and wheel only | light dims while focusing by hand | pointer movement and picture change count (§7.5) |
| 9 | Sequence can't be stopped, and continues if the port drops | stuck or silent failures | Stop; abort on disconnect (§8.2) |
| 10 | No check that Hold covers the exposure | frames that mix colours, unnoticed | caption and EXIF check (§8.3) |
| 11 | Raw Laplacian variance; best decays | meter moves with brightness; "100 %" drifts | normalised score; best per area and zoom (§5.7) |
| 12 | Fixed-rate running average | ghosts when film moves; eyedropper reads the old position | resets on motion (§5.6) |
| 13 | Edge amplify per RGB channel | amplified colour noise | luma only (§5.6) |
| 14 | Warmth/tint gains above 1 and brightness as an added offset | one channel's highlights clip first; black lifts | normalised gains, a brightness curve, optional mid-grey point (§6) |
| 15 | Invert / Levels buttons hidden when White is off | toolbar jumps | disabled with a reason (§3.2) |
| 16 | Peaking implemented with no button | dead code | a toggle, if wanted (§5.6) |

## 11. Integration points
- `ui/main_window.py`: the File menu action after **Tethering…**; holds the
  single window; closes it in `closeEvent`; exposes Tethering state for the
  status strip and emits each newly tethered file's path (for §8.3).
- New: `widgets/live_monitor.py` (window, toolbar, side panel, popups),
  `widgets/live_canvas.py` (painting, overlays, zoom, mouse),
  `core/live_feed.py` (capture worker, discovery, Qt-free processing
  functions), `core/live_negative.py` (the Negative tables), `core/capture_light.py`
  (protocol and link), `core/capture_sequence.py` (shots and trichrome).
- Reused: `ui.theme` tokens and helpers, `CollapsibleSection`,
  `GradientSlider` / `ResettableSlider`, `HistogramWidget`,
  `core.false_colour`, `core.clip_overlay`, `core.film_formats`,
  `core.auto_black_point` (rebate search, §6.3), `utils.qt_threads`.
- `widgets/settings_dialog.py`: the Live capture group (§3.7).
- `requirements.txt`: `pyserial`. Build scripts: include the `serial`
  package (its port listing imports platform modules by name).
  `macos_build_scripts/Info.plist`: `NSCameraUsageDescription`.
- `CLAUDE.md`: a short note on the window, its threads and its reference
  faults.

## 12. Test plan (headless, no hardware)
- **Protocol**: exact bytes of every command; the parser with split packets,
  junk between packets, several packets in one read; telemetry values.
- **Link** with a fake port: DTR retried exactly once; the silent-port
  message; an error → disconnected → reconnects when the port reappears;
  coalesced colour writes keep the last value; no pyserial → the panel
  message and a working window.
- **Sequence** with a fake clock and light: R, G, B order and values, every
  timing, Period waits, Stop mid-sequence ends on White after the current
  pulse, disconnect mid-sequence stops it, the Hold-vs-exposure warning from
  a tethered file's EXIF.
- **Feed** with a fake capture device: one read per frame; a slow consumer
  gets the newest frame and nothing queues; the device is released on close;
  probe order and release; the permission-refused message.
- **Processing**: click → sensor fraction → click round-trips at every zoom;
  Reduce noise resets on a moved frame and converges on a still one; Edge
  amplify leaves chroma unchanged (Cb/Cr within 1 code); the sharpness score
  is unchanged by a ×0.5 brightness (within 2 %) and falls by over half for
  a 1 px blur; output size = canvas × DPR.
- **Negative tables**: on the §6.1 synthetic wedges, two-point and
  three-point casts at or below the measured values (6.5 / 15.5 / 1.4 and
  4.9 / 9.5 / 1.3 levels), noise not above the two-point model's; a sampled
  mid grey comes out neutral within 1 level; Temperature and Tint never
  push a channel above the untouched table (no new clipping); Brightness
  keeps 0 → 0 and 255 → 255; every table is monotone.
- **Window**: opens from the File menu, a second open raises it, camera and
  port released on close; shortcuts scoped (a digit typed into a spin box
  doesn't solo); Negative disabled while White is 0; every panel row fits
  `PANEL_W` (the panel width test).
- **On your Mac** (a checklist for you, not run by me): FPS at 1080p60, the
  permission prompt, Retina sharpness at 1:1, connect / unplug / replug,
  trichrome timing with your camera and tether app.

## 13. Open questions
1. "Light" is the generic name used throughout; is that right, or do you
   want the device named?
2. A separate window (recommended, can sit on a second screen) or a page in
   the main window?
3. On closing the monitor: leave the light as it is (recommended), or turn it
   off?
4. Peaking: include it (the reference had it, unused)?
5. Later, separately: FreeCCR's Tethering imports single frames, so a
   trichrome triplet arrives as three. Group each three into one 3-way merge
   as they land? That would be its own spec.
6. S and 1–4: only in the monitor window (recommended), or from the main
   window too?

## 14. Build order (one branch, one commit per step)
1. Protocol, link and sequence (Qt-free, fully tested) with the Light, LED,
   Presets and Capture panel.
2. Live feed worker, canvas, zoom, status strip.
3. View filters, sharpness meter, histogram / clip / false colour.
4. Frame guide and alignment line.
5. Negative preview.
6. Settings group, File menu, build script and plist changes, CLAUDE.md.
