# The panel's action buttons (2026-11-08)

The user: "get rid of convert current and autocrop buttons in right hand
menu… Just have Convert all, crop all, side by side… an Export (full width)
button at the very bottom of the same right hand menu, under sync to all."

- One row above Film B/W Point: **Convert All** (now the primary style) and
  **Auto Crop All**, side by side. The frame on screen alone is the toolbar's
  Convert (which asks Current / All) and Auto Crop.
- `convert_current_bwp_btn` and `auto_crop_btn` still exist, hidden and out
  of the layout, so the code that greys and restores them (contact sheet,
  negative controls) and their handlers are unchanged.
- **Export…** (`export_button`, primary, full width) is the panel's last row,
  under Sync to All; it opens the same window as File → Export…
  (`ImagePreview.open_export_dialog`).

Tests: `tests/test_bwp_section.py` (the row, hidden buttons, Export last and
as wide as Sync to All, opens the export window).
