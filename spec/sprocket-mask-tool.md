# Spec: Sprocket Mask tool (paint sprocket holes to the background colour)

Status: DRAFT for review (2026-10-26). Open questions in §11.
Owner: FreeCCR
Feature branch: `feature/sprocket-mask-tool`
Related: `spec/sprocket-hole-mask.md` (the automatic reversal-look toggle this
grows out of), `spec/export-canvas-border.md` (the Border colour the holes
take on export), `spec/dust-removal.md` (the brush, its canvas mapping and
stroke storage, reused here).

## 1. Summary

A **Sprocket Mask** section at the very bottom of the adjustments panel,
shown when it is enabled in Settings. With it you paint over the sprocket
holes (or anything else) and the painted area is cut out of the picture: on
export it takes the **Border colour** when the Export window's Border is on,
and **white** otherwise. The preview shows the same thing.

A rough stroke along a row of holes is enough: with **Snap to the holes** on
(default), the mask keeps only the clear film inside the stroke, so each hole
comes out with its own crisp edge. **Find Holes** fills the mask on every
clear-film region of the frame in one go (what the old Settings toggle did to
every frame), and the brush's eraser takes out what it shouldn't have.

## 2. Goals / Non-goals

### Goals
- Settings → General → Film border: **Sprocket mask tool** (tick box). On:
  the Sprocket Mask section appears at the bottom of the panel (below
  Details).
- Paint and erase on the canvas with a round brush; per frame; undoable;
  saved in the catalog.
- Snap to the holes: inside a stroke, only clear film is masked, with the
  hole's own edge.
- Find Holes: the whole frame's clear film, as a starting point.
- Copy a frame's mask to the selected frames or the whole roll; with snapped
  strokes the holes are found again on each frame, so a little drift between
  frames doesn't matter.
- The fill: the export's Border colour if the Border is on, else white; the
  preview shows the fill the export will use.
