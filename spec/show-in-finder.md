# Show in Finder

The user, 2026-11-19: "When you right click an image, it would be nice to be
able to have 'Show in Finder'."

- The thumbnail list's right-click menu has **Show in Finder** (macOS),
  **Show in Explorer** (Windows) or **Show in File Manager** (Linux), after
  Duplicate / Remove from list.
- It shows the files of every selected frame, selected in the file manager:
  a 3-way merge's three source files (red, green, blue), otherwise the
  frame's own file; duplicates and slices show the file they came from.
  Files that no longer exist are left out; with none left the entry is greyed.
- macOS: `open -R` with all the files (one Finder window selects them).
  Windows: `explorer /select,<file>`, one window per folder. Linux:
  `xdg-open` on each folder (selecting a file isn't portable).
- `utils/reveal_files.py`: `menu_label`, `frame_files(img)`,
  `commands(paths, platform)`, `reveal(paths, platform, run)`.

Tests (`tests/test_show_in_finder.py`): the labels and command lines per
platform, a merge's sources, a missing file left out, and the menu revealing
the selected frames' files.
