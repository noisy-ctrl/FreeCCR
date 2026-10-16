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

| Button | Channel Levels change (per point) | Result |
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

## Half points (revised 2026-10-16)

- A click moves **half a printer point**; **Shift-click moves one point**
  (was: one, and four). Right-click still resets.
- Half a point is 1.5 Shift-slider units, and a `QSlider` holds integers, so
  the four Shift sliders (Master, R, G, B) now step in **half units**:
  `HalfStepSlider` keeps the integer position at twice the value (range
  ±200 for ±100) and its `value()` / `setValue()` speak value units, so every
  caller that reads or writes a slider is unchanged. `value()` returns an
  `int` for whole values and a float only for x.5, so settings, catalogs and
  undo snapshots of existing edits are byte-identical; the pipeline already
  takes these parameters as floats (`/ CH_SLIDER_DIV`).
- Consequences on those four sliders, and only those: the value column can
  read `-4.5`; Shift-drag (fine) and the arrow keys reach halves; the mouse
  wheel and a plain drag still move whole units.
- Raw-position code inside the slider class (drag state, the style option)
  uses `QSlider.value(self)`, never the overridden `value()`.
- The readout already printed one decimal for non-whole points, so a half
  point reads `C +0.5`.

### Tests

- Click = ±1.5 units, Shift-click = ±3; two clicks = one point exactly.
- The half value survives slider → settings → slider (`get_slider_values`,
  `_load_active_layer`), whole values stay `int`, the label shows `-1.5`.
- Limits clamp at ±100 in value units; wheel moves one whole unit;
  double-click resets; other sliders are untouched (range ±100, int steps).

## Tests

- Each button moves the right slider by 3 (Shift: 12) in the right direction;
  D moves Master Shift. Columns are D C M Y; there is no step row.
- Readout reflects slider values (including hand-set fractions); 0 resets.
- Limit handling; the pad changes the active layer (area) like the sliders.

## Gamma pair (revised 2026-10-16)

- A **γ− / γ+** column right after D (columns D γ C M Y), in neutral slate
  (darker for γ−), so it doesn't read as a colour key.
- It drives the tone section's **Gamma** slider (the pinned-endpoint midtone
  bend, spec/gamma-slider.md) in that slider's own direction: γ+ lifts the
  midtones, γ− lowers them; black and white stay put. 2 slider units per
  click (about 2/255 at mid-grey), 4 per Shift-click, clamped at ±100 with
  the same "at its limit" hint.
- Note the sign: unlike D (where + adds density, i.e. darker), γ+ is
  lighter, because it follows the Gamma slider the buttons move. Flip
  `PL_GAMMA_*` signs in `_printer_light` if the density convention is
  wanted instead.
- The readout ends with the Gamma value: `... Y 0   points   ·   γ +4`.
  The pad's right-click reset also returns Gamma to 0.
- Nothing new is stored: it is the existing `gamma` key, so undo, copy/paste,
  the Tone sync group, catalogs and area layers are unchanged.

Tests: γ moves only the Gamma slider (+2 / Shift +4, + lifts), the readout
shows it, the limit hint, the pad reset includes it, the column order is
D γ C M Y.