- The standing quality rules (CLAUDE.md, "Finishing tools must keep the
  picture's quality"): exact no-op without a mask, anti-aliased edges whose
  width scales with the render, preview == zoom == export, nothing outside the
  mask changes by a single code.

### Non-goals
- Detecting sprocket holes by shape or fitting rounded rectangles (the clear-
  film test already gives a hole's true outline).
- A mask that affects grading (it is a cut-out, applied after every
  adjustment, not an area layer).
- Feathered/soft brushes in v1 (hard edge with anti-aliasing only; holes are
  crisp). See §11 Q4.

## 3. UX

### 3.1 Settings
The "Film border" group's tick box becomes:

> ☐ **Sprocket mask tool**
> *muted:* "Adds a Sprocket Mask section at the bottom of the panel. Paint
> over the sprocket holes and they are cut out: they export as the Border
> colour, or white when there is no border."

The old "White sprocket holes / clear film (reversal look)" box goes (its
behaviour is the section's Find Holes, §3.2). Migration in §6.

### 3.2 The section (bottom of the panel, below Details)
A `CollapsibleSection("Sprocket Mask")`, open by default:

```
[ Paint ] [ Erase ]                        (checkable, one at a time)
Brush      ───────●──────  0.8 %            (Ctrl+wheel on the canvas too)
☑ Snap to the holes
[ Find Holes ]  [ Clear ]
[ Copy to Selected ]  [ Copy to All ]
Fill  ■ #ffffff  white (no border)          (or "■ #1a1a1a  Border colour")
```

- **Paint / Erase** put the canvas in mask mode (like Dust): the brush ring
  follows the pointer, a drag paints (or erases), Alt/Option-drag does the
  other one, Esc or clicking the active button again leaves the mode. While
  in the mode the mask is tinted magenta on top of the picture so its edge
  can be seen against a white border; the tint is a display layer (like the
  tool masks of Golden Highlights etc.), never in the pixels.
- **Brush** is a fraction of the frame's width (the dust brush's units),
  0.2–8 %.
- **Snap to the holes** applies to new strokes (each stroke remembers its own
  setting). Tooltip: "Inside what you paint, only clear film is masked, so a
  rough stroke over a row of holes masks exactly the holes."
- **Find Holes**: masks every clear-film region of this frame. One undo step.
- **Clear**: removes this frame's mask. One undo step.
- **Copy to Selected / Copy to All**: copies this frame's mask (strokes and
  Find Holes) to other frames, asking first for All; one undo step per frame.
- **Fill** shows the colour the export will use (read from the Export
  window's saved Border settings). It is not chosen here.
- The section is also shown on a frame that HAS a mask while the tool is off
  in Settings (the Layers rule), so a mask can never be invisible and
  uneditable.

### 3.3 What the mask looks like
- Preview, thumbnails, contact sheet, hi-res zoom: the fill colour.
- Histogram: the unmasked picture (as the reversal-look mask does today), so
  a large white border doesn't spike it. Scopes show what is on screen.
- Export: the fill colour, under every format; in the HDR gain-map JPEG the
  masked area never glows (gain 0, like the border).

## 4. Data model
- `CCRImage.sprocket_mask`: None, or
  `{"auto": bool, "strokes": [{"op": "paint"|"erase", "pts": [[x, y], …],
  "r": float, "snap": bool}, …]}`.
  Points are in the dust tool's space: normalised to the full, un-cropped,
  un-rotated frame (x over width, y over height); `r` is a fraction of the
  width. `auto` is Find Holes.
- Catalog: additive key `sprocket_mask` (absent = None); copied on Duplicate;
  part of the undo snapshot.
- `ccr_backend.sprocket_tool` (bool), QSettings `adjust/sprocket_tool`.
- Not in Copy/Paste Settings or Sync in v1 (Copy to Selected/All covers it).

## 5. Processing

### 5.1 The clear-film map
The hole outline comes from the SCAN, where clear film is the brightest thing
in every channel (in the converted picture holes, rebate and deep shadow all
sit near black). `compute_sprocket_alpha` (spec/sprocket-hole-mask.md §4.1)
already does this against the film base and is computed at every B/W-point
conversion, zoom decode and export. It is generalised to every frame:
- with a film base (any conversion with a black point, i.e. almost every
  negative now that auto black point is always on): unchanged;
- without one (unanchored or reference conversions, Positive mode,
  unconverted scans): threshold each channel at the frame's measured clear
  level less half of `MIN_CLEAR_D` (0.05 density), which only clear film
  reaches.
The preview's map is cached at conversion as now (`sprocket_alpha`, renamed
`clear_alpha`); zoom and export compute their own from their own decode.

### 5.2 Rasterising the mask (`core/sprocket_mask.py`, pure, thread-safe)
`mask_alpha(mask, h, w, clear) -> float32 (h, w) in [0, 1] or None`:
1. `m = clear` if `auto` (and a map exists), else zeros.
2. Each stroke, in order, over its own bounding box only: the footprint is
   the round-capped polyline of radius `r·w`, anti-aliased from the distance
   to the centre line: `s = clip((r·w − d) / f + 0.5, 0, 1)`, `f` = 1 px at
   the 1080 preview, scaled with the render (so the edge is the same width on
   the picture at every resolution).
   - snap: `s ← s · clear` (no map: the stroke as painted);
   - paint: `m ← max(m, s)`; erase: `m ← m · (1 − s)`.
3. All zero → None.

### 5.3 Compositing
The last look step, where the reversal-look mask is applied today: after
every adjustment, dust, area and finishing tool, before crop and orientation
(so the stored points need no transform). In float32:
`out = img·(1 − a) + fill·a`, with `fill` = the Border colour as the canvas
paints it (8-bit sRGB × 257, the picture's own encoding), or 65535. Pixels
with `a = 0` are returned untouched. The colour-space conversion then treats
the holes and the border identically, so they match exactly.
- HDR: the alpha goes through the same crop and orientation as the HDR map
  (`hdr_geometry`) and the glow is multiplied by `1 − a`.
- A monochrome frame keeps its single-channel file only on a neutral fill
  (the Border's rule).
- Preview: the fill is read from QSettings `export/canvas_on` /
  `export/canvas_colour`; changing them in the Export window re-renders
  frames that have a mask.

## 6. Migration of the reversal-look toggle
`adjust/sprocket_mask_white = true` at startup: `adjust/sprocket_tool` is set
on, and every frame converted with a film base and no `sprocket_mask` of its
own renders as if it had `{"auto": true}` until it is edited, so existing
work exports unchanged (white holes; the Border colour if a Border is on is
new, which is the point). The old key is then left alone. See §11 Q1.

## 7. Integration points
- `core/sprocket_mask.py`: `mask_alpha`, `stroke_alpha`, `fill_value`,
  `composite`, `copy_mask`.
- `core/ccr_processor.py`: `compute_sprocket_alpha` without a base (§5.1);
  every export tail (B/W-point, reference, positive, unanchored) composites
  through `sprocket_mask.composite` instead of `apply_sprocket_mask`;
  `write_export_image` takes the alpha for the HDR glow.
- `core/ccr_image.py`: `sprocket_mask`, `clear_alpha`; the preview composite
  in `render_preview_pixels` / the snapshot; `render_hires_base` returns the
  clear map for every mode.
- `widgets/image_preview.py`: mask mode (press/move/release, cursor ring,
  Alt = the other op, Ctrl+wheel = size, Esc), the magenta tint layer, the
  hi-res worker compositing it, `_current_adj_sig` including the mask and
  fill.
- `widgets/sliders_panel.py`: the section (last, after Details), its
  visibility rule, the buttons.
- `widgets/settings_dialog.py`, `ui/main_window.py`: the tool tick box and
  QSettings; re-render on fill change.
- `core/catalog.py`, undo snapshot, Duplicate: `sprocket_mask`.

## 8. Edge cases
- A crop that excludes the holes: nothing masked shows (expected).
- Slices (Slice): each child starts without a mask (strokes are in the
  parent's frame); Find Holes on the child.
- Dust healing: the mask comes after it, so a heal next to a hole works on
  the unmasked picture, as now.
- Glow: masked after Glow, so a white hole never blooms into the picture.
- A stroke entirely outside the frame: ignored.

## 9. Test plan (`tests/test_sprocket_mask_tool.py`)
- No mask → the render is byte-identical (preview, zoom, export).
- Stroke rasterisation: footprint radius, round caps, AA edge 0 < a < 1 over
  ~1 px at 1080 and ~4 px at 4× (same width on the picture); erase removes.
- Snap: a rough stroke over synthetic holes on base masks exactly the holes
  (IoU > 0.97 with the true holes), rebate untouched.
- Clear map without a base: holes found, a bright near-clear patch that is
  not clear in every channel is not.
- Composite: a = 0 pixels untouched; a = 1 → fill; mid a = exact blend; the
  fill equals the canvas's own fill code for the same colour.
- Preview == export: the same mask at 1080 and full size agree (area within
  0.5 %, edges within 1 px at 1080 scale).
- HDR: masked pixels have gain 0.
- Catalog round trip, Duplicate, undo/redo, Copy to Selected/All.
- Migration: the old toggle on → old frames still export white holes.
- UI: section hidden/shown by the setting and shown on a masked frame; Paint
  mode stroke lands in the right place on a cropped, rotated, flipped frame.

## 10. Quality notes
The mask never changes a pixel it doesn't cover (returned exactly), works in
float32, has anti-aliased edges of a fixed width on the picture at every
resolution (it is a cut-out, not a tonal weight, so a smooth weight curve is
not the model; an edge that doesn't stair-step is), cannot clip (a blend of
two in-range values) and is decided on the scan, so grain in the picture
cannot steer it.

## 11. Open questions (for the user)
1. **The old "White sprocket holes (reversal look)" setting**: replace it
   with this tool (Find Holes does the same per frame, and §6 keeps old work
   unchanged)? Or keep both?
2. **Real transparency**: also offer a TIFF with an alpha channel (holes
   transparent, for placing on a background later), or always fill?
3. **Snap default**: on (recommended: a rough stroke gives exact holes)?
4. **Soft edge**: hard (anti-aliased) only, or a Softness slider too?
