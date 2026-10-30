# Dust spots you can edit (draft for the user's review)

Status: DRAFT 2026-10-30, not built. Detection stays as it is (manual brush +
the AI detector); infrared is to be tested later (the user's lens passes IR).

## What's there now (spec/dust-removal.md)
Painting a stroke heals it with patches cloned from nearby. Moving where a
patch is copied from already exists, but it is hidden: only with **Show heal
sources** ticked, and only by dragging with the RIGHT mouse button (right-drag
a stroke moves it; right-drag a green source ring re-picks its source; double
right-click deletes). A source re-pick isn't undoable, and a spot can't be
resized after it is painted.

## Goal
Lightroom-style spots: after you paint a spot you see where it copies from
and can drag that, with the normal mouse button; any spot can be picked up,
moved, resized or deleted later.

## UX
Two tools at the top of the Dust Removal panel: **Brush** (B) and **Edit**
(E), as a pair of toggle buttons.

**Brush (as now, plus the source handle)**
- Paint as now. When a stroke lands, it is selected: its outline and its
  source ring(s) + arrow(s) appear.
- Left-drag a source ring of the selected stroke to re-pick where it copies
  from (the heal updates when you let go). Anywhere else, left-drag paints.
- Ctrl+wheel resizes the brush, as now.

**Edit**
- Hovering a spot outlines it and shows its source ring(s).
- Click a spot to select it. Then:
  - drag its body: move the spot (it keeps copying from the same patch, as
    the right-drag does now);
  - drag a source ring: re-pick that source;
  - drag its edge handle, or Ctrl+wheel: resize the spot (all of a stroke's
    dabs scale together; the source stays where you put it);
  - **Delete** / **Backspace**: delete it;
  - **Auto source** button (panel): forget the hand-picked source, let it
    choose again;
  - **Esc**: deselect (a second Esc leaves dust mode, as now).
- Click empty canvas: deselect.

**Both**
- **Show all sources** (the current "Show heal sources" tick): rings and
  arrows for every spot at once.
- Every edit (move, resize, re-pick, auto source, delete) is one Ctrl+Z step.
- The right-button gestures keep working for anyone used to them.
- AI-detected spots are ordinary spots: select, move, re-pick, resize,
  delete. Detect & Remove again replaces only AI spots you haven't touched;
  a spot you edited counts as yours.

## Data model
No new image data: spots and their plan records (offsets, `manual` flag)
already carry everything. Adds:
- a spot's `edited` flag (so a re-detect keeps it), catalog key, additive;
- undo snapshots include the plan records (so a re-pick is undoable).

## Processing
Unchanged: the same clone heal, feather and replay at preview, zoom and
export. A resize re-plans that spot's segments (hand-picked sources keep their
offset). Quality rules (preview == export, CPU == GPU) hold as now.

## Tests (to write with the build)
Hit-testing for body / ring / edge at several zooms and crops; Brush: a fresh
stroke is selected and its ring drags with the left button; Edit: select,
move (plan offsets compensated), resize (radii scaled, manual offsets
kept), delete, auto source, Esc; each edit one undo step; a re-detect keeps
edited spots; catalog round trip of `edited`; the right-button gestures
unchanged.

## Questions for the user
1. Brush + Edit as two tools, or one tool where clicking an existing spot
   selects it and clicking elsewhere paints (Lightroom's way; painting over an
   existing spot then needs a modifier key)?
2. Should moving a spot keep copying from the same patch (as now) or move its
   source along with it?
