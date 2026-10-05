# Horizon tool (crop mode)

**Draw Horizon** button in the crop panel's Straighten section. Click it,
then drag a line on the image along something that should be level; on
release the crop box takes the angle that levels that line, and the tool
disarms (one-shot). Lines closer to vertical level to vertical instead, so a
building edge works too. A click without a line (< 8 px) is ignored and the
tool stays armed. Leaving crop mode disarms it.

The line is measured in the un-rotated pixmap space the crop box lives in
(inverse base transform), so coarse rotation and flips are handled. Angle =
`atan2(dy, dx)` folded to [−90, 90) then to the nearest axis within ±45°, in
the box's own convention (the same one the rotate knob uses). Applied through
`set_pending_straighten`, so the slider, −/+ and typed box all follow.

Tests: `tests/test_crop_panel.py::TestHorizonTool`.
