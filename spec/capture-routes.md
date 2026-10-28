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
  five times was treated as having no USB live view (superseded by §16)
  (`no_preview`): the monitor says so and stops asking. A camera that has
  given frames is only busy mid-capture and keeps being asked.
Test 3 (2026-10-27, with libgphoto2's debug log) settled the rest:
- A failed live view does NOT spoil the next shot, and reconnecting works:
  shot, failed live view, shot, reconnect, shot all succeeded.
- Live view: libgphoto2 sends Initiate Open Capture (PTP 0x101C) and the
  camera answers Device Busy (0x2019) within a millisecond, five times; it is
  refused, not slow. The tests ran in PC Connection Mode "USB tether shooting
  fixed"; "auto" refuses live view the same way.
- "Disconnect failed": on exit libgphoto2 tries to hand control back to the
  body (Fuji PriorityMode 0xD207 → Camera) and the camera answers busy; the
  port is closed anyway, so it is cosmetic, but the body may stay in USB
  priority until it is switched off and on.
- A shot is AF request → Shoot (property 0xD208 + Initiate Capture twice,
  ~40 ms apart) then polling for the new object: ~0.8 s to the file, then
  ~2.5 s to download one uncompressed RAF, so about 3.4 s a frame (≈ 11 s a
  trichrome triplet). The user's contactless lens with release priority is
  why the AF step never holds the shutter.
- `f-number` is empty because the lens has no contacts: the camera reports
  aperture 0, which is not in its own list of apertures, so libgphoto2 leaves
  the setting without a value — any adapted manual lens will do the same.

## 16. Live view works after all (2026-10-27)
In the Live Capture Monitor itself the GFX 50S II streams live view over USB:
XGA (1024×768) at about 3 fps, with shutter speed and ISO shown. The test
scripts only ever saw Device Busy because they asked straight after
connecting or straight after a shot and then stopped; the monitor reads its
settings first and keeps asking. So the camera refuses Initiate Open Capture
for a while after a capture, then accepts it (test 4 measured no wait at all
after connecting, §17). Changes:
- "busy" is retried with a back-off (`PREVIEW_BUSY_BACKOFF_S`, 0.25 s) and
  live view is only given up after `PREVIEW_GIVE_UP_S` (15 s) of busy with no
  frame ever (it was 5 answers, which could give up before the camera was
  ready).
- Exposure is read with `get_single_config` per setting, not the whole
  configuration every 3 s, which stalled the picture.

## 17. Shooting while live view runs (test 4, 2026-10-27)
Test 4 asked for live view straight after connecting: the first frame came in
0.08 s with no busy answer, then 50 fps in a tight loop. So live view is
neither refused nor slow on a fresh connection. The trigger call straight
after it failed with `[-1] Unspecified error`. libgphoto2 2.5.34's source
explains both findings:
- `camera_trigger_capture`'s Fujifilm branch (AF request 0xD208 = 0x0200,
  Initiate Capture, poll AFStatus, Shoot 0xD208 = 0x0304, Initiate Capture)
  never ends live view. `camera_fuji_capture`, behind the full capture call
  `gp_camera_capture`, starts with
  `if (params->inliveview) ptp_terminateopencapture(...)`, then sets
  PriorityMode, shoots and waits for the new object. So with live view
  running, the full capture call is the one to use.
