# Settings → Panel: show or hide editing tools

## Goal

Let users hide editing-panel sections they never use, so the panel stays
short: Settings → **Panel** lists the collapsible tools with a tickbox each.

## Sections

Layers (revision 2026-10-23, see below), Printer Lights, Crosstalk
Correction, Lab Finish (Golden Highlights, Shadow Neutralise, Shadow Lift,
Glow), Channel Levels (with Film Look and Cineon inside it),
Channel Balance, Curves, Subtractive Saturations, Chroma Noise Reduction,
Details. Master Gain, the tone sliders and the B/W-point controls are always
shown.

**Lab Finish** (2026-10-23) is a collapsible of its own, open by default, in
the saturation group between Vibrance and Subtracted Sat. It sits mid-list,
so it has a line above AND below; both hide with it (`_lab_finish_sep_below`
is the extra one). Its two sliders were created where they always were, so
`ADJUSTMENT_KEYS` and stored settings are unchanged: only the layout moved.

**Layers** (2026-10-23), the area-editing list at the top of the panel, is
hideable too, with the line under it (`_layers_sep_below`). It is shown anyway
on a frame that has area layers (`_apply_layers_visibility`, re-checked on
every Layers rebuild: frame switch, area added or removed): the sliders edit
whichever layer is active, so the list that says which one it is must not
vanish while there is a choice. On a frame without areas there is nothing to
pick but Whole Image, so hiding it loses nothing.

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
