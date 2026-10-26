# Spec: Scan type (colour, trichrome, black and white)

Status: REFINED v2, built (2026-10-26). The user's answers are in §10.
Owner: FreeCCR
Feature branch: `feature/scan-type`

## 1. Summary

One app-wide setting says what is being scanned: **Colour** (one frame under
white light), **Colour, trichrome** (three frames under red, green and blue,
merged into one) or **Black & white** (one exposure, rendered grey, under
whatever light is used: white or a single R, G or B channel). The Live
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
(The Hot Folder Tether *banner* is the amber strip across the top of the
main window's picture while a session runs: "● Hot folder tether: “Roll 12”
· 7 captured … Stop".)
- **Live Capture Monitor → Capture**: a **Scanning** combo: Colour · white
  light / Colour · trichrome (R → G → B) / Black & white. It replaces the
  Trichrome checkbox (trichrome = the old checkbox on).
- **Hot Folder Tether**: the banner shows it with a small combo ("Hot folder
  tether: “Roll 12” · 7 captured … Scanning [Colour · trichrome ▾]") so it
  can be seen and changed without the monitor, mid-session too; the
  monitor's "Turn on Hot Folder Tether?" prompt names the current choice.
- Changing it anywhere changes it everywhere (one setting).

### 3.2 What each type does

| | Shoot (monitor) | Light between shots | Hot Folder Tether | Frame records |
|---|---|---|---|---|
| Colour · white light | one shot with the light as it is | unchanged | each file one frame | `capture_type = "colour"` |
| Colour · trichrome | R, G, B shots, then the preset's White | White | each three files one merged frame | `"trichrome"` |
| Black & white | one shot with the light as it is (white or a single channel) | unchanged | each file one frame, Color Profile Black & White | `"bw"` |

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

### 5.2 A shot that can't be merged: greyed out under the frames
A shot that can't be merged (§5.3), a triplet still incomplete 30 s after
its last file (a missed shot), files left over at the start, or frames
waiting when the session stops, is shown as a **greyed-out tile** in a
"Needs attention" strip under the frame list: its frames side by side in
grey, a short label ("Not red, green, blue", "Frames missing", "Left over",
"Merge failed") and the reason and file names in the tooltip. It is NOT a
frame in the list (list rows stay the image indices). Right-click:
**Choose Files…** (pick the red, green and blue RAWs by hand; they merge in
name order, unchecked), **Merge Anyway**, **Load as Single Frames**,
**Remove** (the files on disk are untouched). Double-click = Choose Files.
Nothing is guessed.

### 5.3 The colour check
Before merging, each frame's light is read off the RAW mosaic (black-
subtracted channel means over every fifth photosite, both ways): a frame lit
by one LED puts most of its signal in that channel. Frame k must have channel
k as its largest share and at least 0.60 of R + G + B (white light through
an orange negative leads in red at about 0.45-0.5, so it fails). A
monochrome sensor or an unreadable file isn't judged. A triplet that fails
(a stray white frame, an order shifted by a missed shot) is not merged: it
becomes a greyed-out tile (§5.2) saying which frame looks out of place. The
0.60 is a reasoned estimate, to be confirmed on real triplets.

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
- **Later (the user's choice):** a camera profile or Film Look made for one
  capture type warns when used on another (camera profiles already record
  white-light vs trichrome; a Film Look fitted on trichrome merges doesn't
  describe white-light frames).

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

## 10. Decisions (2026-10-26, the user's answers)
1. The banner (the amber strip shown while Hot Folder Tether runs) carries
   the choice too, so it works without the monitor.
2. A shot that can't be merged is a greyed-out tile: right-click to remove
   it or choose the files by hand (plus Merge Anyway / Load as Single
   Frames).
3. Black & white is simply one exposure: Shoot doesn't care which light is
   lit (white, or a single R, G or B channel), and neither does loading.
   The same goes for Colour · white light: Shoot uses the light as it is.
4. Profile / Film Look mismatch warnings: later.

## 11. As built
- `core/scan_type.py`: types and labels, `TripletGrouper`, `group_existing`,
  `shares_from_mosaic` / `light_shares` / `judge_triplet`, `thumb_bgr`.
- `core/tether_watcher.py`: `process_triplet` (check, then
  `create_images_for_merge` with the Merge detail settings, then the same
  conversion as a single frame), `describe_leftover`, `needsAttention`.
- `ui/main_window.py`: `scan_type_changed`, `set_scan_type` (QSettings
  `scan/type`; the monitor's old `live/trichrome` seeds it once),
  `_route_tether_file` / `_route_existing`, the stall check in the poll,
  `_on_needs_attention` / `_on_attention_action`, the banner's part-shot
  note, and `_on_capture` setting `capture_type` and the colour profile.
- `widgets/thumbnail_list.py`: the "Needs attention" strip
  (`set_attention`, `attention_action`).
- `widgets/tether_banner.py`: the Scanning combo.
- `widgets/live_monitor.py`: the Scanning combo replaces the Trichrome box;
  Black & white ticks the Negative preview's B&W.
- `core/catalog.py`, `core/ccr_image.py`, `core/ccr_backend.py`:
  `capture_type` (saved, restored, copied on Duplicate) and `scan_type`.
- Tests: `tests/test_scan_type.py`.
