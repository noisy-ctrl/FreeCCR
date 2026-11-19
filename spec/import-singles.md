# Frames that are no part of any shot load on their own

The user, 2026-11-19, with a screenshot of five B&W frames refused as "Not
one colour": "When importing files, it seems to be looking for a
combination, even when it's B+W. It would be nice if it just knew it was B+W
and just loaded, especially if captured from the app."

## Why it happened

With **3-way RGB merge** on (Settings → Import, remembered), every import
goes through the trichrome planner (`spec/smart-triplets.md`). A B&W frame is
lit by white light (or one LED), never makes a red/green/blue set, and was
refused with an attention entry. Non-RAW files were refused the same way,
and a selection with no RAW at all was refused outright.

## The change

In a merge import (Open, and Hot Folder Tether's import of the files already
in a trichrome folder), a file that is **no part of any red/green/blue shot
is loaded on its own** instead of being refused:

1. **Capture's log says so** (`capture_log.logged_singles`): a file of a
   shot logged as `bw` or `colour` (its `files` key `bw` / `white`) is taken
   out before any light is read. It loads with what it was: a `bw` frame
   gets the **B&W colour profile** and `capture_type = "bw"`; `colour`,
   `capture_type = "colour"`.
2. **Its light says so** (`triplet_plan.single_frames`, after the weak-frame
   rescue): a frame lit by white or mixed light (read, not one colour) with
   no one-colour frame of the SAME picture, or of an unknown picture, within
   two frames of it in shooting order. A white take next to its own shot's
   red and blue is still a failed take (a note), as before; a white frame of
   its own picture, or a roll of white-lit frames, loads.
3. **One LED only**: when every lit frame of the batch is the same one colour
   (≥ 2 frames, e.g. B&W under Capture's green B&W light), no set can ever be
   made, so they all load.
4. **Not RAW**: a file that isn't RAW can't be merged, so it loads on its own;
   a selection with no RAW at all is a normal import (no refusal).

An unlogged single gets the B&W profile when the app's scan type is B&W
(`ccr_backend.scan_type`), else stays Color; a frame the catalogue restored
always keeps its own profile (`img._catalog_fresh` marks a plain load).
Still noted for attention: frames missing from a shot, an unused retake, a
weak or white take beside its own shot, an unreadable frame.

## Integration

- `triplet_plan.Plan.singles` [(path, kind)], kind `bw` / `colour` /
  `light` (/ `file` for non-RAW in the backend); `plan_triplets` fills it
  from the log first, then `single_frames`, and plans the rest as before
  (`_plan_ordered`).
- `CCRBackend._load_merged_triplets` loads the singles with the
  passthrough files (`create_images_for_path`) and `_mark_single` sets the
  profile / capture type; `load_images_from_files` and the Open pre-check
  skip the merge for a selection with no RAW.
- `TetherWatchWorker.plan_existing` sends singles through `process(path,
  kind)`; `MainWindow._on_capture` takes the frame's own kind
  (`img._single_kind`) over the app's scan type.

## Tests

- White frames of their own pictures → singles, no notes; a white frame of
  its own picture among shots → single, the shots merge; a white take of a
  shot → still a note.
- A roll lit by one LED → all singles.
- Logged `bw` / `colour` files → singles of that kind, their light never
  read.
- The backend: three frames merge, a logged B&W fourth loads with the B&W
  profile and capture type, nothing refused.
