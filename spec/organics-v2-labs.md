# Organics v2: Lab A or Lab B

The user, 2026-11-26: "A lab choice for Organics v2. Have A and B - chosen by
right click?" Picked: A = the Valencia lab alone, B = the other Portra lab
alone; the third lab's six pairs only fill in colours a lab didn't measure;
frames already using Organics v2 move to A.

## Why

Organics v2's tables (spec/colour-organics-v2.md) average three labs. The
labs disagree in places: on light skin and sand the Valencia lab's scans
(060940, 30 pairs) are 6-11 deg yellower with less chroma, while 00003875
(14 pairs) turns the same tones the other way, and sits close to FreeCCR's
default in mid tones. Averaged, those moves partly cancel, so the result is
neither lab. Held out, 00003875 was better off at half strength.

## Goals / non-goals

- Two measured renderings, Lab A and Lab B, each one lab's own hue moves and
  saturation by tone; both sliders (Organics v2, Organic Saturation) use the
  chosen lab's tables.
- Chosen per frame (per layer, like every option), from a right-click on
  either slider's label; copied, pasted, synced, saved, undone like the
  other tools' options.
- Not: a blend of the two, a third lab entry, or lab names in the app (the
  app says "Lab A" / "Lab B" and what each does; no lab or brand names).
- Not: Lab Tone or Clean Lights per lab (a later step if this one is liked).

## UX

Right-click the **Organics v2** or **Organic Saturation** label: a small menu,
two radio items, the frame's choice ticked:

- **Lab A** · yellower skin and reds, softer light tones
- **Lab B** · near the default, reds a little more orange

(written from § Measured)

Picking one is one undo step and re-renders. The labels' tooltips end with
"Right-click: Lab A or Lab B" and, on Lab B, the label is underlined (as a
tool with non-default options is) and the tooltip says "Lab B". Neither
slider changes value. In B&W both rows are hidden as before.

## Data model

- `adjustment_settings["organics_v2_lab"]`: `"b"` for Lab B; absent for Lab A
  (the default). `organics_v2.normalize_lab(v)` -> `"b"` or None (anything
  else, including "a", is A), so a stored dict only ever carries "b".
- Per layer (global and area), riding every slider -> dict rebuild like
  `organics_shape`.
- Catalogue: `adjustment_settings` is saved as it is; nothing to migrate.

## Tables (`core/organics_v2_data.py`, WRITTEN by the tool)

`LABS = {"a": {...}, "b": {...}}`, each with `HUE_MOVE`, `CHROMA_LOG` on the
same knots, plus `pairs` (frames measured) and `lab` (its id, for the record
only: never shown). Module-level `HUE_MOVE` / `CHROMA_LOG` stay as aliases of
A's for anything that reads them.

Fit (`tools/fit_organics_v2.py --labs a=060940 b=00003875 --gap 14868425`):
per lab exactly as today (Gaussian kernel, every frame the same, shrunk by
support eff / (eff + 150)); then the gap lab fills what that lab left:
`value = v_lab + (1 - s_lab) * v_gap` (v already shrunk), so a node the lab
measured well is its own and a node it never saw takes the gap lab's shrunk
value. The chroma table is capped at 0 after the fill, as before.

The tool also reports, per lab, leave-one-frame-out errors (hue and chroma,
each frame scored on tables fitted without it) for: no Organics v2, the old
average, Lab A, Lab B; plus each lab's frames scored with the other lab's
tables.

## Processing (`core/organics_v2.py`)

Unchanged maths. Everything table-derived is per lab: `table_fields(L, h,
lab="a")`, `_grids(lab)` (cached per lab), `fold_limit(lab)`, `amounts(hue,
sat, lab)`, `field_maps(..., lab)`, `apply_organics_v2(img, hue, sat,
lab="a")`. The fold limit holds each lab's own hue amount.

## Integration

- `ccr_processor.adjust_image` / `adjust_image_opencl`: `organics_v2_lab`
  appended LAST (positional callers), passed to both v2 sites and to the
  OpenCL fallbacks into `adjust_image`.
- `CCRImage`: `s.get('organics_v2_lab')` at both render sites.
- `SlidersPanel`: `_v2_lab` (None = A) loaded with the layer, attached on
  rebuild, reset to A, synced with the "sat" group, in the copy snapshot,
  pasted with the Organics v2 / Organic Saturation rows (either ticked brings
  the clipboard's lab; neither keeps the target's); the paste rows read
  "Organics v2  (50, lab B)". Film presets carry it through the clip.
