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
- Right-click a bar: "Reset <name> to 0" resets that one, "Reset printer
  lights to 0" all five (revision 3: a double-click no longer resets).
- A change of the underlying slider (e.g. a frame loaded) moves the bar;
  the value text reads "+1.5" / "−2" / "0".
- Disabled bars ignore input.
- The panel still fits its width (`tests/test_panel_width.py`).

## Revision 2026-11-19: clicks step

The user: "Are the sliders clickable? … click on the right side and it adds
the amount." A click (a press that moves less than `DRAG_PX` 3 px) right of
a bar's 0 line adds one step and left of it takes one off, as the pad's
buttons do: half a point (Shift: one) on Density / Cyan / Magenta / Yellow,
2 (Shift: 4) on Gamma. Dragging past 3 px is the relative drag as before;
double-click still resets (its first click steps, then the reset wins) —
until revision 3 below.

## Revision 2026-11-19 (2): right-click, the step, taller bars

The user: "Can you right click on printer lights and have the ability to
change to sliders and vice-versa as well as retaining the ability to change
increments. Can the sliders be slightly taller to be more user friendly for
clicking."

- One right-click menu (`_fill_printer_menu`) on the pad's buttons, the bars
  and the section's header (before its Hide entry): **Reset printer lights to
  0**; **Show as Buttons** / **Show as Sliders** (stored, the same
  `panel/printer_style` as Settings); **Each Click ½ point (γ 2)** / **1 point
  (γ 4)** / **2 points (γ 8)** (`panel/printer_step`, default ½). Shift-click
  is always twice the step; the bars' clicks and arrow keys follow it, and so
  does every tooltip.
- The bars are 34 px tall (`PL_BAR_H`; slider rows are 28).

## Revision 2026-11-19 (3): rapid clicks step, Reset is on the right-click

The user: "Can we remove double click reset for the new printer lights
sliders. Reset should be in right click menu. I need to be able to click
rapidly and it's registering as double click." Qt delivers the second press
of a quick pair as a double-click, so `BarSlider.mouseDoubleClickEvent` is
now a press like any other: every click of a rapid run steps (and a drag can
start on it). A bar's right-click menu starts with **Reset <name> to 0**
(`_printer_light_reset`, disabled at 0), then **Reset printer lights to 0**;
the pad's and the header's menus are unchanged. Tooltips no longer mention
double-click. Tests: `test_rapid_clicks_all_step_and_a_double_click_never_resets`,
`test_right_clicking_a_bar_offers_its_own_reset`.
