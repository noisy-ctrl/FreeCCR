# Capture's frame guide is saved with the shot

The user, 2026-11-09: "auto crop would have done a better job if it took into
account the frame in capture. I thought maybe we used capture as a reference
to prevent this kind of error?" — "Please build yes."

## Problem
The guide a shot carries (`core/capture_guide.py`, spec/guide-follow-and-crop.md
§3) lived only in memory, tied to file paths for this session. A frame got it
only when it arrived through Hot Folder Tether while FreeCCR was still
running. Frames opened later with File → Open (after a restart, or because
Hot Folder Tether had rejected them) never had it, so Convert and Auto Crop
fell back to plain auto crop.

## Goals
- Every Capture shot taken with the guide in use writes the guide into its
  line of the capture log, with the small live picture it was placed on.
- File → Open (single files or a 3-way merge), Merge Anyway / Load as Single
  Frames and Hot Folder Tether all attach it from the log when the session's
  memory doesn't have it, exactly as a live capture does (`map_guide`).
- Nothing changes for files Capture didn't shoot, or shots without a guide.

Non-goals: drawing the guide in the main window; editing it after the shot;
a guide for files whose log has been deleted.

## Data
In the shot's log entry (`freeccr-capture-log.jsonl`), when Shoot had a guide
record: `"guide": {"box": {cx, cy, w, h, angle}, "format", "live_size": [w, h],
"following": bool, "live": ".freeccr-guides/<roll>_<shot>.png"}`. The PNG is
the record's grey live picture (≤ `LIVE_SIDE` 480 px, 8-bit), in a hidden
folder beside the log (`GUIDE_DIR`). Written once, at the shot's first line;
the rewrites as files arrive carry the same dict.

## Functions (`core/capture_guide.py`)
- `save_for_log(folder, roll, shot, record)` → the dict above (writes the
  PNG; None when there is no record or the PNG can't be written — the shot
  is never affected).
- `record_from_log(paths, logs=None)` → a record (the same shape
  `make_record` gives) for the first of `paths` named in its folder's log
  with a guide, else None. `logs` caches `read_log` per folder for a batch.
- `attach(img, paths, raw=None, logs=None)` → the in-memory record
  (`lookup`) first, else the log's; `map_guide` onto `raw` (default
  `img.resized_raw`) → `img.capture_guide`, the printed "Capture guide for
  NAME: …" line. True when attached.

## Integration
- `LiveCaptureMonitor._begin_log_shot` puts `save_for_log(...)` in the entry
  when `p["guide"]` is set.
- `TetherWatchWorker._guide_from_capture` calls `attach` (so Merge Anyway and
  Load as Single Frames find it after a restart too).
- `CCRBackend._attach_capture_guides(images)` runs on the loader thread just
  before a load is published (`_commit_load`): for each image without a
  `capture_guide`, its paths (`merge_sources` or `file_path`), a shared log
  cache, and the unconverted picture (`resized_raw`, or `raw_preview` for a
  frame the catalogue restored converted). Only files named in a log with a
  guide cost anything.
- Convert and Auto Crop already use `img.capture_guide`
  (`crops_at_conversion`, `detect_frame_crop`), and the catalogue already
  stores it.

## Tests (`tests/test_guide_in_log.py`)
- `save_for_log` writes the PNG and a dict that `record_from_log` reads back
  to the same guide and picture; no record → None.
- `record_from_log` finds a shot by any of its files, ignores a shot without
  a guide and a missing PNG.
- `attach` maps a synthetic live picture onto its scan (the guide comes back
  where it was placed), prefers the in-memory record.
- Capture's `_begin_log_shot` writes the guide into the log.
- Open: `load_images_from_files` on a file named in a log gives the image its
  `capture_guide`; a file not in any log is untouched.