- `organics_v2_menu()` builds the menu (the test seam); `set_v2_lab(lab)`
  does the work.
- The Organics v2 tooltip no longer names the scanner maker (app text names
  no brands).

## Test plan (`tests/test_organics_v2_labs.py`)

- `normalize_lab`: "b" -> "b"; "a", None, "x" -> None.
- Each lab: greys exact, L kept, chroma never grows, the hue map never folds
  at 100, a fine hue ramp stays in order, in range, colour grain doesn't grow.
- A and B differ where the labs do (light skin: A turns yellower than B).
- `adjust_image(..., organics_v2_lab="b")` differs from A; at 0 both are the
  input.
- Panel: the menu ticks the frame's lab; choosing B stores "b", is one undo
  step, re-renders; A removes the key; reset returns to A; copy -> paste with
  the Organics v2 row brings the lab, without it keeps the target's; sync
  with "sat" carries it; the paste row says "lab B".
- `tests/test_organics_v2.py` keeps passing on Lab A (its directions are A's).

## Refinement (once, before building)

- Gap fill on BOTH labs or only where needed? Both, by the formula: a node
  the lab measured (s near 1) is untouched, so it only ever fills.
- Why not keep the old average as a third, hidden "missing key" table so
  yesterday's frames don't move? The user chose that they move to A; one
  table less to carry, and A is the closest lab to the average anyway (30 of
  50 pairs).
- Should the choice be app-wide? No: it is a look decision per roll / frame,
  and per-frame values travel with copy/paste/sync/presets for free.
- The menu text is written from the measurement (§ Measured), not guessed.

## Measured

On the same samples as the first fit (`variants.npz`: each frame rendered by
FreeCCR's default after fitting its six Channel Levels numbers to the lab
scan, with Clean Lights at 50). Scores as the first fit's: median |hue|
residual per (hue, L) cell in degrees, median |log chroma| residual, mean
colour difference x100; every frame counted the same. "Held out" = each frame
scored on its lab's tables fitted without that frame.

| frames of | Organics v2 off | old average | Lab A | Lab B | own lab, held out |
|---|---|---|---|---|---|
| 060940 (A) | 3.49 / 0.081 / 1.92 | 1.99 / 0.051 / 1.82 | 1.59 / 0.036 / 1.81 | 3.03 / 0.067 / 1.87 | 1.97 / 0.044 / 1.84 |
| 00003875 (B) | 2.23 / 0.049 / 1.00 | 3.06 / 0.060 / 1.05 | 4.64 / 0.097 / 1.20 | 1.20 / 0.034 / 0.94 | 1.69 / 0.049 / 1.00 |
| 14868425 (gap) | 3.62 / 0.071 / 1.67 | 2.04 / 0.045 / 1.53 | 2.45 / 0.063 / 1.60 | 2.80 / 0.047 / 1.57 | |

So, held out: Lab A on the Valencia lab's frames matches the old average on
hue and does better on chroma (0.044 vs 0.051); Lab B on 00003875's frames
does what the average couldn't, 2.23 -> 1.69 deg where the average made them
worse (3.06). Each lab's tables on the other lab's frames are worse than off
for B's frames (4.64), slightly better than off for A's (3.03): the choice
matters.

Where they differ (hue move in deg / chroma ratio at the lab's amount, OKLab):

| | Lab A | Lab B |
|---|---|---|
| light skin (h 55, L 0.80) | +10.3 / x0.81 | +0.4 / x0.96 |
| light reds (h 25, L 0.80) | +14.0 / x0.78 | +2.8 / x0.91 |
| light sand (h 85, L 0.80) | +5.0 / x0.90 | +0.2 / x0.97 |
| dark greens (h 135, L 0.30) | -6.0 / x0.81 | +0.5 / x0.95 |
| dark teal (h 200, L 0.30) | -7.0 / x0.98 | +0.7 / x0.95 |
| deep sky (h 245, L 0.30) | -10.5 / x0.78 | -10.0 / x0.86 |
| mid reds (h 25, L 0.55) | +6.1 / x0.92 | +5.1 / x1.00 |

Both maps stay unfolded at 100 (fold limit 100 for each). The repo's tool
(`fit_organics_v2.py --choice a=060940 --choice b=00003875 --gap 14868425
--write`) uses the same maths as the measurement script; its writer
reproduces the data file byte for byte.
