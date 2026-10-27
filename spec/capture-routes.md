# Spec: Capture routes — the USB camera route

Status: REFINED v2, built (2026-10-26). The user's answers are in §10; as
built in §11.
Owner: FreeCCR
Feature branch: `feature/capture-routes`
Related: `spec/live-capture-monitor.md`, `spec/camera-tethering.md` (Hot
Folder Tether), `spec/scan-type.md`.

## 1. Summary

The Live Capture Monitor assumed one setup: live view through an HDMI
capture device, the shutter fired by the Scanlight's release output, and the
camera maker's own tether app saving into the folder Hot Folder Tether
watches. This adds a **USB camera** route: the camera's own live view, its
shutter and its files over its USB cable, so a setup without a capture card,
release cable or tether app still works.

Two open-source backends drive it, chosen by what is available:

| Backend | Platforms | What it is |
|---|---|---|
| **libgphoto2** (the `gphoto2` Python package) | macOS, Linux | Talks to the camera directly. Its camera table lists the Canon EOS 5D Mark III and IV (and Fujifilm GFX 50S/R) with capture and live view. |
| **digiCamControl** | Windows | A free, open-source Windows camera-control app with a built-in web server on `localhost`. FreeCCR asks it for live view frames and to capture; it saves the files into the hot folder. |

libgphoto2 has no Windows build (and on Windows it needs the camera's USB
driver replaced, which stops other software seeing the camera), so Windows
goes through digiCamControl, which uses the normal driver.

The files always arrive through **Hot Folder Tether**: libgphoto2's files are
downloaded into the hot folder by FreeCCR, digiCamControl's session folder is
pointed at it. So triplet grouping, the light check, Needs attention, B&W
handling and the first-frame black point prompt are all unchanged.

## 2. Goals / Non-goals

### Goals
- USB cameras appear in the monitor's **Camera** list beside the capture
  devices ("USB · Canon EOS 5D Mark III", "digiCamControl · <camera>"); the
  picture tools (zoom, Edge amplify, false colour, Negative preview,
  sharpness, guides) work the same on them.
- A **Shutter** choice in Capture: *Scanlight* (today) or *USB · <camera>*
  (listed when a USB camera is open). Choosing a USB camera for the view
  picks it for the shutter too; HDMI view + USB shutter is allowed.
- With the USB shutter: Shoot fires the camera; single shots (colour, B&W)
  don't need the Scanlight (the light is used as it is); trichrome still
  needs it (to set the colours). The sequence is **event-driven**: colour →
  settle → fire → wait for the file → next colour.
