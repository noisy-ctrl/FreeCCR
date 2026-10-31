# File → New Roll…

The user (2026-10-31): "in File… in top menu, it would be good to have a new
roll button that closes the current images".

## Goals
- One menu command that ends the roll on screen and leaves an empty window
  ready for the next one, without opening anything.
- Nothing is lost: the open images' edits are saved to the catalogue first
  and come back when those files are reopened (as Open Files already does).
- The next roll starts as a fresh batch does today: the roll's sampled black
  point and white point and the film stock selection go back to the default
  (in memory only, exactly like `CCRBackend` does at the start of a load;
  the QSettings copy that restores at the next app start is untouched).

## Non-goals
- Deleting or moving any file.
- Stopping Hot Folder Tether: a new roll on the copy stand is the commonest
  case, so the watch carries on.
- Remembering anything per roll (a roll name, a folder).

## UX
- File menu, first item: **New Roll…** (Ctrl+N / ⌘N), above Open Files.
- With images open it asks first (`_confirm_new_roll(n)`, the test seam):

      New Roll
      Close the 36 open images and start a new roll?
      Your edits are saved and come back when you reopen those files. The
      black point and film stock go back to the default, as when you open
      new files.  [Hot Folder Tether keeps watching “folder”.]
                                             [Cancel]  [New Roll]

  Cancel is the default and Esc (as the Open confirm, spec/open-confirm-and-
  drop.md).
- With nothing open there is no question: the roll state is reset and the
  panel's hint says "Ready for a new roll."
- After a reset the hint says "New roll: closed N images (edits saved)."

## What it does (`MainWindow.new_roll()`)
1. Ask (above); Cancel → nothing changes.
2. `_close_session_for_tether()` (save catalogue, `ccr_backend.clear()`,
   reload the list → empty list, empty canvas).
3. Roll state: `ccr_backend.black_point_bgr = None`, `white_point_bgr =
   None`, `sliders_panel.reset_film_stock_combo()` (which clears the
   backend's film stock through its signal).
4. The "Needs attention" strip is emptied (`_tether_attention = []`): its
   rejected combinations belong to the roll just closed.
5. Hot Folder Tether, if on: keeps watching the same folder; the banner's
   count goes back to 0; the black point lead starts over
   (`_tether_prompted = False`, `_tether_lead_img = None`,
   `_tether_last_lit = None`), so the next lit capture is the new roll's
   lead and nudges for its black point. Frames of a trichrome shot still
   waiting for the rest (`_tether_grouper.flush()`) are the old roll's tail:
   they go to the (now empty) Needs attention strip, as when tethering stops,
   rather than merging with the new roll's first frames.
6. A capture already being converted on the worker when New Roll is chosen
   lands in the new roll (it was dispatched before); rare, and removable.

## Tests
- The menu has New Roll… first with Ctrl+N.
- Cancel keeps the images and the black point.
- Confirmed: images gone, catalogue saved (the edits come back on reopen),
  black/white point None, film stock Default, attention emptied.
- Nothing open: no question, roll state reset.
- Tethering: still active on the same folder, count 0, lead state reset,
  a waiting part-triplet goes to attention.
