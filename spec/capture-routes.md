# Spec: Capture routes (other cameras, shutters and lights)

Status: DRAFT for review (2026-10-26). Open questions in §10.
Owner: FreeCCR
Feature branch: `feature/capture-routes`
Related: `spec/live-capture-monitor.md`, `spec/camera-tethering.md` (Hot
Folder Tether), `spec/scan-type.md`.

## 1. Summary

Today the Live Capture Monitor assumes one setup: the camera's live view
through an HDMI capture device, the Scanlight on USB serial, the shutter fired
by the Scanlight's release output, and the camera maker's own tether app
saving into the folder Hot Folder Tether watches. Each of those four is made a
**route** with alternatives, so other setups work without that exact kit:

| | Route today | Added routes |
|---|---|---|
| Live view | HDMI capture device | **USB camera** (the camera's own live view over its USB cable) |
| Shutter | Scanlight release output | **USB camera**; **Manual** (you press the shutter) |
| Files in | Hot folder (the maker's tether app) | **USB camera** (downloaded straight into the session folder) |
| Light | Scanlight | **Manual light** (the monitor asks for each colour) |

The USB camera route uses libgphoto2 (through the `gphoto2` Python package),
which drives most Canon, Nikon, Sony and Fujifilm bodies over USB: its
camera table lists the Canon EOS 5D Mark III and Mark IV (and the GFX 50S/R)
with capture and live view. One cable then gives live view, the shutter and
the files, with no capture card, release cable or separate tether app.

## 2. Goals / Non-goals

### Goals
- A **Camera** choice in the monitor: *HDMI capture device* (today) /
  *USB camera* (listed by model when found) / *None*. The picture tools
  (zoom, Edge amplify, false colour, Negative preview, sharpness, guides) work
  the same on every route: they run on frames, wherever frames come from.
- With a USB camera: Shoot fires it over USB and the RAW lands in the session
  folder, where Hot Folder Tether picks it up exactly as now (triplets, the
  light check, Needs attention all unchanged). Pressing the camera's own
  shutter button is caught too.
- A **Shutter** choice: *Scanlight* / *USB camera* / *Manual*.
- A **Light** choice: *Scanlight* / *Manual*. Manual: the monitor shows the
  colour to set ("Set the light to RED, then shoot") and moves on when the
  frame arrives.
- Trichrome on the USB-camera shutter is **event-driven**: set the colour,
  wait the settle time, fire, wait for the file, next colour. No pulse/hold
  timing to tune, and a dropped frame is a timeout that stops the sequence,
  not a shifted triplet.
- **Check setup**: one button that lists what is connected, takes a test
  shot (or triplet) and reports whether files arrive, decode, and (trichrome)
  how strongly each frame is lit by its own colour on this camera.

### Non-goals
- Windows for the USB camera route in v1: the `gphoto2` package has no
  Windows build. Windows users keep the HDMI route and Hot Folder Tether with
  their camera's own app (§7).
- Camera makers' own SDKs (closed licences, per-maker code).
- Changing exposure settings from the app in v1 (shown read-only: shutter
  speed, ISO, aperture where the camera reports them). See §10 Q3.
- Video recording.

## 3. UX

### 3.1 The monitor's Setup section (top of the controls)
```
Camera   [ USB · Canon EOS 5D Mark III ▾ ]   1/60 s · ISO 100 · f/8
Shutter  [ USB camera ▾ ]
Light    [ Scanlight · connected ▾ ]
[ Check setup ]
```
- Camera lists capture devices (as now) and USB cameras by model; *None*
  leaves the picture area blank with a note (shooting still works).
- Shutter offers only what can work: USB camera when one is chosen, Scanlight
  when it is connected, Manual always.
- Choices persist (QSettings `live/camera_route`, `live/shutter`,
  `live/light`), with the camera remembered by model.
- Shoot is enabled when the shutter route can fire (today it needs the
  Scanlight even for a single shot).

### 3.2 USB camera specifics
- Live view frames are the camera's live-view JPEGs (smaller than HDMI;
  around the camera's own screen size). On Canon EOS bodies the camera's live
  view magnification (5× / 10×) is offered as **Camera zoom** for focusing, on
  top of the app's own zoom.
- When the file arrives the session folder gets it under the camera's own
  name; Hot Folder Tether is started on that folder if it isn't already
  (asking first, as the monitor does now).
- **macOS**: the system's own camera service can hold the camera so nothing
  else can open it. When that happens the monitor says so and offers
  **Release camera** (ends that service's hold for this session; asks first).

### 3.3 Manual light
The colour rings show which colour to set; Shoot (or the file arriving, with
Manual shutter) moves on. Trichrome: "Set RED · shoot", "Set GREEN · shoot",
"Set BLUE · shoot", then "Back to WHITE".

### 3.4 Check setup
A small dialog: what was found (capture devices, USB cameras, light ports),
then **Test shot** (single) or **Test triplet** (trichrome): fires, waits for
the files, decodes them, shows the light shares per frame (§5.3 of
spec/scan-type.md) and says whether the trichrome light check will pass on
this camera. If the weakest own-colour share is close to the threshold it
offers to set the threshold for this camera model (stored per model).

## 4. Data model
- QSettings: `live/camera_route` (`hdmi` | `usb` | `none`), `live/usb_model`,
  `live/shutter` (`scanlight` | `usb` | `manual`), `live/light`
  (`scanlight` | `manual`), `scan/light_share_min/<model>`.
- No catalog changes. Frames record `capture_type` as now.

## 5. Processing / architecture
- `core/usb_camera.py`: `UsbCamera`, a `WorkerThread` that is the ONLY code
  touching the camera (libgphoto2 calls block and one camera can't be shared
  between threads), the way `LightLink` owns the serial port. Commands in
  (preview on/off, capture, zoom), events out (frame JPEG, file added, status,
  lost). Frames go through the same latest-wins mailbox as `LiveFeed`, then
  `cv2.imdecode` into the same `Processor`.
- `core/live_feed.py`: the frame source becomes pluggable (`OpenCvSource`,
  `UsbCameraSource`); `Processor` is untouched.
- `core/capture_sequence.py`: the shutter becomes an object with `fire()`
  and a "frame done" signal; the timer path (Scanlight) stays as built, the
  event path (USB, Manual) waits for the file with a timeout (default 20 s).
- Downloading: on "file added" the worker saves the file into the session
  folder with a temporary name and renames it when complete, so Hot Folder
  Tether never sees half a file.
- Packaging: `gphoto2` is an optional dependency (macOS and Linux wheels
  bundle libgphoto2). Missing → the USB route is hidden with a note. Licences
  (LGPL) go in `LICENSES/`. Builds include the package and its libraries.

## 6. Integration points
- `widgets/live_monitor.py`: the Setup rows, Check setup, Manual light,
  Shoot enabling, Camera zoom.
- `core/live_feed.py`, `core/capture_sequence.py`, new `core/usb_camera.py`.
- `ui/main_window.py`: unchanged apart from starting Hot Folder Tether on the
  session folder when the USB route needs it.
- `core/scan_type.py`: `LIGHT_SHARE_MIN` read per camera model when set.
- `requirements.txt` (optional extra), build scripts, `LICENSES/`.

## 7. Windows
The USB route isn't available. The monitor's HDMI route and Hot Folder
Tether already work there with the camera maker's own tether app saving into
the watched folder. The Check setup dialog says so instead of listing USB
cameras.

## 8. Edge cases
- The camera's card vs computer: files are taken from the camera's memory
  after each shot and saved to the computer; whether a copy stays on the card
  follows the camera's own setting.
- Live view and capture on one cable: the worker pauses live view for the
  capture and resumes it (some bodies need it), so the picture blinks once
  per frame.
- Camera unplugged mid-sequence: the sequence stops, the light goes to
  White, the status says so (as the Scanlight disconnect does).
- Two cameras connected: listed separately; one at a time.

## 9. Test plan
- `UsbCamera` against a fake libgphoto2 module (events, file added, busy,
  claimed by another process, unplug): the worker's queue, the mailbox, the
  temporary-name download.
- Sequence: event-driven trichrome with a fake shutter: order, settle, timeout
  stops it, Stop mid-way.
- Manual light: prompts in order; file arrival advances.
- Route persistence and the Shutter list's availability rules.
- Check setup: shares and the per-model threshold, with fake decoded frames.
- No `gphoto2` installed: the route is hidden, nothing else changes.

## 10. Open questions (for the user)
1. **Order**: USB camera route first (it covers the 5D Mark III/IV case
   end to end), then Manual light and Check setup? Or all together?
2. **Your friend's computer**: Mac, Linux or Windows? (Windows means Hot
   Folder Tether with the camera's own app for now.)
3. **Exposure from the app**: read-only in v1, or set shutter speed per
   colour for trichrome (balancing the channels with exposure instead of LED
   levels)?
4. **His light**: a Scanlight (its release output should fire a 5D through
   an N3 adapter cable), another RGB light, or white light only?
