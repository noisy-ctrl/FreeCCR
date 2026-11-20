# Flat fields: what a frame got, and the camera and lens they were shot with

The user, 2026-11-20: "In the film dropdown box, can we have some kind of
indication that a flat field correction has been applied? In the flat field
data, can we store info of lens and camera (both from metadata if it exists
or manually). In my case, everything has been with my GFX 50sii and a
Schneider-Kreuznach MAKRO-SYMMAR 5.6/120."

## Goals

1. The film canister's menu says which flat field the frame on screen was
   decoded with (or none), whether it followed the carrier's opening, and
   when that is out of date (a different flat applies on re-grading).
2. Its Format and Film entries are marked where they bring a flat: a format
   with a flat for the roll's light in the library, a preset that names one.
3. Every flat records the camera and lens it was shot with: from the shot's
   metadata where it has them, typed where it hasn't (an adapted lens records
   nothing) or where you'd rather name it. Typed wins.
4. Typing them is possible when shooting (Capture → Shoot Flat Fields…), in
   the field-correction wizard, and afterwards for every flat at once
   (Settings → Field correction → Camera and Lens…).

## Non-goals

- Choosing a flat by camera or lens (a flat is still chosen by light,
  format and the carrier's opening; spec/flat-format-match.md). A later step
  could prefer the frame's own camera.
- Saving what a frame was decoded with in the catalogue: every decode sets
  it again.

## 1. What the decode applied

`CCRImage._apply_field_correction` keeps `img.flat_applied`: None (no flat)
or {"name", "id" (content id), "light", "format", "camera", "lens",
"carrier": "moved" | "same" | "not found" | None} — None when the flat has no
opening of its own, "not found" when the frame's opening wasn't found or the
move was refused (`flat_field.track_state(profile, opening)` →
(move, state); `track` is its first half). Set by every decode, so it is what
the frame on screen shows.

## 2. The film menu

After Format, before Edit Film Presets…: a "Flat field" heading and, for the
frame on screen (`ImagePreview.current_idx`), disabled lines:

- `Frame N: White light flat · 6x4.5` (`flat_field.flat_label`: the light's
  label, "flat", the format's short label; else the flat's name) or
  `Frame N: no flat field`;
- what else is known, joined by " · ": `followed the carrier` / `the carrier
  wasn't found: applied as shot`, the camera, the lens;
- when the frame's grading signature differs from what a decode gives now
  (`profile_signature != ccr_backend.active_profile_signature(img)`):
  `⚠ Re-grade to apply <the new flat or "no flat">: right-click its thumbnail`.

With no frame: `No frame open`. A frame decoded before this (no
`flat_applied` attribute) reads from `flat_field.profile_for(img)`.

Format entries whose format has a flat of the roll's light (trichrome →
RGB, else white) carry "flat field" in the shortcut column (`text\tflat
field`, right-aligned, Qt's own); preset entries naming a flat
(`film_presets.flats_map`, not "none") too.

## 3. Camera and lens

- Data: `meta["camera"]` stays the EXIF block (`source_metadata`); typed
  names are `meta["details"]` = {"camera": str, "lens": str} (empty strings
  dropped, the key dropped when both are empty).
- `flat_field.camera_name(p)` = typed, else EXIF make + model (the maker's
  name not repeated when the model starts with it); `lens_name(p)` = typed,
  else the EXIF lens label (`_lens_label`), else "".
- `summary()` (the Settings line) uses them.
- `flat_field.set_details(p, camera, lens)` writes the details and saves the
  file, **keeping its content id** (`meta["content_id"]`, which
  `load_profile` and `save_profile` prefer to the file's hash): naming a lens
  must never flag frames for re-grading or change which flat applies. A
  re-shot flat is a new profile with no pinned id.
- Remembered typed names (`flat/camera`, `flat/lens`) fill the fields of the
  next Shoot Flat Fields / wizard, and are written whenever a flat is made
  with them.

### UI

- **Shoot Flat Fields…**: Camera and Lens fields under Format (placeholders
  "From the shot's metadata" / "None in the metadata? Type it"), prefilled
  with the remembered names; `values()` gains "camera", "lens"; they reach
  `build_flats(..., details=)`.
- **Wizard** review page: Camera and Lens under the name, prefilled with the
  remembered names, the shot's EXIF as placeholders.
- **Settings → Field correction → Camera and Lens…**
  (`widgets/flat_details_dialog.py`): a table of every flat in the library
  (Flat, Camera, Lens; the EXIF value as each field's placeholder), "Same
  for All" copying the first row's typed names to every row, Save writing
  each changed flat with `set_details`. The test seam is the dialog's
  `values()` / `save()`.

## Tests (`tests/test_flat_field_info.py`)

- `flat_applied` after a decode: none; a flat with its light/format/carrier
  state; untagged profile (carrier None).
- `track_state`: moved / same / not found / no opening.
- The film menu's flat lines: applied, none, out of date, no frame; format
  and preset marks; existing lookups by label still work with the mark.
- Camera / lens: EXIF only, typed wins, both; `summary`.
- `set_details` keeps the content id across a reload, so the frame's
  signature doesn't change; a re-built flat gets a new id.
- Shoot Flat Fields passes typed names into the saved flats and remembers
  them; the wizard's fields too; the Camera and Lens dialog: Same for All,
  Save writes only changed flats.
