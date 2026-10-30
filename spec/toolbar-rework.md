# Toolbar: workflow order, text-only labels

Status: built 2026-10-30 (the user's request and choice of layout).

The canvas toolbar (`ImagePreview.toolbar`) mixed symbols into text buttons
(◯ Area, ▤ Gradient, ☀ HDR, an orange broom emoji on Dust) and ran in no
particular order (orientation, area tools, view modes, Dust, Convert,
Un-convert, zoom, Export).

Now, left to right, groups split by the toolbar's own separators:

    ▦ | ⟲ ⟳ ⇵ ⇋ | Area  Gradient  Dust | Fit ▾  HDR  False colour ........ Un-convert  Convert    Export…

(▦ = sprocket holes white, an icon toggle added later: spec/sprocket-toolbar-toggle.md.)

**Revision 2026-10-30 (the user's request):** Un-convert and Convert moved to
the right-hand end, beside Export, after the stretch, with a plain gap
(`EXPORT_GAP_PX`, 18 px, no separator line) between Convert and Export. The
sprocket icon now starts the toolbar. The actions are still created first in
`ImagePreview.__init__` (other code refers to them) and only placed at the
end. The toolbar still fits at the window's minimum width in a Mac-wide font
(885 of 900 px).

- **Text buttons carry no symbols**: "Area", "Gradient", "Dust", "HDR".
  Only the icon-only buttons (rotate left/right, mirror vertical/horizontal)
  are pictures. The rule for new toolbar buttons: a picture alone, or words
  alone.
- **Convert** is bold (the main action); Un-convert keeps its red text.
  Since the revision both sit on the right with Export (Un-convert first).
- The zoom box joins the view group (zoom, HDR, False colour); Export stays
  at the far right, after the stretch.
- Every action keeps its attribute, tooltip, checkable state and handler,
  so nothing else changed; the toolbar still fits at the window's minimum
  width (`tests/test_panel_width.py`).
