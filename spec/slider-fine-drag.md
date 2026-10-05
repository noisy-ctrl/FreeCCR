# Fine slider drag (Shift)

Hold **Shift** while dragging any adjustment slider's handle to move it 10×
slower (`ResettableSlider.FINE_DRAG_FACTOR`), so single steps are easy.

Implementation: `ResettableSlider` drags RELATIVELY (the handle follows mouse
movement from the press, accumulated as a float) instead of jumping to the
absolute mouse position. At factor 1 this is identical to the old feel, and
pressing or releasing Shift mid-drag never makes the handle jump. Clicks off
the handle are still ignored, and double-click now resets to each slider's own
default (`reset_value`, set by `create_slider`).

Tests: `tests/test_slider_fine_drag.py`.
