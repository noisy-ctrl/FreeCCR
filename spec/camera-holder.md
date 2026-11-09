# Which program is holding the camera

Status: specified and built 2026-11-01.

The user (2026-11-01): "Is there any safe way the software can tell you what
other software is holding the camera?"

## Goal
When FreeCCR can't open a USB camera because another program has claimed it
(libgphoto2's `GP_ERROR_IO_USB_CLAIM`, `CameraBusy`), name that program and
its process number over the picture, so the user knows what to quit. Read
only: FreeCCR never ends another program. The existing **Release Camera**
button (it ends macOS's own camera service, after asking) stays, and is
offered only when the holder is that service or can't be told.

Non-goals: Windows (USB cameras go through digiCamControl there, which
reports its own errors); watching for the holder to quit (Find Cameras or
Release Camera retries, as now).

## How the holder is found (`core/camera_holder.py`, Qt-free)
- **macOS**: `ioreg -r -c IOUSBHostDevice -l -w 0` (Apple's own read-only
  tool for the I/O Registry; nothing is opened or changed). A program that
  claims a USB device for itself is recorded on the device or its interface as
  `"UsbExclusiveOwner" = "pid 512, ptpcamerad"`. The output is split into one
  section per `IOUSBHostDevice` (its interfaces follow it), each with
  `idVendor`, `idProduct`, `USB Product Name`, `locationID`, `USB Address` and
  its owners. The camera's section is chosen by, in order: libgphoto2's port
  `usb:BBB,DDD` = (`locationID >> 24`, `USB Address`) (libusb's own numbering on
  macOS); the product name sharing a word with the model ("GFX 50S II"); a
  camera maker's USB vendor id. One section with an owner and nothing else to
  go on is taken as it; otherwise nothing is claimed.
- **Linux**: the port's device file `/dev/bus/usb/BBB/DDD`; any process whose
  `/proc/<pid>/fd/*` points at it, named from `/proc/<pid>/comm` (other users'
  processes it may not read are skipped). The usual one is the desktop's
  `gvfs-gphoto2-volume-monitor`.
- `find(port, model)` → a list of `Holder(pid, name, system)`; `system` is
  True for macOS's camera service (`ptpcamerad`, `PTPCamera`, `mscamerad`,
  `Image Capture`, `Photos`) and the GNOME volume monitor. Any failure (no
  tool, timeout, unreadable output) → `[]`, never an exception.
- `describe(holders)` → the line shown: `"ptpcamerad" (process 512), macOS's
  own camera service` / `"<name>" (process N)`. FreeCCR shows the name the
  system reports; its own text names no other product.

## Integration
- `UsbCamera._open`: on `CameraBusy` it runs `camera_holder.find` on the
  worker thread (~0.1 s, only on a failed open) and keeps the result in
  `UsbCamera.holders` before emitting `state("busy", …)`.
- Monitor (`_on_usb_state`, kind "busy"):
  - a holder that isn't the system's: `"<name>" (process N) is using the
    camera, so FreeCCR can't open it. Quit it (or close its tether window),
    then Find Cameras.` No Release Camera button.
  - the system's camera service: as before, naming it with its process
    number, and Release Camera (macOS).
  - none found: the old messages.

## Tests (`tests/test_camera_holder.py`)
ioreg parsing on a sample with a webcam held by another service, a hub and
the camera held by ptpcamerad: matched by bus/address, by product name, by
vendor; nothing claimed when two unknown devices are held; a failing or
missing `ioreg` → `[]`. Linux: a fake `/proc` with a process holding the
port's device file. `UsbCamera` keeps the holders on a busy open. The monitor
names a non-system holder and hides Release Camera; names ptpcamerad and
shows it.


## Revision 2026-11-07: never another device's owner, never FreeCCR itself
The user saw "“python” (process 6008) is using the camera" while Image
Capture had it. Image Capture's hold shows no `UsbExclusiveOwner` on the
camera's entry, so the camera wasn't among the HELD devices, and the
"one held device and nothing else to go on" rule took the only held device:
one FreeCCR itself (python) had open.
- The port names the camera's entry outright, held or not; a camera entry
  with no owner gives `[]` (a holder we can't see). Another device's owner is
  never reported.
- FreeCCR's own process (`os.getpid()`) counts only when nobody else holds
  the camera's entry: `Holder(own=True)`, "FreeCCR itself (process N)"; the
  monitor says "FreeCCR still has the camera from an earlier connection.
  Unplug the camera's USB cable and plug it in again, then Find Cameras." (no
  Release Camera). Without a port, devices only FreeCCR holds don't count.
- The lone-held-device fallback needs a camera service as its owner.
- On macOS the messages name Image Capture, Photos and Preview: "If Image
  Capture, Photos or Preview is open, quit it", with or without a holder
  found ("Another program has the camera, so FreeCCR can't open it." when
  none is).

## 2026-11-09

The button is now **Release Camera from Other Apps** (the user's wording); its
question's title follows. Behaviour unchanged.
