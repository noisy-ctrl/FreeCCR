# Film presets

The user, 2026-11-19: "Can we slightly rethink film-specific presets and how
they carry over the whole workflow? With a manager in a new tab in Settings
maybe. Film base settings in Capture; the initial dialogue when opening
Capture; a LUT if there is one; slider settings in the main window."

He also asked for:

- a small push/pull list (−2, −1, 0, +1, +2; 0 the default) beside the film
  base's name, where a different value makes a separate ID;
- a **Write to Preset** button right of Colour Profile, with that list made
  narrower;
- an ID made of `[Film][speed] [push/pull] V[n] (notes)`, for example
  "Portra400 V1", "Portra400 V2 (Barcelona 2022)", "Colorplus200 +1 V1",
  "Colorplus200 +1 V2". The film format is not part of it.

His choices: a roll's first capture starts from the preset and later
captures carry from the frame before, as now. Chemistry or lab differences
go in the version and notes, with no field of their own.

## 1. What a film preset is

One record per ID (`core/film_presets.py`, Qt-free; stored as JSON in
QSettings `film/presets`):

| key | |
|---|---|
| `id` | stable, random (12 hex). Frames, sessions and film bases point at it, so renaming never breaks a link. |
| `film` | the film and speed as typed, e.g. `Portra400`, `XP2`. |
| `push` | −2 … +2, 0 = box speed. |
| `version` | 1, 2, … |
| `notes` | free text, optional ("Barcelona 2022", "home kit"). |
| `when` | date made. |
| `clip` | the slider settings, in Copy Settings' clipboard form without the geometry (crop, rotation, flips, fine rotation): adjustments, curves, Cineon, Film Look, the tools' options, flags, colour profile. None until written. |
| `chosen` | which of the clip's rows the preset carries (paste row ids). |

- **ID** = (film case-folded, push, version); two presets never share one.
- **Label** = `film`, then ` +1` / ` −2` when pushed or pulled, then
  ` V<n>`, then ` (notes)` when there are notes: "Colorplus200 +1 V2".
- The **Film Look** is the clip's `look_lut` (with its colour-only / balance
  marks) and is listed when chosen. Setting a look in the manager writes it
  there; a preset may carry only a look.
- A preset may carry none of these and still name the roll.

## 2. Film bases belong to presets

- A film base gets `preset` = a preset's id (optional; older ones have none
  and work as before). Its `name` is the preset's label, kept in step when
  the preset is renamed.
- One preset can hold one film base per scan type: trichrome, white light,
  B&W. So film-base names are now unique per **overlapping scan type**, not
  across the whole list: "Portra400 V1" may be both a trichrome and a
  white-light base. An old one-exposure base (no scan recorded) overlaps both
  white light and B&W.
- **Measure** (new film base) asks for the ID in a small window
  (`widgets/film_preset_id_dialog.py`): Film (an editable list of the films
  already used), Push/pull, Version, Notes, with the resulting label shown
  live. It starts from the roll's preset (the Start window's choice) when it
  has no base of this scan type, else the next free version. An ID that
  exists links to that preset; a new one makes it.
- **Edit Film Base**: a push/pull list right of the Name (the user's mock-up),
  and a line under it with the preset's full label. Name and push/pull are
  the preset's film and push:
  - **Save**: a changed film or push moves this base to that ID's preset
    (made if needed, same version). When this base was the old preset's only
    content (no other base, no settings) the old preset is renamed instead,
    so nothing is left behind.
  - **Save as New**: a new film base on the next free version of that film
    and push, which starts with the old preset's settings and look (notes
    left empty).
- Older, unlinked film bases: Edit links them on Save. The manager lists
  them and makes presets from them.

## 3. Start Hot Folder Tether

- **Film** lists the presets by label (newest first), and you can still type
  a plain film name. Picking a preset sets the session's `preset`; typing
  clears it.
- File names take the preset's film and push: `Portra400`,
  `Colorplus200Push1`, `HP5Pull1` (`film_presets.file_film`; `+` would
  vanish from a file name). Version and notes are not in file names.
