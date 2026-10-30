# Toolbar: workflow order, text-only labels

Status: built 2026-10-30 (the user's request and choice of layout).

The canvas toolbar (`ImagePreview.toolbar`) mixed symbols into text buttons
(◯ Area, ▤ Gradient, ☀ HDR, an orange broom emoji on Dust) and ran in no
particular order (orientation, area tools, view modes, Dust, Convert,
Un-convert, zoom, Export).

Now, left to right, groups split by the toolbar's own separators:

    ⟲ ⟳ ⇵ ⇋ ▦ | Area  Gradient  Dust | HDR  False colour ........ Un-convert  Convert      Export…  Fit ▾

(▦ = sprocket holes white, an icon toggle added later: spec/sprocket-toolbar-toggle.md.)

**Revision 2026-10-30 (the user's request):** Un-convert and Convert moved to
the right-hand end, beside Export, after the stretch, with a plain gap
(`EXPORT_GAP_PX`, 18 px, no separator line) between Convert and Export. The
sprocket icon now starts the toolbar. The actions are still created first in
`ImagePreview.__init__` (other code refers to them) and only placed at the
end. The toolbar still fits at the window's minimum width in a Mac-wide font
(885 of 900 px).

**Second revision, same day (the user's request):** the sprocket icon follows
the rotate / flip icons (it no longer starts the toolbar); the gap between
Convert and Export is wider (`EXPORT_GAP_PX` 18 → 32 px, still no line); and
the zoom box moved from the view group to the far right, after Export
(`ZOOM_GAP_PX` 8 px), as in the Live Capture Monitor — so Un-convert, Convert
and Export sit to the left of it. The zoom box went 78 → 74 px wide so the
toolbar keeps a margin at the minimum width (886 of 900 px).

- **Text buttons carry no symbols**: "Area", "Gradient", "Dust", "HDR".
  Only the icon-only buttons (rotate left/right, mirror vertical/horizontal)
  are pictures. The rule for new toolbar buttons: a picture alone, or words
  alone.
- **Convert** is bold (the main action); Un-convert keeps its red text.
  Since the revision both sit on the right with Export (Un-convert first).
- The zoom box is last, at the far right after Export (second revision);
  HDR and False colour form the view group.
- Every action keeps its attribute, tooltip, checkable state and handler,
  so nothing else changed; the toolbar still fits at the window's minimum
  width (`tests/test_panel_width.py`).
