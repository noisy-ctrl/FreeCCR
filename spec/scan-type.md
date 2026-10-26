# Spec: Scan type (colour, trichrome, black and white)

Status: DRAFT v1 for review (2026-10-26). No code yet.
Owner: FreeCCR
Feature branch: `feature/scan-type`

## 1. Summary

One app-wide setting says what is being scanned: **Colour** (one frame under
white light), **Colour, trichrome** (three frames under red, green and blue,
merged into one) or **Black & white** (one frame, rendered grey). The Live
Capture Monitor shoots accordingly, and Hot Folder Tether loads the frames
accordingly: a trichrome scan arrives as ONE merged colour frame per shot,
not three single-colour frames; a black-and-white scan arrives already set to
Black & White. Each frame remembers how it was scanned.

## 2. Goals / Non-goals

### Goals
- One setting, `scan_type` = `colour` | `trichrome` | `bw`, persisted
  (QSettings `scan/type`, default `colour`), the same value wherever it is
  shown.
- The monitor's Capture section: a **Scanning** choice replaces the
  Trichrome checkbox; Shoot follows it.
- Hot Folder Tether follows it while running: trichrome files are grouped
  into triplets and merged as they arrive; black-and-white frames get the
  Black & White colour profile.
- Each loaded frame records `capture_type` (catalog key, additive), so later
  steps can tell a trichrome merge from a white-light frame.

### Non-goals
- Detecting the scan type from the files by itself (a red-lit frame is
  recognisable, a B&W negative under white light is not reliably). The user
  says what it is; a check only warns (§5.3).
- Changing how Open / Open Folder load (3-way merge there stays its own
  Settings toggle).
- Changing the conversion maths. A trichrome merge, a white-light frame and
  a B&W frame convert exactly as they do today.

## 3. UX

### 3.1 Where it is chosen
- **Live Capture Monitor → Capture**: a **Scanning** combo: Colour · white
  light / Colour · trichrome (R → G → B) / Black & white. It replaces the
  Trichrome checkbox (trichrome = the old checkbox on).
- **Hot Folder Tether**: starting it (File menu, the monitor's prompt or the
  monitor's button) shows the current choice in the folder picker step's
  question box ("Scanning: Colour · trichrome. Change…"), and the banner
  shows it with a small combo ("Hot folder tether: “Roll 12” · 7 captured ·
  Scanning [Colour · trichrome ▾]") so it can be changed mid-session.
- Changing it anywhere changes it everywhere (one setting).

### 3.2 What each type does

| | Shoot (monitor) | Light between shots | Hot Folder Tether | Frame records |
|---|---|---|---|---|
| Colour · white light | one shot under the preset's White | White | each file one frame | `capture_type = "colour"` |
| Colour · trichrome | R, G, B shots, then White | White | each three files one merged frame | `"trichrome"` |
| Black & white | one shot under the preset's White | White | each file one frame, Color Profile Black & White | `"bw"` |

The monitor's Negative preview turns its Black & white box on for a B&W
scan.

## 4. Data model
- `ccr_backend.scan_type` (str), QSettings `scan/type`.
- `CCRImage.capture_type`: `"colour"`, `"trichrome"`, `"bw"` or None
  (unknown: anything loaded by Open, or before this feature). Saved in the
  catalog (additive key), copied with the image like `film_format`.
- No other stored state changes.

## 5. Hot Folder Tether in trichrome

### 5.1 Grouping
New RAW files are queued in name order (the same order Open's merge uses,
`ccr_merge.sort_for_merge`). Whenever three are waiting they are taken as one
(R, G, B) triplet and loaded with `catalog.create_images_for_merge` (the
merge Open uses, with the current Merge detail setting), then converted the
same way a single tethered frame is (the session's black point, if set).
- The banner counts shots, and while a triplet is part-way says so: "2 of 3
  frames of the next shot".
- A JPEG-only arrival can't be merged (merging reads the RAW): it is loaded
  as a single frame and the banner says why.
- "Import existing files" at the start: in trichrome they are grouped the
  same way; a count that isn't a multiple of three asks whether to load the
  leftover as single frames or leave it.

### 5.2 A shot that went wrong
If a triplet stays incomplete for more than 30 s after its last file (a
missed shot), the banner offers **Load as single frames** and **Keep
waiting**; nothing is guessed.

### 5.3 The colour check
Before merging, each frame's brightest channel is checked on a quick
half-size read: a red-lit frame is red-dominant, and so on. A triplet that
isn't red, green, blue in that order (a stray white frame, one shot missed
and the next started) is NOT merged: the banner says which frame looks out
of place and offers **Merge anyway** / **Shift by one** / **Load as single
frames**. (This is the only place the files' content is read for type.)

### 5.4 Switching type mid-session
A part-filled triplet when switching away from trichrome is loaded as single
frames. Switching to trichrome starts grouping from the next file.

## 6. Black & white
Arriving frames get `color_profile = "bw"` (the existing Black & White render:
grey preview, thumbnail, export, single-channel files). Nothing else changes;
the existing tether template already copies a previous frame's profile, and
`bw` now also applies to the first frame of a session.

## 7. Where `capture_type` is used (now and later)
- Shown in the frame's info and the contact sheet's caption ("trichrome").
- **Later, separately (open question 4):** a camera profile or Film Look
  made for one capture type warns when used on another (camera profiles
  already record white-light vs trichrome; a Film Look fitted on trichrome
  merges doesn't describe white-light frames).

## 8. Integration points
- `core/ccr_backend.py`: `scan_type`, persisted with the other import
  settings.
- `core/tether_watcher.py`: a `TripletGrouper` (Qt-free: feed names, get
  triplets, leftovers, timeouts) and the colour check; the worker's
  `process` gains `process_triplet(paths)` using `create_images_for_merge`.
- `ui/main_window.py`: tether dispatch by type; the start question; banner
  combo; `_on_capture` sets `capture_type` / `color_profile`.
- `widgets/tether_banner.py`: the Scanning combo and the part-triplet /
  out-of-place messages with their buttons.
- `widgets/live_monitor.py`: Scanning combo in Capture (replaces Trichrome),
  Negative preview B&W for `bw`, kept in step with the banner.
- `core/catalog.py`: `capture_type` saved and restored.

## 9. Test plan
- `TripletGrouper`: name order, triplets as soon as three wait, leftovers,
  the timeout, switching type part-way.
- The colour check on synthetic RAW-like frames: R, G, B passes; a white
  frame or a shifted order is caught; Shift by one regroups.
- Tether worker with real small RAW-less fixtures (as the existing tether
  tests do) via a stubbed merge creator: one merged frame per triplet,
  converted with the session black point; JPEG-only loads single.
- B&W: arriving frames are `bw`; colour frames are `color`.
- `capture_type` survives a catalog save and load; unknown for Open.
- Monitor: the Scanning combo drives Shoot (one shot vs three) and stays in
  step with the banner; old `live/trichrome` setting migrates to the combo.

## 10. Open questions
1. Is the combo on the Hot Folder Tether banner the right second place, or
   should the type only be chosen in the monitor?
2. A triplet that fails the colour check: pause and ask (as above), or just
   load the three as single frames and say so?
3. Black & white: always under White, or also offer a single colour (Green)
   for B&W negatives?
4. Profile / Film Look mismatch warnings by capture type: now, later, or
   not at all?
