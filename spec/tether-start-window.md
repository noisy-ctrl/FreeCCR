# One window starts Hot Folder Tether

Status: built 2026-10-30 (the user's request and answers). Supersedes
`spec/tether-start-choice.md` (its Add / Start Blank / Cancel question is
now a tickbox here) and the pop-up part of `spec/tether-black-point.md`.

## Problem
Starting a tether could take four questions in a row: the monitor's "Turn
on Hot Folder Tether?", the folder picker, "add to the open images or start
blank?", "import the files already in the folder?" — then a pop-up about
the roll's black point on the first lit frame.

## The window (`widgets/tether_start_dialog.py`, `TetherStartDialog`)
"Start Hot Folder Tether", one muted line ("Loads each new file saved into
the folder."), then:
- **Folder** [path, read-only] [Choose…] — starts at the last folder used
  (`files/last_tether_dir`) when it still exists; Start is disabled until a
  folder is set.
- **Scanning** [Colour · white light / Colour · trichrome / Black & white]
  — the app-wide scan type, set on Start (`MainWindow.set_scan_type`).
- ☐ **Close the N open images first** — only with images open; unticked
  (adding is the default). Closing = `_close_session_for_tether` (edits
  saved, restored on reopening).
- ☐ **Import the M files already in the folder** — only when the folder has
  files FreeCCR can load (recounted when the folder changes); unticked.
- ☐ **Don't ask when the monitor opens** — only when the Live Capture
  Monitor opened it; remembered in `live/ask_tether`.
- [Cancel] [Start].
`options(started)` → {start, folder, scan_type, close_open,
import_existing, dont_ask}.

## The main window
`enter_tethering(from_monitor=False)` shows it through
`_tether_start_options(n_open, start_dir, from_monitor)` (the test seam) and
returns the answers (None when already on). Cancel changes nothing. The
folder is checked for awkward Unicode as before.

## The monitor
On opening, with Hot Folder Tether off and `live/ask_tether` on, it shows
the same window straight away (no "Turn on?" first); with it on, nothing is
asked (the status strip shows it; the old "Start New… / Keep Current"
question is gone). Shooting over USB with it off shows the same window.
`LiveCaptureMonitor._start_hot_folder(from_monitor)` is the seam.

## The roll's black point: a banner nudge, no pop-up
The first new LIT capture (spec/tether-black-point.md) sets the lead as
before, but instead of a pop-up the banner's note reads "Lead frame in: press
Black Point… and draw over the clear film base." and its Black Point… button
is highlighted (`TetherBanner.set_black_point_nudge`, primary style). Both go
back to normal once a black point is set (or in Positive mode). Black Point…
then samples on the lead (`start_roll_black_point` prefers it while the nudge
stands), else the newest lit capture, else the frame on screen.

## Tests (`tests/test_tether_start_window.py`)
The window shows only the choices that apply, recounts the folder, and
returns its answers; the main window asks once, keeps open images by default,
closes them after saving when ticked, changes nothing on Cancel, takes the
scan type and the import from the window, asks nothing when already on; the
lead frame nudges the banner (no pop-up) and the nudge clears once a black
point is set. The monitor's tests: the window is shown on opening only when
the tether is off, "don't ask" is remembered, USB Shoot shows it.