- The camera's own shutter button works too: its files come in the same way.
- RAW + JPEG: only the RAW is brought in (the hot folder's own rule).
- Shutter speed, ISO and aperture shown read-only under the camera.
- **Camera zoom** (Canon EOS live view 1× / 5× / 10×) for focusing, where
  the backend offers it.
- macOS: when the system's camera service holds the camera, say so and offer
  **Release Camera**.
- Windows: the Scanlight is found by its USB ID (Windows names serial ports
  `COM3` etc., which the name-based ranking didn't recognise).

### Non-goals (v1)
- Manual light and Check setup (proposed in draft v1; not chosen for now).
- Setting exposure from the app.
- Camera makers' own SDKs (closed).
- Deleting files from the camera.

## 3. UX

### 3.1 Camera (status strip under the picture)
The Camera combo lists capture devices as now, then USB cameras:
`Camera 0 · 1920×1080`, `USB · Canon EOS 5D Mark III`,
`digiCamControl · 5D Mark III`. **Find Cameras** searches both. The info text
beside it shows `1/60 s · ISO 100 · f/8` for a USB camera, and a **Camera
zoom** combo (1× / 5× / 10×) appears when the camera offers it.

### 3.2 Shutter (Capture section)
A row under Scanning: `Shutter [ Scanlight ▾ ]` / `[ USB · Canon EOS 5D
Mark III ▾ ]`. Timing… applies to the Scanlight shutter; with the USB shutter
only **Settle** (the Focus field, relabelled in the popup when USB is chosen)
applies: the time between setting a colour and firing. The caption under
Shoot says what happens: "Trichrome: R, G, B over USB, then White · about 6 s".

### 3.3 Where files go
Shooting over USB needs Hot Folder Tether: Shoot asks to turn it on if it is
off (the same prompt the monitor shows on opening). libgphoto2 files are
written as `<name>.part` and renamed when complete, so the hot folder never
sees half a file; a name already in the folder gets `_1`, `_2`…
digiCamControl: on opening, its session folder is set to the hot folder (and
put back on closing).

### 3.4 Messages
- No backend installed: the USB entries simply don't appear; the Find Cameras
  tooltip says how to add them (the `gphoto2` package on Mac/Linux;
  digiCamControl with its web server on, on Windows).
- Camera held by macOS's camera service: "macOS is holding the camera" with
  **Release Camera** (ends the service's hold; it may come back when the
  camera is replugged).
- Unplugged / lost: as the capture device's "stopped sending frames"; a
  running sequence stops ("Stopped: the camera disconnected").
- A shot whose file doesn't arrive within 30 s stops the sequence ("No file
  from the camera after 30 s").

## 4. Data model
- QSettings: `live/camera_key` (the chosen camera: an int index or
  `usb:<backend>:<id>`; `live/camera_index` kept for HDMI), `live/shutter`
  (`scanlight` | `usb`), `live/dcc_port` (digiCamControl's web port, default
  5513).
- No catalog changes.

## 5. Architecture

### 5.1 Backends (`core/camera_backends.py`, no Qt)
Each backend is used from ONE thread only (libgphoto2 calls block and a
camera can't be shared between threads):
```
available() -> bool
list_cameras() -> [CameraInfo(key, label)]
open(key, dest_dir)            # dest: the hot folder (may be None)
close()
set_dest(dest_dir)
start_live() / stop_live()
preview() -> jpeg bytes | None
fire()                         # returns when the camera accepted it
poll(timeout_s) -> [Event]     # ("file", path) saved into dest, ("lost",)
exposure() -> {"shutter", "iso", "aperture"}
zoom_levels() -> [str] ; set_zoom(level)
```
- `Gphoto2Backend`: `gp.Camera.autodetect`, port + abilities lookup, `init`;
  `capture_preview`; `trigger_capture`; `wait_for_event` → FILE_ADDED
  collected for a short quiet spell (0.3 s), RAW kept over a same-name JPEG,
  `file_get` → `.part` → rename; config `shutterspeed` / `iso` /
  `aperture` or `f-number`; `eoszoom` for camera zoom. Error code -53 (USB
  claim) → `CameraBusy`; -52/-7/-34/-35 repeated → lost.
- `DigiCamControlBackend`: HTTP on `127.0.0.1:<port>` (urllib, 1 s timeouts):
  `?slc=list&param1=cameras` (serials), `?slc=set&param1=camera&param2=<id>`,
  `?slc=get/set&param1=session.folder`, `?CMD=LiveViewWnd_Show/Hide`,
  `/liveview.jpg`, `?slc=capture&param1=&param2=`,
  `?slc=get&param1=lastcaptured` (a new name, not `-`, is a new file in the
  session folder = the hot folder), `?slc=get&param1=shutterspeed|iso|aperture`.
  Responses are read as JSON when they parse, else as text.
- `FakeBackend` for tests (and a demo env var).

### 5.2 The worker (`core/usb_camera.py`)
`UsbCamera(WorkerThread)` owns the backends: a command queue in (scan, open,
close, live, fire, zoom, dest, release), signals out (`cameras_found`,
`state(kind, text)`, `file_saved(path)`, `exposure(dict)`, `fired()`).
Live view frames are decoded (`cv2.imdecode`) into a one-slot latest-wins
mailbox; `frame_source(key)` returns a capture-like object (`read`, `get`,
`release`) that `LiveFeed` opens like an OpenCV device, so the `Processor`
and everything after it is untouched.

### 5.3 LiveFeed
`open_camera(key)` takes an int (OpenCV) or a `"usb:…"` key, dispatched to
the source registered for its prefix (`feed.sources["usb"]`). A USB source's
`read()` waits up to 0.2 s for a new frame and repeats the last one meanwhile
(live view pauses while the camera captures), so a capture isn't mistaken
for a lost camera; it fails only when the worker reports the camera lost.

### 5.4 The sequence
`CaptureSequence.start(..., shutter=None)`: with a shutter object (USB) the
shot is colour → settle (Focus, ≥ 50 ms) → `shutter.fire()` → wait for
`frame_done()` (the monitor calls it on `file_saved`) or a 30 s timeout →
next. Stop never abandons a shot in flight: it finishes when that file lands.
Without a shutter: the timer sequence exactly as built.

## 6. Integration points
- `core/camera_backends.py`, `core/usb_camera.py` (new).
- `core/live_feed.py`: key dispatch, `sources`.
- `core/capture_sequence.py`: event shutter.
- `core/capture_light.py`: ports ranked by USB ID (RP2040 `0x2E8A` first).
- `widgets/live_monitor.py`: camera list, Shutter row, exposure/zoom, Shoot
  gating, hot folder prompt on USB Shoot, Release Camera, settle label.
- `requirements.txt`: `gphoto2` as an optional line (commented, macOS/Linux);
  build scripts include it when installed; `LICENSES/license-gphoto2.txt`.
- `ui/main_window.py`: `tether_folder()` accessor.

## 7. Edge cases
- Live view and capture share one cable: the worker stops nothing itself;
  libgphoto2 pauses live view during a capture and resumes on the next
  preview call.
- Two USB cameras: listed separately, one open at a time.
- The camera writes to its card and to the computer: files on the card stay.
- Hot Folder Tether stopped while the USB camera is open: downloads pause
  (files stay on the camera) and the caption says so.

## 8. Test plan (`tests/test_capture_routes.py`)
- Gphoto2Backend against a fake `gphoto2` module: list, open, preview, fire,
  FILE_ADDED → `.part` → rename, RAW kept over JPEG, name clash suffix,
  claim error → busy, I/O errors → lost, exposure, zoom.
- DigiCamControlBackend against a local fake HTTP server: list, session folder
  set and restored, live view frame, capture → new `lastcaptured` → file
  event, JSON and text responses, server gone → lost.
- UsbCamera worker with the FakeBackend: frames reach a LiveFeed through the
  `usb:` key; `file_saved`; lost.
- Sequence: event-driven single and trichrome, settle, timeout, Stop mid-shot
  waits for the file, light needed only for trichrome.
- Ports: a `COM5` with the RP2040 USB ID ranks first.
- Monitor: USB cameras listed, Shutter row follows the view, Shoot over USB
  asks for the hot folder when it's off, exposure text shown.

## 9. Licences
libgphoto2 (LGPL-2.1+) and python-gphoto2 (LGPL-3.0+) are compatible with
FreeCCR's AGPL-3.0; their licence text goes in `LICENSES/`. digiCamControl is
a separate program the user installs; nothing of it is shipped.

## 10. Decisions (2026-10-26, the user's answers)
1. Build the USB camera route (Manual light and Check setup: not now).
2. The friend scans on Windows: Windows is covered through digiCamControl.
3. Naming open-source software in the app is fine; only closed-source
   software and brands are not to be emulated and named.

## 11. As built
- `core/camera_backends.py`: `Gphoto2Backend`, `DigiCamControlBackend`,
  `FakeBackend` (tests; `FREECCR_FAKE_USB_CAMERA=1` lists it in the app),
  `default_backends()` (gphoto2 when installed; digiCamControl on Windows or
  with `FREECCR_DCC=1`), `release_macos_camera_service()`, `keep_raw`,
  `unique_path`, `exposure_text`. A camera without USB live view (libgphoto2
  answers "unsupported") sets `no_preview`: the monitor says so and the camera
  still shoots.
- `core/usb_camera.py`: `UsbCamera` (the worker) and `UsbFrameSource`.
  `open()` marks the camera "opening" at once so the source made for it is
  alive while it opens (a camera can take seconds).
- `core/live_feed.py`: `open_camera(key)`, `sources`, `find_cameras(...,
  open_found)`; a source with `alive()` true is never reported lost.
- `core/capture_sequence.py`: `start(..., shutter=)`, `frame_done`,
  `fire_failed`, `abort`, `settle_ms`, `cycle_ms(camera=)`, FILE_TIMEOUT_S.
- `core/capture_light.py`: `port_info_rank` (the Scanlight's USB ID first).
- `widgets/live_monitor.py`: USB cameras in the Camera list, the Shutter row,
  exposure text, Camera zoom, Release Camera (macOS), USB Shoot asking for the
  hot folder, captions for timeouts and a lost camera, the USB Timing popup
  (Settle, Period).
- `ui/main_window.py`: `tether_folder()`.
- Build: `macos_build_scripts/build_compatible.sh` bundles gphoto2 when it is
  installed; `requirements.txt` notes it (not a requirement: older macOS has
  no wheel); `LICENSES/license-gphoto2.txt`.
- Tests: `tests/test_capture_routes.py`, including the real libgphoto2
  against its own virtual camera (the `vusb` driver in the gphoto2 wheels):
  list, open, exposure, fire, the file event and the `.part` download.
- Not verified here (no hardware): a real 5D Mark III / IV over libgphoto2,
  digiCamControl's actual reply format on Windows (both JSON and plain text
  are handled), and the Nuitka-built app's bundled libgphoto2 drivers.

## 12. Magic Lantern notice (revision 2026-10-26, the user's request)

### Goal
Warn a user whose camera has Magic Lantern (open-source, unofficial
software that runs alongside the camera's own firmware) on its card. Nothing
in FreeCCR needs it or touches it; the notice is so that a camera that
misbehaves while tethered can be ruled in or out.

### Detection
Magic Lantern installs `autoexec.bin` and an `ML` folder at the top of the
card (its install guide). `Gphoto2Backend.addons()` lists each storage's top
level after the camera opens (`folder_list_folders('/')`, then
`folder_list_files` / `folder_list_folders` per store, names compared
case-insensitively) and reports "Magic Lantern" when both are there. It
means the card carries it; whether it booted can't be told over USB.
- digiCamControl (Windows) and the HDMI route can't list the card: no
  notice there.
- A listing that fails is no finding (never an error).

### UX
- Once per session per camera, unless turned off: a warning box, "Magic
  Lantern on this camera's card", saying FreeCCR only uses the camera's
  standard USB controls and doesn't need it; that Magic Lantern is
  unofficial software its own developers say can't be guaranteed safe and is
  used at the user's own risk, and that a wrong setting written by it can stop
  a camera starting up; and that if the camera misbehaves while tethered, it
  can be started without Magic Lantern by holding SET while switching on, or
  with a card that doesn't have it. Check box: "Don't warn me about this
  again" (QSettings `live/warn_addons`, default on).
- While that camera is open, a small warning-coloured "Magic Lantern on
  card" beside the camera info, with the same text as its tooltip (shown even
  when the box is turned off).
- Brand names: the camera maker isn't named ("the camera's own firmware").

### Architecture
- `addons()` on every backend (`[]` for digiCamControl; the fake's is set
  by tests); `UsbCamera` emits `addons_found(list)` after a successful open.
- `LiveCaptureMonitor._on_addons` → `_addon_notice(model, found)` (the test
  seam, returns don't-ask-again) and the badge.

As built: as above; `addons_on_card` holds the rule, `FakeBackend.card_addons` the
tests' finding.

### Tests
Fake libgphoto2 listings (found / only one of the two / listing error), the
real libgphoto2 virtual camera with `autoexec.bin` and `ML/` in its folder,
the worker's signal, and the monitor's box (shown once, suppressed by the
setting) and badge (cleared when the camera closes).

## 13. The camera's own name (revision 2026-10-26)
libgphoto2 matches a camera to its table by USB id, and Fujifilm reuses ids
across models: a GFX 50S II matches the GFX100 entry (libgphoto2 issue 1282;
it has no entry of its own as of its 2026-10-23 source). Once a camera is
open, its label is the model it reports (`cameramodel`, with the
`manufacturer`'s first word when the model lacks it); the table name is only
used before opening. Whether a 50S II captures and streams live view through
the GFX100 entry is untested.

## 14. A setting with no value crashed the process (2026-10-27)
On the user's GFX 50S II, walking the camera's settings with python-gphoto2
2.6.4 killed Python (SIGSEGV in `CameraWidget.get_value` →
`PyUnicode_FromString(NULL)`): for text / radio / menu widgets its wrapper
checks the pointer to the result instead of the result, so a setting the
camera leaves empty dereferences NULL. No exception is raised, so nothing in
Python can catch it. `camera_backends.widget_value` reads string settings
through libgphoto2's own `gp_widget_get_value` (ctypes, on the same library
file the package loaded, so the same instance) and returns None when the
value is NULL; numbers still go through python-gphoto2. Every setting the
backend reads (`exposure`, `reported_model`) uses it. Tested on an empty
widget made in libgphoto2 itself and across the virtual camera's settings.

## 15. The GFX 50S II on test (2026-10-27)
On the user's GFX 50S II (firmware 2.12, libgphoto2 2.5.34, matched to the
GFX100 entry): trigger + wait for the file + download WORKS (one RAF,
`/store_10000001/DSCF0001.raf`); live view answers `-110 I/O in progress`
every time, before or after a shot, and closing then also answers busy; the
`f-number` setting is the empty one that crashed python-gphoto2 (§14).
Consequences, built:
- The Shutter row lists every USB camera found, not only the open one, and
  choosing one opens it as the shutter only (no live view asked). So the
  50S II can fire over USB and bring its files in while the HDMI capture
  device gives the picture, with no shutter cable and no maker's tether app.
  The remembered shutter is reopened the same way at start.
- A camera that has never given a frame and answers live view with busy
  `PREVIEW_BUSY_LIMIT` (5) times is treated as having no USB live view
  (`no_preview`): the monitor says so and stops asking. A camera that has
  given frames is only busy mid-capture and keeps being asked.
Still open: whether a failed live view spoils the next shot and whether a
reconnect clears it (test 3 sent to the user).
