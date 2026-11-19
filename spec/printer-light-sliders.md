# Printer Lights as sliders

The user, 2026-11-19, with a screenshot of another app's panel: "can we have
the option in the settings to change printer lights to sliders that look like
this … I want to keep the same options though (printer light colours) and
density and gamma."

## What changes

- Settings → Panel → **Printer Lights**: *Buttons* (the pad, as now, the
  default) or *Sliders*. Staged like the rest of the page and applied on
  Done; stored in `panel/printer_style` (`"buttons"` | `"sliders"`).
- *Sliders* replaces the pad and its readout line with five bar sliders, in
  the pad's column order: **Density**, **Gamma**, **Cyan**, **Magenta**,
  **Yellow**. The underlying controls are unchanged: Density, Cyan, Magenta
  and Yellow are still the Channel Levels Master / R / G / B Shift sliders in
  printer points (3 Shift units a point, `PRINTER_POINT_UNITS`), Gamma is still
  the tone Gamma slider. Nothing new is stored, so undo, copy/paste, sync,
  catalogues, area layers and the Film Look are untouched, as with the pad.

## The bar slider (`widgets/bar_slider.py`, `BarSlider`)

The look asked for: one rounded bar per control, the name inside on the left,
the value inside on the right, a faint line at 0 and a bright line at the
value; no separate handle or groove.

- Each colour bar is tinted from its complement on the left to its own
  colour on the right (Cyan: red → cyan, Magenta: green → magenta, Yellow:
  blue → yellow), faded to nothing at the centre, so the side a press moves
  towards is the colour it adds. Density runs light → dark, Gamma dark →
  light. Right is "+" on every bar, with the pad's meaning: more of that
  colour, more density (darker), γ+ lifts the midtones.
- Values: printer points with one decimal when not whole, "+1.5", "−2", "0";
  Gamma in its own units ("+4").
- Range: the underlying slider's whole range (±33⅓ points, Gamma ±100), so the
  line's place is honest.
- **Drag** moves the value relatively from where it was (a click never jumps
  it), half a point at a time (Gamma: 1); **Shift-drag** is four times finer.
  **Double-click** resets that one to 0. **Left / Right** keys: half a point
  (Gamma 2), **Shift**: one point (Gamma 4), the pad's steps. The wheel is
  left to the panel, which scrolls.
- Hover: a little lighter, with a faint tick every point (every 5 taller).
- Right-click: "Reset printer lights to 0", as the pad.
- Disabled (no frame, or the underlying slider off): muted, inert.
- Releasing a drag settles the render as releasing a slider does
  (`_settle_now`).
- Kept in step with the underlying sliders wherever the pad's readout was
  updated (`_update_printer_readout`), so loading a frame, undo, paste or the
  Channel Levels sliders move the bars.

## Tests

- The option switches the pad and the bars; it is stored and applied on Done.
- Dragging Cyan right by one point's width → R Shift −3; Density right →
  Master Shift negative; Gamma right → Gamma up; Shift-drag is finer.
- Keys: Right = +0.5 point (−1.5 units), Shift+Right = +1 point; Gamma ±2/±4.
- Double-click resets one; right-click reset clears all five.
- A change of the underlying slider (e.g. a frame loaded) moves the bar;
  the value text reads "+1.5" / "−2" / "0".
- Disabled bars ignore input.
- The panel still fits its width (`tests/test_panel_width.py`).
