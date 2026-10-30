# Rejected combinations take one line

Status: built 2026-10-30 (the user's request).

The "Needs attention" strip under the frame list (trichrome shots that
couldn't be merged, spec/scan-type.md §5) showed up to two rows of tiles
(~180 px) whenever there was anything in it. It is now one line:

- `ThumbnailList.attention_bar`, a checkable text button in the warning
  colours: "N rejected combination(s)", shown only when N > 0.
- Hovering the line (or the tiles) shows `attention_popup`, a frame laid over
  the bottom of the frame list, just above the line, holding the same
  `attention_list` (up to four rows tall, then it scrolls). Leaving both for
  350 ms hides it (`_attention_hide_timer`, `_maybe_hide_attention`).
- Clicking the line pins it open (checked) so right-click choices and
  double-click work at leisure; clicking again closes it. Resolving the last
  entry hides the line and the tiles.
- The tiles, their tooltips, right-click menu and double-click are
  unchanged, and the list rows stay image indices (nothing is added to the
  frame list).

Tests (`tests/test_scan_type.py`): the existing strip test reads the line;
a new one checks the wording, that the line is one line high, that hover
opens and leaving closes, that a click pins it through a leave, and that
removing the last entry hides everything.