- With a preset, Capture selects that preset's film base for the scan type
  (`LiveCaptureMonitor.apply_roll_preset`). When there is none, the caption
  says so ("No trichrome film base for Portra400 V1 yet: Measure to add
  one").

## 4. Captures

- Each capture in a roll with a preset records it (`img.film_preset`, a
  catalogue key, additive).
- The **first** capture starts from the preset: when the frame on screen is
  not a frame of that preset, the look the next capture inherits is the
  preset's (`MainWindow._tether_template`): its settings merged on defaults
  exactly as Paste does (`sliders_panel.paste_into`), its colour profile, no
  crop, rotation or areas. After that each capture inherits from the frame
  before, as now.

## 5. Main window

- The **Colour Profile** row: the list is narrower (`PROFILE_COMBO_W` 104 px)
  with **Write to Preset** right of it.
- **Write to Preset** opens a window (`WritePresetDialog`): which preset
  (the frame's own, else the roll's, else the first; "New Preset…" asks for
  an ID) and the frame's settings as Paste lists them, all ticked except
  Temperature and Tint (set per frame by auto white balance; tick them to
  keep them). Write replaces the preset's clip and chosen rows and marks the
  frame as that preset's.
- Its right-click menu: **Apply Preset to This Frame…** / **to the N Selected
  Frames…** (pick a preset, then Paste's list of what it carries, all
  ticked; one undo step a frame; the frames are marked as that preset's),
  and **Manage Film Presets…** (Settings at that page).
- The tooltip names the frame's preset.

## 6. Settings → Film Presets

A page in Settings (changes are saved as they are made, not on Done):

- The list of presets (label; a dot for each scan type with a film base).
- For the selected one: Film, Push/pull, Version, Notes (renaming relabels
  its film bases; an ID already taken is refused with a note); Film Look
  (the looks library, "None"); "Slider settings: N settings · written
  <date>" with **Clear**; film bases by scan type with their exposure
  summary (measured in Capture).
- Buttons: **New**, **New Version** (a copy on the next free version, film
  bases not copied), **Delete…** (asks; its film bases are kept, unlinked;
  frames keep their mark and show "(deleted)").
- "Film bases with no preset": **Make Presets** makes one per base from its
  name (a trailing ` +1`, ` −1`, ` V2` are read as push and version).
- Film-base changes go through the main window
  (`MainWindow.film_bases_data` / `set_film_bases_data`), which reloads an
  open Capture, so its in-memory list never overwrites them.

## 7. What it doesn't do

- No format in the ID (the roll's Format sets the guide, as now).
- Process/chemistry has no field (version + notes).
- A preset's settings never change a frame by themselves after it was
  captured or applied: only Apply does.

## Tests

- IDs and labels; uniqueness; next version; file-name film; parse a label;
  clip summary; normalise drops broken records.
- Film bases: same label allowed for trichrome and white light, refused for
  two of one type; relabel; links kept through Measure Again / Edit / Rename.
- Capture: Measure with an ID links or makes the preset; Edit with a new push
  moves the base (renames a preset with nothing else); Save as New takes the
  next version and copies settings; `apply_roll_preset` selects the base or
  says none.
- Start window: lists presets; picking one gives `preset` and the file film;
  typing clears it.
- Captures: the first takes the preset's settings, the next the frame
  before's; `film_preset` is saved in the catalogue.
- Write to Preset: writes the ticked rows; Temperature/Tint unticked by
  default; Apply pastes onto selected frames with one undo each.
- Settings page: edits, refuses a taken ID, New Version, Delete unlinks,
  Make Presets.
- `paste_into` gives exactly what `_apply_paste` gave before (refactor).

## Refinements (before building)

- **Rename…** on a linked film base (Capture's menu) opens the ID window and
  renames the preset itself (all its film bases follow); an ID already taken
  is refused. On an unlinked one it is the old plain rename.
- **Delete** on a linked film base removes only that base; the preset stays.
- The seam `_ask_film_base_name(default)` stays: it may return a plain name
  (the old behaviour: an unlinked base with exactly that name; the tests
  use it) or an ID dict (`film`, `push`, `version`, `notes`), which links.
  The real window returns an ID dict. `_edit_film_base_dialog` likewise
  returns its values with an optional `"_id"`; without one, Save / Save as New
  behave as before.
- "First from the preset": the template is the preset's when the frame on
  screen doesn't carry the session's preset (`img.film_preset`), so a roll's
  first capture, and a capture made while some other film's frame is on
  screen, start from the preset; a frame of the same preset carries.
- Storage: presets in QSettings `film/presets` (JSON list); film bases stay in
  `live/film_bases`.
- Names in file names: `film_presets.file_film(p)` = film + `Push1` / `Pull2`
  (nothing at 0).
