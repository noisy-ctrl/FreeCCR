# Toolbar Convert asks: current or all

## Goal

The toolbar's **Convert** opens a small dialogue: **Convert Current**,
**Convert All**, **Cancel** (Convert Current is the default button).

## Behaviour

- **Convert Current**: a frame with a reference frame drawn (right-drag)
  converts from it, as the toolbar Convert always did; otherwise it is the
  panel's **Convert Current** (the Film B/W Point settings: black point, auto
  black point, white point, film-stock slopes). Before, the toolbar Convert
  refused a frame without a reference frame.
- **Convert All**: the panel's **Convert All**, without its second "Convert
  all N images?" question (the dialogue already asked). Its own no-anchor
  warning (Settings → General → Conversion) still applies.
- **Cancel**: nothing happens.
- Positive mode: the action stays disabled, as before.

## Integration

`ImagePreview._ask_convert_scope()` → "current" / "all" / None (a seam for
tests); `ImagePreview.convert_ccr` dispatches;
`SlidersPanel._on_convert_all_bwpoint(confirmed=False)`; the reference path
is `ImagePreview._convert_current_by_reference`.

## Tests (`tests/test_convert_button_choice.py`)

Cancel converts nothing; Convert All calls the panel's Convert All with
`confirmed=True` (no second question); Convert Current with a reference frame
uses it, without one calls the panel's Convert Current; the panel's own
Convert All still asks.

## Revision 2026-11-23: Convert Selected, as Auto Crop's

The user: "can the convert button mirror the options of the auto crop one
when selecting multiple images". With more than one thumbnail selected the
question offers **Convert Current / Convert Selected (N) / Convert All /
Cancel**, Selected the default (`_ask_convert_scope(n_selected)`, the seam);
with one or none it is as before.

Selected (`SlidersPanel._on_convert_selected(indices)` → a
`BWPointConvertWorker(images=…)` behind Convert All's progress window →
`CCRBackend.convert_frames(images)`): each chosen frame exactly as Convert
Current converts it (`plan_frame_conversion` + `apply_frame_conversion`:
its own film base, else the sampled or roll one; its white point by its
choice; auto tone, AWB, the crop after converting when that is on), with the
roll's RECORDED black and white points as context. Convert All's batch is
not used because it reloads every frame to detect its base, which would
undo the frames left out. A frame with nothing to anchor to is converted
with the fixed density invert and named, as in Convert All (Convert
Current asks first; a batch doesn't). The summary is Convert All's
(`last_auto_bp_summary` → the hint).
