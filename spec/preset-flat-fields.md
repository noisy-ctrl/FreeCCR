# Flat fields in a film preset

Status: BUILT 2026-11-20.

The user, 2026-11-20: "Can we add flat field correction in edit film
preset. Multiple presets can share the same flat field. Maybe flat fields
can be stored in a separate folder."

## 1. Goals / non-goals

- A film preset can name the flat its frames are corrected with, one per
  scan type (RGB / White light / B&W), as it holds a film base per scan type.
- Flats stay files in the field-correction library (their own folder,
  `<app data>/FreeCCR/field_profiles`, beside `camera_profiles` and `looks`);
  a preset only POINTS at one, by file name, so any number of presets share
  one flat, and re-shooting it (Shoot Flat Fields writes the same file name)
  updates every preset that uses it.
- Default **Automatic**: the per-frame choice by light and format that
  already exists (`flat_field.profile_for`, §5 of
  `spec/ir-dust-and-flat-sets.md`). **None** turns correction off for that
  preset's frames of that scan type.
- Non-goals: a flat embedded in the preset; IR flats per preset (the IR flat
  is for the dust search and follows the format automatically); moving the
  library (it already has its own folder; Settings gets a Show Folder button).

## 2. UX

Settings → Film Presets, under Film Look: **Flat fields**, three rows,
RGB / White light / B&W, each a list:

- `Automatic (by light and format)` (stored: nothing)
- `None` (stored: `"none"`)
- the library's flats that fit that scan type: RGB → flats lit `rgb`; White
  light → `white`; B&W → `white`, `red`, `green`, `blue`; plus untagged
  (wizard-made, no light) flats in every list. Named as the library names
  them, with the tag ("RGB flat · 6x4.5 — RGB (trichrome) · 6x4.5").
- a pinned file no longer in the library shows "<file> (missing)" and the
  frames fall back to Automatic.

A change is saved at once (as every edit on the page), pushed to the
per-frame choice, and open frames whose flat would now differ are flagged ⚠
(right-click → Replace… re-grades them), as a camera-profile change is.

**Apply Preset to This / the N Selected Frames** and **Write Preset** (which
marks the frame as of that preset) re-grade the frames whose flat changes,
at once: the user asked for that preset on those frames. A preset that
carries flats but no slider settings can still be applied (it skips the
rows window and only marks the frames).

Settings → Field correction gains **Show Folder** (opens the library folder)
and names it in its help text.

## 3. Data

Preset record: `"flats": {"trichrome"|"colour"|"bw": "<file name>"|"none"}`
(a missing key = Automatic), kept by `film_presets.clean`, copied by New
Version; `film_presets.set_flat(presets, pid, scan, ref)` /
`flat_of(p, scan)` / `flats_map(presets)` ({pid: flats} of the presets that
pin any).

The per-frame choice can't read QSettings (core, any thread), so the main
window pushes the pins: `flat_field.set_preset_flats(fp.flats_map(...))` at
start and after the Film Presets page saves.

## 4. The choice per frame

`flat_field.profile_for(img)` first asks the frame's preset:

- the preset: `img.film_preset`, else a capture note (below);
- the scan type (`frame_scan`): a 3-way merge → trichrome; `img.capture_type`;
  the capture note's; a B&W colour profile → bw; else colour;
- pin `"none"` → no correction (even with an active profile); a file name
  found in the library (`flat_field.flat_by_ref`, by the file's base name) →
  that flat (tracked by the carrier opening as usual); missing / no pin →
  the automatic choice unchanged.

**Captures**: a tethered capture is decoded on the worker before
`_on_capture` marks it with the roll's preset, so the main window notes each
new capture's files with the roll's preset and scan type when it hands them
over (`flat_field.note_capture(paths, pid, scan)`, `_dispatch_to_worker` /
`_emit_triplet`, the same files `_on_capture` marks: not the ones imported at
start); the note is read by path (`file_path` or any `merge_sources`),
bounded at 2,000 entries.

A pinned flat of another format than the frame's, or whose carrier opening
has another shape, is left for the automatic choice (`pinned` → "format";
spec/flat-format-match.md, the user the same day: a carrier stands one
format up and another lying down).

## 5. Flagging per frame

The grading signature was global: any change to the library flagged every
frame. It is now per frame: `CCRBackend.active_profile_signature(img)` =
camera part + `flat_field.frame_field_signature(img)` (the id of the flat
`profile_for(img)` picks, or `none`); stamped at decode with the frame
(`_stamp_profile_signature`) and compared with the frame
(`ThumbnailList._profile_mismatch` / `refresh_profile_warnings`). So a
preset's flat change, or a flat added for 35 mm, flags only the frames whose
flat changes. `active_profile_signature()` with no frame is unchanged.

## 6. Tests

`tests/test_preset_flat_fields.py`: the record keeps / cleans / copies its
flats; a pinned flat wins for that preset's frames of that scan type and not
others; "none" turns correction off; a missing file falls back; a capture
note gives a frame decoded before it was marked its preset's flat; the
per-frame signature flags only the frames whose flat changes; the page's
three lists (filtered by light, Automatic / None, missing) save and push;
Apply Preset re-grades only the frames whose flat changed; Show Folder opens
the library folder.
