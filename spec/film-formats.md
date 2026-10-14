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