- `camera_wait_for_event`'s Fujifilm branch writes PriorityMode = 1 and lists
  the object handles on EVERY call (the body sends no ObjectAdded events, so
  libgphoto2 makes them up from handles it hasn't seen). FreeCCR checked
  events between every frame, which explains its ~3 fps against 50.
Changes:
- `Gphoto2Backend.fire()` uses `camera.capture(GP_CAPTURE_IMAGE)` when live
  view has been ASKED for since the last shot (`_live_asked`, set before
  `capture_preview` because a request that starts live view and then fails
  leaves it running). The returned file goes into the shot's pending group
  like an event's, so a RAW + JPEG pair still keeps the RAW. A file announced
  again by an event in the same group is downloaded once; the guard is per
  group, since some bodies name every shot alike (`capt0000.jpg`). Not
  supported (-6) falls back to the trigger call. With no live view since the
  last shot the trigger call stays: it is the one the GFX 50S II passed (§15)
  and it doesn't hold the worker. `stop_live()` leaves `_live_asked` set: a
  Fujifilm's live view only ends with the next capture call. The full call
  blocks the worker for the exposure, which is fine for scanning exposures;
  one longer than the sequence's 30 s file timeout would time out.
- `poll()` checks events about once a second (`LIVE_EVENTS_EVERY_S`) while
  live view was asked for in the last `LIVE_RECENT_S` (0.5 s) and no shot is
  on its way. After `fire()` (until the shot's group is in, or `SHOT_WAIT_S`,
  30 s) and with no live view it checks every turn as before. A file from the
  camera's own button is still found, at most a second late. `stop_live()`
  ends the throttle at once.
- The worker pauses `IDLE_LIVE_S` (20 ms) in a turn with no frame and no
  events, since a backed-off preview and a throttled check both return at once.
`stop_live()` now reads only the `viewfinder` setting (`get_single_config`),
not the whole configuration. Test 5 (`gfx50sii_test_v5.py`) is to confirm
this on the body: frame rate with no event checks, one before every frame,
and one a second; then two full-call shots with live view running, and how
soon live view comes back after each.

## 18. Test 5 results (2026-10-27)
On the GFX 50S II (same setup as §15):
- Live view straight after connecting: first frame after 0.10 s, then
  49.9 fps with no event checks; 3.4 fps with an event check before every
  frame (FreeCCR until §17); 36.1 fps with one a second. So each Fujifilm
  event check costs the picture ~0.3 s, and `LIVE_EVENTS_EVERY_S` is now
  3 s (about 45 fps, with a short stall every 3 s).
- The first event check announced `/store_10000002/LIVE0001.JPG`: the live
  view frame itself, on a store of its own (libgphoto2 skips only handle
  0x80000001 and deletes the frame once it has fetched it). FreeCCR would
  have downloaded it into the hot folder or, with none, reported a shot
  with nowhere to go. `is_live_view_file` (`LIVE` + digits, `.jpg`) drops
  such files at the event. A download that fails now counts towards a lost
  camera through `_failed` (no USB = lost at once; otherwise the next
  successful call resets the count) and skips that file, rather than
  declaring the camera lost on one file that vanished.
- The full capture call with live view running WORKS (the §17 fix): 3.67 s
  and 3.63 s to return the file, then 0.05 s to "download" it, as
  `add_objectid_and_upload` already fetched it inside the call. About 3.7 s
  a frame, as before.
- After a shot, live view stays busy (116 and 115 busy answers in 30 s), yet
  the second shot worked. Test 6 (`gfx50sii_test_v6.py`) tries what might
  bring it back: deleting the shot from the camera once it is saved (the
  body's store holds each shot it took; its numbering restarted at
  DSCF0001, which suggests a tether buffer rather than the card), then
  reconnecting.

## 19. Test 6: what keeps live view busy after a shot (2026-10-27)
Both of the body's stores were EMPTY before the shot (no DCIM, nothing left
from test 5, so a tether buffer, not the card, cleared when the body is
switched off). After the shot live view was busy (20 answers in 5 s); after
`file_delete` of that shot, the first frame came in 0.07 s, and the
disconnect went through without the `-110` it had given in every earlier
test. So the camera holds each shot until the computer deletes it, and is
busy for live view and for handing control back until then.
Decision (the user, 2026-10-27, revised the next day in §20): FreeCCR deletes NOTHING from the camera.
So on this body live view stops after the first USB shot of a session and
comes back only once the camera is switched off and on; shots keep working
(test 5 took two). Not known: how many shots the buffer holds before it
refuses another.

## 20. Clearing shots from the camera's memory (2026-10-28, the user's decision)
In use the monitor's USB picture froze on the last frame after a shot while
the body's LED kept cycling green/orange as if still transferring: in this
protocol the computer ends a transfer by deleting the shot, and until then the
body counts it as unfinished (test 6). The user agreed, on the basis that
libgphoto2's own command line does the same by default after capturing and
downloading (`--no-keep`, which it applies even to card files), to clearing
shots from the camera's OWN memory:
- **When**: in `Gphoto2Backend._download`, after `os.replace` puts the whole
  file in the hot folder, and only if `_saved_whole`: the size on disk equals
  the bytes received and, when the camera reports a size
  (`file_get_info(...).file`, `GP_FILE_INFO_SIZE`), that size too. Not when
  there is no hot folder (the shot is "skipped") or the download failed.
- **What**: only folders that aren't on a memory card, `on_memory_card` =
  any path part is `DCIM` (the DCF layout every card uses; the Fujifilm
  tether buffer is `/store_10000001` with files at its top). A camera that
  records to a card keeps every file. The other half of a RAW + JPEG pair
  (dropped by `keep_raw`) is cleared too, but only once its RAW was saved
  whole: the body would otherwise keep holding it. In tether mode it would
  be gone at switch-off anyway.
- **If it fails**: printed, nothing lost; the body stays busy.
- **Saying so**: `Gphoto2Backend.live_stalled()` is true when live view has
  given frames and then answered busy for `LIVE_STALL_S` (5 s); the worker
  emits `live_paused(bool)` on each change while its live view is on, and the
  monitor shows "Live view is paused: the camera is still holding the last
  shot. Switching the camera off and on brings live view back." over the
  picture when that camera gives the picture (the next frame clears it).
Tests: the fake body holds every shot and answers live view busy until it is
deleted; card folders are never deleted; nothing is deleted unless saved
whole (size mismatch, no hot folder, failed download); a pair's JPEG goes only
after its RAW; the stall is reported only after frames; the real libgphoto2
virtual camera keeps its DCIM file and, with the card rule lifted, has it
deleted through the real size check and `file_delete`.
