# Settings → Panel: show or hide editing tools

## Goal

Let users hide editing-panel sections they never use, so the panel stays
short: Settings → **Panel** lists the collapsible tools with a tickbox each.

## Sections

Printer Lights, Crosstalk Correction, Channel Levels (with Film Look and
Cineon inside it), Channel Balance, Curves, Subtractive Saturations, Chroma
Noise Reduction, Details. Master Gain, the tone sliders and the B/W-point
controls are always shown.

## Behaviour

- Hiding is display-only: a hidden section keeps applying whatever values the
  image already has (the page says so). Nothing is reset.
- The separator line above a hidden section hides with it.
- Staged like the other Settings toggles: applied on **Done**, discarded on
  close. Persisted as `panel/hidden_sections` (comma-separated ids) in the
  panel's QSettings and applied at start-up.
- Default: everything shown.

## Integration

`SlidersPanel.HIDEABLE_SECTIONS` ((id, label, attribute)), `hidden_sections()`,
`set_hidden_sections(ids)` (persist + apply), `apply_section_visibility(ids)`.
`SettingsDialog` builds the page from `HIDEABLE_SECTIONS` and calls
`set_hidden_sections` from `_apply_pending` when the staged set changed; a main
window without a panel (tests) simply gets no page.

## Tests

Hide/show toggles the section and its separator; persistence round-trip;
unknown ids ignored; the Settings page lists every section, seeds from the
panel and applies only on Done.
