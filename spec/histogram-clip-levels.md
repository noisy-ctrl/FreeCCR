# Right-click the histogram: scale the sliders to clip 0 % or 2 %

The user, 2026-11-22: "In the main window, with the histogram top right, can
you be able to right click it and have an option that's like 'Scale sliders
to clip 0%' and 'Scale sliders to clip 2%'. With these it adjusts the white
and black points accordingly."

## 1. Goals / non-goals

- One click sets the tone section's **White Point** and **Black Point**
  sliders so the picture just reaches white and black (0 %), or so 2 % of it
  clips at each end (a punchier stretch).
- Non-goals: no other slider moves (Master Gain, Auto Gain, Brightness,
  Contrast, Channel Levels stay); no per-channel stretch (that would change
  the colour); not a standing "auto levels" mode, it is a one-off that sets
  two sliders you can move after.

## 2. UX

- Right-click the histogram (top right of the main window): a menu with
  **Scale Sliders to Clip 0 %** and **Scale Sliders to Clip 2 %**
  (`SlidersPanel.histogram_menu()`, the seam).
- The sliders move (one undo step), the picture re-renders, and the hint
  says what was done: "White Point 2.3 · Black Point 0.8: the picture just
  reaches white and black." / "…: 2 % clips at each end." If a slider ran
  out of travel: "White Point is at the end of its range: 0.4 % still
  clips." (or "doesn't reach white").
- Refused, with a hint, when there is no frame, the sliders are greyed
  (an unconverted negative) or an area layer is being edited (the sliders
  then edit the layer: "Pick Whole Image in Layers first").
- A busy cursor while it works (a closed loop of renders, typically 15–25).

## 3. What "clips" means

The same as the conversion's clip guard (`core/clip_guard.clip_counts`): in
the 8-bit render the preview and histogram use, after a 3×3 median, a pixel
clips white when any channel is 255 and black when any channel is 0. The
median keeps a lone dust speck or hot pixel from deciding it. Shares are of
the picture's pixels:

- the area is the crop (inset by its turn so no outside sliver is read),
  else the picture rect the conversion measured (`conversion_inputs
  ["picture"]`), else the whole frame. The film's border is not the picture.

## 4. The solve (`core/clip_levels.py`, Qt-free; `CCRImage.solve_clip_levels`)

A closed loop on the real render (no model of the pipeline, as for the
neutral solves): `measure(white, black)` renders the frame's preview base
(`resized_raw`) with those two values written over its settings, Auto Gain
measured once on the whole base, dust skipped, and returns the (black,
white) shares inside the area.

Both shares are non-decreasing in their own slider (White Point is a gain
or a remap towards white; stored Black Point raises the black end). On the
slider's own grid (tenths, its range in stored units, ±20):

1. White: the LARGEST White Point whose white share is ≤ the target, by
   bisection (the ends tested first: if even the top is within, the top; if
   even the bottom is over, the bottom, said so).
2. Black: the largest stored Black Point whose black share is ≤ the target,
   with that White Point.
3. The Black Point remap also lifts the highlights a little, so White is
   checked once more at the new Black Point: kept if it is still within and
   one step up is over, else solved again.

Memoised; renders counted and reported. The target is 0 or 0.02 per end.

Speed: the search runs first on a half-size copy of the base (INTER_AREA,
a quarter of the time per render), and its answer is then settled on the
preview base itself (`clip_levels.refine`: a bracket widened by doubling
round the guess, then bisected), so the answer is the full render's.
Measured here (numpy, 1080 px, 0.16 s a render): 3–30 full renders after
~30 small ones, 1.5–4.8 s; a GPU render on the user's Mac is several times
faster.

## 5. Integration

- `HistogramWidget` gets a custom context menu policy; the panel connects
  it to `_show_histogram_menu`.
- The values are set like the Skin pull (`SlidersPanel.set_solved_values`,
  which `on_skin_sampled` now calls): signals blocked, labels updated, one
  `on_slider_changed`, a settle render.

## 6. Tests (`tests/test_histogram_clip_levels.py`)

- `largest_within` on a step function: inside, at both ends, never over.
- `solve` on a synthetic measure where black depends on white: 0 % gives
  shares of 0 with one step up over; 2 % gives ≤ 2 % with one step up over;
  round 3 corrects White after Black lifted the highlights.
- `refine` gives `largest_within`'s answer from any guess.
- On a real converted `CCRImage` (a synthetic gradient negative): after
  "0 %" no picture pixel clips and one tenth more of either slider clips
  some; after "2 %" each end is ≤ 2 % and one tenth more is over (a
  frame's tones may jump past 2 % in one tenth: the answer is still the
  largest within).
- The panel: the menu has the two entries; triggering sets both sliders as
  one undo step and says so; refused while an area layer is active.
