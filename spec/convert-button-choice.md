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
