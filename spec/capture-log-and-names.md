# Capture log and file names

The user, 2026-11-08: "Can we not have a log somewhere where captures are
recorded? … file names repeat themselves in the folder … there should be a
session id then underscore and number … it could also say the format (e.g.
645) and film … an option when opening a new capture". Answers to the
questions asked: names `R0034_645_Portra400_013_RED.RAF`; the roll id an
auto number you can edit; the log in the capture folder. Shooting over USB
is the norm; files that arrive another way (a shutter cable, with the
camera's own software saving them) keep their names but are logged.

## Goals
- Every shot Capture takes is written down in the capture folder: which
  files, which colour each is, when, the roll, shot number, scan type,
  format, film and exposures.
- Files FreeCCR downloads get names that say what they are and never repeat:
  `<roll>_<format>_<film>_<shot>_<COLOUR>.<ext>`.
- Opening the folder later puts each logged triplet together from the log,
  exactly; only files the log doesn't know are grouped by their light.

Non-goals: renaming files another program saved; editing or deleting log
entries from the app; using the log for Hot Folder Tether's live grouping
(captures already combine as they arrive).

## Names (`capture_log.shot_stem`)
`R0034_645_Portra400_013_RED`: roll id, format key (film_formats key, e.g.
`645`, `6x7`, `35mm`), film with everything but letters and digits removed
(`Portra 400` → `Portra400`), shot number (3 digits, more if needed), colour
word: RED / GREEN / BLUE for a trichrome shot, WHITE for a single colour
exposure, BW for a black-and-white scan. Format and film are left out when
not set. The extension is the camera's. `camera_backends.unique_path` still
adds `_1` if a name is taken (a retake of one colour).

The backend names the file as it downloads it: `UsbCamera.fire(exposure,
name)` hands the stem to the worker, which sets `backend.next_name` before
firing; `Gphoto2Backend._download` (and the fake backend) use it once, for
the first file that arrives, then clear it. A file nobody asked for (the
camera's own button) keeps the camera's name. digiCamControl names its own
files (its session folder) and is left alone.

## The session: roll, format, film
The Start Hot Folder Tether window (opened from File or from Capture) gets
three fields under Scanning: **Roll** (default: the last roll used + 1,
`R0001`, `R0002`…; editable; letters, digits and `-` only), **Format** (blank
or a film format from the table) and **Film** (free text, the last ten used
offered). Stored in QSettings `capture/last_roll`, `capture/format`,
`capture/film`, `capture/films`; held as `MainWindow.capture_session`
{roll, format, film}. **New Roll** moves the roll on by one (format and film
kept). Capture shows "Roll R0034 · 645 · Portra 400 · next shot 013" under
Shoot.

Shot numbers carry on within a roll: next = 1 + the highest shot of that roll
in the folder's log or in the folder's file names, so restarting FreeCCR or
re-entering the same roll id never repeats a number.

## The log (`freeccr-capture-log.jsonl`, in the capture folder)
One JSON object a line, appended. A shot's line is written again each time
one of its files arrives and when it ends; a reader keeps the LAST line per
(roll, shot). Fields: `v` 1, `roll`, `shot`, `time` (ISO, local), `scan_type`,
`format`, `film`, `files` {red, green, blue | white | bw: file name},
`exposures` (as shot, per colour), `route` "usb" | "cable", `done`
(true once the sequence ended), `stopped` (the end reason if it didn't finish
normally). Unreadable lines are skipped.

Recording is a small registry like `capture_guide`'s:
`begin_shot(folder, entry)` at Shoot, `add_file(path)` for each file (Capture's
USB `file_saved`, and the main window's hot-folder route for cable shots,
ignoring one already added), `end_shot(reason)` when the sequence ends; files
arriving within 60 s of the end still join that shot. A file is given the
next colour of the shot not yet filled, in the order shot (red, green, blue),
which is the colour that was lit for it (a USB shot's file lands before the
next colour fires; a cable route shoots R, G, B in order).

## Open uses the log (`triplet_plan.plan_triplets`)
Before reading any light: each folder among the paths is checked for a log;
every logged trichrome shot whose red, green and blue files are all among the
paths becomes a set, exactly. The remaining paths go through the light-based
planner as before. The log's sets come first, in shooting order.

## Tests
`tests/test_capture_log.py`: names (parts left out, film cleaned, colour
words, wide numbers); next roll id; next shot from log and names; the log
round trip, last line wins, bad lines skipped; the registry (colours in order,
late file within grace, not twice, after grace not added); plan_triplets takes
logged sets exactly (mixed with unlogged files, a set with a file missing
falls back); backend naming (used once, then the camera's name; `_1` on a
clash); the start window's fields and defaults; New Roll moves the roll on;
Capture names a USB trichrome shot RED/GREEN/BLUE and logs it.

## Revision 2026-11-09: the colours sort red, green, blue

Reported: with Capture shooting into the hot folder, the main window rejected
every trichrome shot. Cause: Hot Folder Tether's `TripletGrouper` takes new
RAWs in NAME order, and the new names sort `…_013_BLUE`, `…_013_GREEN`,
`…_013_RED` — so each three reached the light check as blue, green, red and
failed it. Fix: `capture_log.slot_order(name)` reads FreeCCR's own names
(stem up to the shot number, red 0 / green 1 / blue 2, retake number), and
both `scan_type._key` (the grouper) and `triplet_plan.natural_key` (Open's
tie-break) sort by it; any other name sorts exactly as before. Test:
`tests/test_capture_log.py::test_slot_order_and_the_tether_grouper_takes_red_green_blue`.

## Revision 2026-11-09 b: a number before the colour

The user: "what can we add in the file names so that it keeps in the order of
red, green, blue" — "R0003_004_1RED.raf long like this works". A trichrome
shot's words are now `1RED`, `2GREEN`, `3BLUE` (`capture_log.WORDS`), so the
files sort red, green, blue by name in any app (Finder included); single
exposures keep `WHITE` / `BW`. `slot_order` and `_shot_from_name` (next shot)
read both the new names and the older `RED` / `GREEN` / `BLUE`, so rolls
already shot still group and number correctly. Tests:
`test_new_names_sort_red_green_blue_and_old_names_still_read`,
`test_next_shot_reads_old_and_new_names`.
