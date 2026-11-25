# Clear All takes the found dust too; Dust removal moves along the toolbar

The user, 2026-11-25: "In dust removal section: can clear all clear the
proposed dust reduction. Can the dust removal button on the right side of
that row (on the left) of buttons".

## Clear All

The Dust panel's **Clear All** (in the More fold) took off the frame's
heals only; dust and marks that Find had flagged (amber / blue, "Paint Over
N Found") stayed. Now it takes both.

- Nothing to clear (no heals, nothing found): "Nothing to clear.", no
  question.
- Otherwise it asks, naming what goes (`dust_panel.clear_question`):
  "Take the 3 heals off this frame?", "Take the 5 found spots not painted
  over yet off this frame?", "Take the 1 heal and the 5 found spots not
  painted over yet off this frame?" (`_confirm_clear`, the seam).
- `ImagePreview.dust_clear_all` clears `dust_spots` and `ir_dust_pending`
  in ONE undo step (both are in the undo snapshot), redraws the found-spot
  outlines and the toolbar's red dot. Nothing is learned from it (it is
  not a judgement on any one find).
- The IR section then says "Cleared. Find looks again." (memory-only flag
  `img.ir_cleared`, reset when Find runs), not "No dust or marks found".
- Tooltip: "Take every heal off this frame, and the dust and marks Find
  found but nothing has painted over yet."

## The toolbar

Dust removal leaves the Area / Gradient group and ends the left-hand
pictures, after Skin (then the centred Film Presets and Capture). Same
action, same tooltip; the toolbar's width is unchanged.

## Tests (`tests/test_dust_clear_found.py`)

The question's wording; heals + found cleared in one undo and both back
with ↶; found alone cleared and the IR line; nothing to clear asks nothing;
No changes nothing; Dust removal right after Skin and before Film Presets
(`tests/test_toolbar_icons.py` order updated).
