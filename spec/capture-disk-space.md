# Disk space in Capture

The user, 2026-11-23: "In capture, could we have harddrive space status somewhere
suitable."

## What it shows
Under the roll note ("Roll R0025 · 35 mm · Colorplus · next shot 05"), only while a
hot folder is on (that is where every shot lands): **Disk: 212 GB free, about 480
shots**. Muted text normally, amber (`theme.WARN_TEXT`) under 30 shots left, red and
bold (`theme.DANGER`) under 5. With no size per shot known yet: just the GB, amber
under 5 GB. The tooltip names the folder and how shots are counted.

## Counting (`core/disk_space.py`, Qt-free)
- Free bytes: `shutil.disk_usage(hot folder)`.
- Bytes a shot takes: the newest complete shot in the capture log whose files are
  all there (its files' sizes summed: a trichrome shot with IR is four RAWs);
  else the median of the folder's 12 newest RAW files × the files a shot makes
  now (3 for RGB, else 1, + 1 with IR for dust).
- Looked at every 5 s (`DISK_EVERY_S`) from the 1 s status refresh, and at once
  after each shot (the roll note's refresh with the next shot's number).

## Tests (tests/test_capture_disk_space.py)
GB text; shots from a logged shot, from the folder's RAWs × files per shot, none;
levels at 30 / 5 shots and 5 GB; Capture's note shown only with a hot folder, its
colour, and throttled.
