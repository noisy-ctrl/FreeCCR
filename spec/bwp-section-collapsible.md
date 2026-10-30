# Film B/W Point folds away

Status: built 2026-10-29 (the user's request and answer).

The Film B/W Point section's sampling rows took a lot of the panel's height.
They now sit in a `CollapsibleSection("Film B/W Point")` (the panel's usual
"+ / −" header, `SlidersPanel.bwp_section`) in the same place:

- **Inside**: Set Black Point / Set White Point / ✕, Film Stock (combo, ＋,
  −), White Point (combo, Show).
- **Outside, always visible, and ABOVE the section** (revised 2026-10-30,
  the user's request): Convert Current / Convert All, Auto Crop / Auto Crop
  All, then the anchor-source line (used on every frame). They are created
  after the section's rows but inserted at its slot (`_convert_slot`).
- Opens by default; the state is remembered in QSettings
  `panel/bwp_open` (bool, `SlidersPanel.BWP_OPEN_KEY`), saved on each toggle.
- Folded it saves ~96 px (130 → 34). Nothing else changes: the widgets are
  the same objects, enabling and syncing are untouched, and the tether's
  Black Point buttons start sampling without the panel's button being shown.

Tests (`tests/test_bwp_section.py`): opens by default with every row shown;
folding hides exactly the sampling rows and keeps Convert / Auto Crop; the
state is saved and a new window starts the way it was left; sampling from
the tether still works when folded.
