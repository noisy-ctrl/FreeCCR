# Right-click Set Black Point / Set White Point: Reset and Auto

The user (2026-10-31): "Can we add right click to b/w points to reset or
auto add".

## UX
Right-click **Set Black Point**:
- **Auto from This Frame's Border** — measures the frame on screen's clear
  film border exactly as Convert does (`find_rebate` on the unconverted
  preview, `CCRBackend.raw_preview`, so a converted frame is not touched) and
  makes it the roll's Black Point: the fallback for frames with no border of
  their own (e.g. tethered shots framed tight, which otherwise wait for one).
  High or medium confidence only; low/none says why and suggests drawing it.
  The measured strip is outlined (`flash_auto_bp_strip`).
- **Reset Black Point** — clears the roll's sampled Black Point, in memory
  AND the saved copy, so it doesn't come back at the next start. Each frame's
  own border still converts it. Disabled when none is set.

Right-click **Set White Point**:
- **Auto for This Frame** — the frame's White Point choice becomes "frame"
  (its brightest near-grey area, spec/white-point-choice.md); a converted
  frame converts again straight away, as changing the White Point combo does.
- **Reset White Point** — drops this frame's hand sample and its choice
  (back to the Settings default); a converted frame converts again. Disabled
  when the frame has neither a sample nor a choice of its own.

The ✕ button is unchanged (clears both).

## Saved Black Point
`MainWindow.persist_bwpoint` now REMOVES `convert/black_point_bgr` when the
Black Point is None. Its only callers are a sample (set) and an explicit clear
(✕, Reset), so a clear sticks; a new batch / New Roll still clears memory only.

## Tests
Auto sets the roll Black Point from a frame with a clear border (synthetic
frame), refuses on a borderless frame and leaves an existing one alone;
Reset clears memory and settings; white Auto/Reset set the choice / clear the
sample, and re-convert only converted frames; menu enabling.
