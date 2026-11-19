# The film canister's menu, the film in the window title, Write Preset

The user, 2026-11-19: "When clicking the new preset button, can we have a
dropdown box that shows presets, and film format (as transfered from
capture). There could be edit box at the very bottom that brings up the
settings page. The write to preset button can just be write preset. The box
is too big. … Can the film name be visible in name of the window. So like
FreeCCR    XP2 35m".

## 1. The canister opens a menu (`ImagePreview.open_film_presets`)

The toolbar's film canister no longer jumps straight to Settings. It drops a
menu under the button (`ImagePreview.film_menu()`, built fresh each time, the
test seam):

- **Film** (a disabled heading), then **None** and every film preset in the
  Settings order (`film_presets.ordered`), checkable, the roll's ticked. A
  roll whose film was typed rather than picked shows that name too, ticked
  and inert.
- **Format** (a heading), then **Not set** and every film format
  (`film_formats.FILM_FORMATS`, their labels), the roll's ticked.
- a line, then **Edit Film Presets…** (Settings → Film Presets, what the
  button did before).

"The roll's" is `MainWindow.capture_session`, which the Start Hot Folder
Tether window fills (Roll / Format / Film). Picking here changes the same
session (`MainWindow.set_roll_film(pid)` / `set_roll_format(key)`, both
through `_set_capture_session`), so:

- Capture follows at once (`apply_roll_preset` picks the preset's film base,
  `apply_roll_format` reshapes the frame guide), new captures carry the
  preset (`img.film_preset`, its look on the roll's first frame) and the
  capture log and file names use its film;
- the Start window offers it next time (the same `capture/preset` and
  `capture/format` settings);
- with no session yet (no tethering since FreeCCR opened), picking creates
  one on the roll last used (`capture/last_roll`, unchanged, so the Start
  window still offers the next roll).

Nothing is applied to frames already open: that stays Write Preset's
right-click (Apply Preset to This / the Selected Frames).

## 2. The film and format in the window title (`MainWindow._update_title`)

`FreeCCR · <film> · <format>`: the preset's label (`Portra400 V1
(Lisbon 2026)`) or the typed film, and the format's short name
(`film_formats.short_label`: 35mm, Half frame, XPan, 6x4.5, 6x6, 6x7, 6x8,
6x9, 6x12, 6x17, 4x5, 8x10). Either part may be missing; with neither it is
plain `FreeCCR`. Updated whenever the session changes (Start window, New
Roll, the canister menu, a preset renamed or deleted in Settings).

## 3. Write Preset

The button beside Colour Profile reads **Write Preset** and is as wide as
its words (it stretched over the rest of the row); the row ends in space.
Its tooltip, right-click and window are unchanged.

## Tests

- The menu lists None + the presets with the roll's ticked, Not set + the
  formats, and Edit Film Presets… last; picking a preset / a format sets the
  session (and Capture's), and Edit opens Settings at Film Presets.
- With no session, picking creates one on the last roll without moving it.
- The title reads `FreeCCR · Portra400 V1 · 35mm`, `FreeCCR · 6x7`,
  `FreeCCR`, and follows a preset's rename.
- The button reads Write Preset and is no wider than its words need.
