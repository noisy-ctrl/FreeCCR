# Film formats in the Crop panel and on the contact sheet

## Goal

Speak film, not ratios. The Crop panel's list leads with film formats
("120: 6x4.5", "35mm: 36x24"…), and the contact sheet names each frame's
format, totals them in its footer, and cuts its rows like the film's strips.

## The table (`core/film_formats.py`)

One list, used everywhere: key, label, gate ratio (long / short of the image
area, not the nominal name), frames per contact-sheet row.

| key | label | ratio | row |
|---|---|---|---|
| 35mm | 35mm: 36x24 | 1.500 | 6 |
| half | 35mm: half frame | 1.333 | 6 |
| xpan | 35mm: XPan | 2.708 | 3 |
| 645 | 120: 6x4.5 | 1.349 | 4 |
| 6x6 | 120: 6x6 | 1.000 | 3 |
| 6x7 | 120: 6x7 | 1.232 | 3 |
| 6x8 | 120: 6x8 | 1.357 | 3 |
| 6x9 | 120: 6x9 | 1.500 | 2 |
| 6x12 | 120: 6x12 | 2.000 | 2 |
| 6x17 | 120: 6x17 | 3.000 | 1 |
| 4x5 | Sheet: 4x5 | 1.250 | 3 |
| 8x10 | Sheet: 8x10 | 1.250 | 3 |

Auto crop's own format names map onto these keys (`DETECTOR_TO_FILM`), and
its messages use the labels ("Auto crop: 120: 6x4.5, +0.30°…").

## Crop panel

- The section is called **Format**. The list: Free, Original, then a
  **FILM FORMATS** heading and the table, then a **PRINT AND SCREEN** heading
  and the ratios that were there before (1:1 … 2.39:1), then Custom…
  Headings are disabled items: never selectable, and a saved choice that is a
  heading falls back to Free. Old saved keys are unchanged, so a remembered
  ratio still loads.
- A film format locks the box to its gate ratio, with Landscape / Portrait as
  before (6x6 has no orientation).
- **Done with a film format picked records it on the frame**
  (`img.film_format`; Free, Original or a print ratio leave it as it was).
  It is saved in the catalog (an additive key) and is what auto crop learns
  as that frame's format.

## Contact sheet

- `frame_format(img)`: the format picked in the Crop panel, else what auto
  crop found, else what the crop's proportions say (3:2 reads as 35mm, ~1.35
  as 6x4.5, 1:1 as 6x6, 1.23 as 6x7, within 2.5%; a print-ratio crop gives
  none). Ambiguous shapes (35mm vs 6x9, 6x4.5 vs 6x8 vs half frame) are
  settled by picking the format in the Crop panel.
- Under each frame, after the name: "120: 6x4.5 · auto crop (medium)".
- Footer: "12 frames · 120: 6x6 · 14 Oct 2026", or with a mixed roll
  "120: 6x6 ×10 · 120: 6x4.5 ×2".
- Frames per row follow the roll's most common format (table above): 35mm in
  6s, 6x4.5 in 4s, 6x6 and 6x7 in 3s, 6x9 in 2s. With no format known, 6 for
  more than 12 frames, else 4.
- A format change redraws that frame's label and the footer, like any other
  change.

## Tests

- Table: labels, auto crop name mapping, ratios, strip lengths; inferring from
  a crop's proportions (either orientation; none for a print ratio).
- Crop panel: film formats first under a disabled heading, the panel reports
  the film format, Done records it and Free doesn't unset it.
- Contact sheet: format priority, labels, footer summary, strips of 3 for
  6x6, a format change redraws that frame and the footer.
- Catalog round trip; learning records the picked format.

## Revision 2026-11-22: the Crop panel follows the frame's format

The user: "Crop tool doesn't seem to catch that I've changed from 120 645 to
35mm again." The panel's Format was only ever the last one used
(`crop/aspect_key`), so a 6x4.5 left from the last roll stayed on a 35mm roll.

- On opening (`CropPanel.bind_image` → `follow_frame_format`), a remembered
  FILM format gives way to the format the frame on screen is known to be
  (`frame_format`): its own `film_format` (Done with a film format, or the
  film menu's marking), else the frame guide's it was shot with
  (`capture_guide["format"]`), else the roll's (the film menu's Format,
  `MainWindow.roll_film_and_format`). Nothing known: unchanged.
- A print ratio, Free, Original or Custom is a choice of its own and stays
  (a 5:4 for posting isn't the film's format).
- It is set quietly: a crop the frame already has is never reshaped; a box
  seeded on entry takes the new shape as before. The new choice is
  remembered like a picked one.

Tests (`tests/test_crop_panel.py`): a remembered 6x4.5 becomes the roll's
35mm, the guide's format beats the roll's and the frame's own beats both;
a remembered print ratio, Free, Original or Custom stays; nothing known leaves it; an existing crop
keeps its box.
