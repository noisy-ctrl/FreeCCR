# Straighten: fine controls

## Goal

Let the crop panel's Straighten be set to 0.1° reliably. The slider already
stores tenths of a degree, but it spans ±45° over roughly 200 px, so one pixel
of mouse travel is ~0.4° and a drag can't land on a tenth.

## UX

Under the Angle slider, a row aligned with the slider column:
`[−]  [ +1.2° ]  [+]`

- **−/+** nudge by `CropPanel.STRAIGHTEN_STEP` (0.1°); holding one repeats.
- The **angle box** (`QDoubleSpinBox`, −45…45, one decimal, no spin arrows)
  takes a typed value, applied on Enter or focus-out; Up/Down step 0.1°.
- It replaces the old read-only value label and always mirrors the slider.

## Integration

The slider stays the single source of truth: the box and buttons only set
the slider, whose existing handler drives `set_pending_straighten`.
`on_crop_geometry_changed` (canvas → panel) updates both under `_suppress`,
so a knob drag is not echoed back.

## Tests

`tests/test_crop_panel.py::TestStraightenFineControls`.
