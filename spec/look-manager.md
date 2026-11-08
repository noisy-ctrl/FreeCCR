# Manage Film Looks

The user, 2026-11-08: "In the lut drop down box, can we have a 'manage'
option where you can delete luts and see folder etc."

- Both Film Look lists end "Import…", "Manage…" (`SlidersPanel.LOOK_MANAGE`).
  Picking Manage… puts the list back on the frame's look and opens the
  **Film Looks** window (`widgets/look_manager.py`, `LookManager`;
  `SlidersPanel._make_look_manager` is the test seam).
- The window lists the library (`look_lut.list_looks`), each look with
  "balance data" when its cube carries it, "can't be read", and "used by N
  open frames" (`SlidersPanel.look_users`, any colour / balance version), the
  folder's path, and **Import…** (several .cube files at once; bad ones are
  named), **Delete…** (several at once; asks Yes / No, No the default, saying
  how many open frames use them), **Show Folder** (the system's file browser),
  Close.
- Delete = `look_lut.delete_look(name)`: to the Trash (`QFile.moveToTrash`),
  else removed; the cached look is dropped. Frames that used it keep their
  `look_lut` value and show "<name> (missing)", rendering without it (as for a
  look missing on another machine); the frame on screen re-renders.
- After the window, if anything changed, both lists rebuild and keep the
  frame's look selected; the Balance boxes follow.

Tests: `tests/test_look_manager.py`.
