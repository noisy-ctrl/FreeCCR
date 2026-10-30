# Toolbar: workflow order, text-only labels

Status: built 2026-10-30 (the user's request and choice of layout).

The canvas toolbar (`ImagePreview.toolbar`) mixed symbols into text buttons
(◯ Area, ▤ Gradient, ☀ HDR, an orange broom emoji on Dust) and ran in no
particular order (orientation, area tools, view modes, Dust, Convert,
Un-convert, zoom, Export).

Now, left to right, groups split by the toolbar's own separators:

    Convert  Un-convert  ▦ | ⟲ ⟳ ⇵ ⇋ | Area  Gradient  Dust | Fit ▾  HDR  False colour ........ Export…

(▦ = sprocket holes white, an icon toggle added later: spec/sprocket-toolbar-toggle.md.)

- **Text buttons carry no symbols**: "Area", "Gradient", "Dust", "HDR".
  Only the icon-only buttons (rotate left/right, mirror vertical/horizontal)
  are pictures. The rule for new toolbar buttons: a picture alone, or words
  alone.
- **Convert** comes first and is bold (the main action); Un-convert keeps
  its red text.
- The zoom box joins the view group (zoom, HDR, False colour); Export stays
  at the far right, after the stretch.
- Every action keeps its attribute, tooltip, checkable state and handler,
  so nothing else changed; the toolbar still fits at the window's minimum
  width (`tests/test_panel_width.py`).
