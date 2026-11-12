# Tone slider ranges: Highlights / Shadows ±150, fine White / Black Point

## Goals
- **Highlights / Shadows** (not the ⅃ / L versions) reach −150..150.
- **White Point / Black Point** cover the range actually used: −20..20 in
  tenths, so the whole slider travel is useful.
- **Black Point reads the same way as White Point**: + lifts the blacks
  (brighter), − crushes them.

## Non-goals
- No change to Highlights ⅃ / Shadows L / Contrast L ⅃.
- No change to what any stored value renders.

## Processing
- Highlights / Shadows: one bump pass beyond ±100 folds the curve (−150
  Highlights makes a mid-grey darker than a darker grey around x = 0.5; +150
  Shadows likewise). Amounts beyond ±100 therefore run as a **second pass**
  on the first pass's result (`_hs_passes`, mirrored in the OpenCL kernel).
  Within ±100 the result is byte-identical to before.
- Known, unchanged: Highlights −100 with Shadows +100 *together* already
  folds mid-greys in a single pass (min slope −0.42). Not addressed here.
- White / Black Point maths unchanged; they already accept fractional values.

## UI / data model
- `FineSlider` (SCALE 10): position = shown value × 10. `sign=-1` shows the
  stored value reversed (Black Point), so **stored values keep their
  meaning** and every existing edit renders identically.
- A stored value outside ±20 (an edit from the old ±100 slider) is kept
  exactly until that slider itself is moved, like `HalfShownSlider`.
- Wheel: 0.1 per notch. The paste dialog shows Black Point reversed too.

## Tests
`tests/test_tone_slider_ranges.py`: unchanged within ±100, monotonic at ±150,
±150 goes further than ±100, FineSlider tenths / reversed display /
out-of-range preservation, paste-dialog display. GPU/CPU parity checked
on a CPU OpenCL runtime (max difference 1/65535).
