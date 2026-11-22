# Dust removal: a Brush and an Eraser

The user, 2026-11-22: the Dust panel "feels unintuitive (confusing how to use
it, what tool is selected)", and "the Heal / Erase distinction doesn't quite
make sense to me since the goal is to heal the dust spots by a process
similar to erasing… a brush and eraser makes more conceptual sense since
you're painting over the dust". He suggested "Paint over __ found" for the
found dust. Draft 3 (`dust-panel-draft-3.png`) was signed off: "Looks good".

## Goals

- One obvious answer to "what will a drag do now?": a **Brush | Eraser**
  pair at the top of the panel, the one in use lit, and the same word on the
  cursor over the picture.
- The words match the idea: the Brush paints over dust (and the heal follows
  the paint); the Eraser takes paint (a heal) away, or a found spot before
  it is painted over.
- Found dust is offered as paint: **Paint Over N Found**.
- The panel shows what is used every time; the rest folds away.

Non-goals: no change to how a spot heals, how dust is found, or what is
stored. No new kinds of undo.

## Layout (top to bottom)

1. **Tool** (section label). A row: **Brush** | **Eraser** (two checkable
   buttons as one segmented control; the one in use is light with dark bold
   text, the other the plain surface) and a small **↶** button (undo).
   Under it a muted line: "Brush: paint over dust to heal it. Eraser: paint
   over a heal to take it away. B / E switch, Ctrl+wheel sizes the brush,
   ↶ undoes."
2. **Brush size**, **Feather** (as before), **Fit each click to the speck**.
3. A line, then **Find Dust** (section label; its right-click keeps
   Forget What It Learned…). The status line (below). A row **Find** |
   **Before**. **Dust** (the IR finder's sensitivity, was "Sensitivity") and
   **Marks** sliders. **Paint Over N Found** (primary, disabled with nothing
   found). With something found, a muted "Wrong ones: take the Eraser to
   them first."
4. A line, then a fold, collapsed by default and remembered
   (`dust/more_open`): "More: heal sources, AI detection, attach IR shot,
   clear all". Inside: **Attach IR Shot…** (Change IR Shot… when one is
   attached), **Show heal sources** with the existing advanced hint (move a
   stroke, re-pick a source, delete a stroke), **Clear All** (danger), then
   the AI Dust Detection controls as before.
5. Stretch, **Done**.

Gone: "Erase Spots" (the Eraser is it), "Heal Them" (Paint Over N Found),
"Undo last spot" (↶), the separate "Manual" heading.

## Behaviour

- **Tool.** `DustRemovalPanel.set_tool("brush"|"erase")` sets both buttons
  (signals blocked) and `ImagePreview.set_dust_tool`, and redraws the cursor
  where it is. Unticking the tool in use picks the other one, so one is
  always lit. Each time the panel opens (`bind_image`) it starts on Brush,
  as before.
- **Keys.** **B** = Brush, **E** = Eraser, window shortcuts on the main
  window enabled only while dust mode is on (`MainWindow._dust_tool_shortcuts`,
  set in `toggle_dust_removal` and wherever dust mode ends), so the letters
  are not taken at other times. A text field with focus keeps its letters.
- **Cursor.** The brush ring follows the pointer as before; beside it, up
  and to the right, a small dark tag reads **Brush** or **Eraser**, drawn at
  a fixed screen size (`ItemIgnoresTransformations`). The Eraser's ring has
  a diagonal line through it. Display only.
- **↶ / Ctrl+Z in dust mode** (`ImagePreview.dust_undo`): if the newest undo
  snapshot differs from now ONLY in the dust keys (`dust_spots`,
  `dust_plan`, `ir_dust_pending`), it is popped and the dust redrawn, view
  kept: so one press takes back a whole Brush stroke, an Eraser stroke, a
  Paint Over N Found, or a re-picked source. Otherwise "Nothing to undo
  here." (the newest step was not dust work: the general undo, outside dust
  mode, still has it). The old behaviour (drop the last spot whatever the
  last step was) took one spot off a Paint Over and couldn't undo an erase.
- **Paint Over N Found** = the old Heal Them (`ImagePreview.heal_ir_dust`).
  N counts finds, not spots: IR finds by candidate (`ir_c`; bridges join
  pieces and don't count), marks by (`mk_c`, `mk_f`).
- **Status line**: "IR shot attached." / "No IR shot." then what Find did:
  "Found 14: 12 dust (amber), 2 marks (blue)." / "14 found spots painted
  over. Before shows the picture without them; the Eraser takes a wrong one
  away." / the existing no-IR and alignment / darkness / learning notes,
  with "Attach IR Shot…" now named "More → Attach IR Shot…".
- Everything the old buttons did is reached the same way (same methods), so
  the IR learning (an erase = "not dust", a paint over = "dust") is
  unchanged.

## Tests (`tests/test_dust_brush_eraser.py`, and updated IR tests)

- The panel has Brush/Eraser checkable, exactly one checked; ticking Eraser
  sets the canvas tool, unticking it gives Brush; `set_tool` keeps them in
  step; `bind_image` starts on Brush.
- B / E switch the tool in dust mode and are disabled outside it.
- `dust_undo`: a brush spot, an erase and a heal are each one press; a
  slider change on top means "nothing to undo here" and keeps it.
- The cursor: a tag item reading "Brush" / "Eraser"; the Eraser has a slash.
- Paint Over N Found counts finds (a fibre's strokes = 1) and is disabled
  at 0; the More fold is collapsed by default and holds Attach IR Shot…,
  Show heal sources, Clear All and the AI controls.
- The panel still fits its width (`test_panel_width.py`).
