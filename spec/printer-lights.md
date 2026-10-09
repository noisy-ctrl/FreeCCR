# Printer Lights

## Goal

A lab-style printer-lights pad: C−/C+, M−/M+, Y−/Y+, D−/D+ buttons with a
step of 1, 2 or 4 points and a reset, as on a Frontier/Noritsu operator panel
(and Negbase). Fast, repeatable exposure/colour corrections in the lab's own
units.

## What a printer point is here

Printer lights are not CMY maths: in an enlarger they change the red, green and
blue light reaching the paper; C/M/Y name the colour the change pushes the
print towards. One traditional printer point is 0.025 log exposure (≈ 1/12
stop). On FreeCCR's density base (`d = 0.8·log10(base/scan)`) that is
`0.8 × 0.025 = 0.02` density, i.e. **3 units** of a Channel Levels Shift slider
(`CH_SLIDER_DIV = 150`).

| Button | Channel Levels change (× step) | Result |
|---|---|---|
| C+ / C− | R Shift −3 / +3 | more cyan / more red |
| M+ / M− | G Shift −3 / +3 | more magenta / more green |
| Y+ / Y− | B Shift −3 / +3 | more yellow / more blue |
| D+ / D− | Master Shift −3 / +3 | darker / lighter |

"+" always adds that colour (or density) to the picture — the scanner
convention.

## Design

- The pad is a front end to the existing Channel Levels Shift sliders. Nothing
  new is stored: undo, copy/paste, sync, catalogs, area layers and the Film
  Look (which reads density after Channel Levels — exactly where a lab's
  printer lights act) all work unchanged.
- Section **Printer Lights**, between Crosstalk Correction and Channel Levels
  (pipeline order), expanded by default.
- Row 1: D− C− M− Y−; row 2: D+ C+ M+ Y+ — **density first**, as on a lab
  printer's keypad — in saturated colours: each button is the colour its press
  pushes towards (D− light grey, D+ near-black with an outline).
- One printer point per click; **Shift-click** moves 4. **Right-click** any
  button: "Reset printer lights to 0" (the four Shift sliders).
- No step row and no per-channel value boxes (Negbase shows the values in boxes
  under its buttons; this pad deliberately doesn't copy that). A single muted
  readout line under the pad: `D +5  C +3  M −2  Y 0  points` (one decimal when
  a slider was set by hand to a non-multiple of 3). Updates on every change.
- At a slider's limit (±100 = ±33 points) further presses do nothing and the
  hint says so.

*Revised 2026-10-09:* the first version had a +1/+2/+4/0 step row under the pad
and C M Y D order; replaced by Shift-click / right-click and D C M Y.

## Tests

- Each button moves the right slider by 3 (Shift: 12) in the right direction;
  D moves Master Shift. Columns are D C M Y; there is no step row.
- Readout reflects slider values (including hand-set fractions); 0 resets.
- Limit handling; the pad changes the active layer (area) like the sliders.
