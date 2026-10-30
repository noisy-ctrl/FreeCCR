# Shutter speed and ISO from the Live Capture Monitor

Status: spec 2026-10-30 (the user's go-ahead after tests 7–9), refined once,
then built. Extends `spec/capture-routes.md` (USB cameras).

## Why
On the GFX 50S II in "USB Tether Shooting Fixed", the camera's own dials are
locked for the whole USB connection: libgphoto2 puts it in USB priority when
it connects, and that holds with or without live view (tests 7 and 9: the
dials changed neither the camera's screen nor what it reports). Asking for
camera priority instead is refused while live view runs ("Device Busy"), and
once accepted it stops live view altogether and the dials still don't
register (test 8). In USB priority the computer is meant to set the
exposure, so FreeCCR does.

What the camera accepts (tests 8–9):
- **Live view off**: shutter speed and ISO set in ~40–60 ms and read back
  correctly.
- **Live view running**: every setting is refused with PTP "Device Busy"
  (libgphoto2 reports it as "Unspecified error" for the shutter and "Bad
  parameters" for ISO), whatever is asked.
- **Ending live view**: libgphoto2 ends a Fujifilm's live view only when it
  shoots or disconnects. Disconnect + reconnect + set + first frame took
  394–700 ms (disconnect 91–382 ms, connect 147–169, set 47–63, first frame
  89–92), and the live picture followed the exposure (brightness 105 → 145 at
  one stop slower, 68 at one stop faster, back to 105).

## Goals
- Pick shutter speed and ISO in the monitor, from the camera's own lists.
- With live view off, a change goes straight to the camera.
- With live view running, a change that the camera refuses is applied by
  ending live view (a reconnect), setting, and letting live view start again:
  the picture holds its last frame for well under a second.
- A burst of changes (clicking or scrolling through values) costs one pause,
  for the last values picked.
- Never while a shot is on its way or a Shoot sequence is running: the change
  waits and goes in when it has finished, so a trichrome set is never shot at
  two exposures.
- The pickers follow the camera (the exposure readout, every 3 s), except
  while a change of yours is waiting.

## Non-goals
- Aperture (the user's lens is manual; a contactless lens reports none).
- digiCamControl (Windows) and HDMI capture devices: the row stays hidden.
- Per-colour exposure for trichrome, or automatic exposure: see the
  auto-balance idea, a separate spec if wanted.
- Remembering exposure in FreeCCR: the camera keeps its own settings.

## UX
Capture section, a new row directly under Shutter:

    Exposure  [ 1/20      ▾ ]  [ ISO 200  ▾ ]

- Shown only while a USB camera is open whose shutter speed or ISO can be
  set (a field with no choices is hidden on its own).
- Values are the camera's, in its order. ISO values the camera reports as
  negative numbers (the GFX 50S II lists `-1 … -3` after its last ISO) are
  its automatic modes and are shown as "Auto 1 … Auto 3"; everything else
  is shown as reported.
- Picking a value: the capture caption says "Setting 1/10…", then nothing
  (success shows in the readout and the picker), or a warning in the caption
  if the camera didn't take it ("The camera didn't take ISO 320: …"), and the
  pickers go back to what the camera reports.
- During a Shoot sequence the pickers stay usable; the caption says "1/10
  is set when this shot finishes".
- Tooltips say the camera's own dials don't work while it is connected over
  USB, so exposure is set here.

## Worker and backend
`UsbCamera` (the only code touching the camera):
- `set_exposure(field, value)` (GUI side): stores `field → value` in a
  latest-wins dict under the lock and wakes the worker. No command queue
  entry, so a burst is one application.
- Each worker turn, after its commands: if something is wanted, the camera
  is open and the backend has no shot on its way (`backend.shot_pending()`),
  it takes the whole dict and calls `backend.set_exposure(wanted)`, then
  emits `exposure_set(results)` and a fresh `exposure_changed(reading)`.
  Otherwise it keeps the dict for a later turn.
- After a camera opens it emits `exposure_choices({"shutter": [...],
  "iso": [...]})` (empty dict: no pickers). A lost/closed camera clears
  what is wanted.

`Gphoto2Backend`:
- `exposure_choices()`: for shutter and ISO, the first of `SHUTTER_KEYS` /
  `ISO_KEYS` that `get_single_config` returns with at least two choices;
  the key used is remembered per connection (`_exp_keys`).
- `shot_pending()`: files waiting or a shot's window open (`_pending`,
  `_shot_until`).
- `set_exposure(wanted)`: set each field directly and read it back. If any
  failed and live view has been asked since the last shot (`_live_asked`),
  `_reconnect()` (exit, a fresh `Camera` on the same port and abilities,
  `init`; the shot state, hot folder and frame count are kept, live view state
  is reset so the next preview starts it again) and set the failed ones again.
  Returns `{field: (ok, text)}`. A reconnect that fails raises `CameraLost`
  (the existing lost-camera path). Cameras that take settings during live
  view (most bodies other than Fujifilm) never pause.
- `open()` and `_reconnect()` share `_connect(ident)`.

`DigiCamControlBackend` and the test `FakeBackend`: `exposure_choices()`
returns `{}` for the former; the fake has lists, refuses while its live view
runs (like the GFX), and counts reconnects.

## Monitor
- `exposure_row` (a widget holding the label and two combos) under the
  Shutter row; `shutter_speed_combo`, `iso_combo`.
- `_on_exposure_choices(choices)` fills them (blocking signals) and shows
  the row; `_on_exposure` (the existing readout slot) also selects the
  reported values unless that field is waiting (`_exp_waiting`).
- `_on_exposure_picked(field, value)`: during a sequence, store in
  `_exp_held` and say so; otherwise `usb.set_exposure` and record it in
  `_exp_waiting`. `_on_sequence_finished` sends anything held.
- `_on_exposure_set(results)`: clears the waiting fields, captions a
  failure.

## Tests
- Backend (fake gphoto2 module): choices read from the first working key;
  a direct set with live view off (no reconnect); with live view asked and
  the camera refusing, one reconnect then a successful set, `_live_asked`
  cleared, pending shot state kept; a failure after the reconnect reported
  per field; `shot_pending`.
- Worker: a burst of three picks → one `set_exposure` call with the last
  values; nothing applied while a shot is pending, applied after; results and
  a fresh reading emitted.
- Monitor: the row hidden until choices arrive and hidden again when the
  camera closes; "Auto N" labels with raw values kept; a pick calls
  `set_exposure`; the readout moves the pickers except a waiting field; a
  pick during a sequence is held and sent when it finishes; a failure
  captions and reverts.
- Unchanged: the existing capture-routes, live monitor and tether tests.
