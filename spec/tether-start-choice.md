# Starting Hot Folder Tether with images open

Status: built 2026-10-29 (the user's request and answer).

## Problem
Starting Hot Folder Tether always added its captures to whatever was open,
so a new roll's frames landed after the last roll's.

## Behaviour
After the folder is chosen, and only when images are open, FreeCCR asks:

- Title "Start Hot Folder Tether", text "N image(s) are open.",
  informative "Add this roll's captures to them, or close them and start
  blank? Your edits are saved and come back when you reopen those files."
- **Add to Them** (default): as before.
- **Start Blank**: the open session is closed (`_close_session_for_tether`:
  `save_catalog()` — already called at the start of `enter_tethering` — then
  `ccr_backend.clear()` and `thumbnail_list.load_thumbnails()`, which empties
  the list, clears the canvas and shows the import hint): the same as opening
  new files does.
- **Cancel** / Esc: the tether doesn't start; nothing changes.

The existing "Import Existing Files?" question comes after this one, so with
Start Blank and Yes the session is the folder's files plus new captures.
Asked through `MainWindow._tether_start_choice(n)` → "add" | "blank" | None
(the test seam). With no images open nothing is asked. The Live Capture
Monitor's "turn on Hot Folder Tether?" goes through `enter_tethering`, so it
asks the same.

## Tests (`tests/test_tether_start_choice.py`)
- No images: not asked; tether starts.
- Add: images kept, tether starts.
- Blank: images gone (backend and list), catalog saved first, tether starts,
  then a capture is the only image.
- Cancel: images kept, tether not started.
- The folder dialog cancelled: not asked.
