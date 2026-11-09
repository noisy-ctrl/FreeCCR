# Sharpness meter, revision 2: read near focus, follow drift, see a new frame

The user (2026-10-31):
- "The sharpness tool in tether should read a bit more logarithmically(?).
  There's a huge difference in 5 % between 95 % and 100 %."
- "I also worry about the accuracy considering the frame drifts slightly due
  to focus breathing and the play [in] the linear rail the camera is on. Can
  it take into account a little?"
- "Can the sharpness tool sense when there's an entirely new frame and reset
  (and say this)?"
- Later: he will put a stepper motor on the focusing rail. The meter is what
  an automatic sweep would read, so it must give a steady number with a clear
  peak.

## 1. What is measured: unchanged
`sharpness` (Laplacian variance over mean luma squared) stays. A
contrast-normalised variant (fine-detail energy over the area's own
coarse contrast) was built and measured against it on synthetic film
(8 textures, drifts of 4, 8 and 12 px, area 200 x 160): the change a drift
caused was median 3.0 % / max 9.7 % with it, against 1.9 % / 8.2 % without,
so it was dropped. What removes drift is following it (§3): measured on the
same content, the number only moves with focus and grain.

## 2. Steady (`FocusMeter`)
The meter reads at most 30 times a second (as before) and shows the median of
the last 5 readings (~0.17 s): one noisy frame can't jump the number, and the
best is taken from the steadied value, so noise can't set an unreachable best.

## 3. Following drift (`AreaTracker`, drawn areas only)
Each reading, the drawn area is phase-correlated (Hanning window) against the
same patch from the previous reading; a confident shift (response ≥ 0.15,
under a quarter of the area) moves the area with the picture, accumulated,
capped at half the area's size from where it was drawn so it can never
wander off. Reset whenever the area, the view, the best or the frame
changes. The outline drawn on the picture stays where you drew it (it is the
user's choice; the tracking is a small correction under it). The whole-view
reading isn't tracked (drift barely changes a whole view).

## 4. The readout (stretched near best focus)
Shown as **Focus N** where `N = 100·(1 − sqrt(−ln r))`, `r` = steadied
score / best, clamped 0..100 — a Gaussian focus peak model, so N is roughly
"how far from best focus": 99 % of best reads 90, 95 % reads 77, 90 % reads
68, 50 % reads 17. The bar follows N; the plain "% of best" stays, smaller,
on the right. `focus_closeness(r)` is the one formula.

## 5. A new frame (`NewFrameDetector`)
A 64x36 grey thumbnail (the motion test's) normalised to zero mean and unit
variance; its correlation with the thumbnail at the last reset:
- below 0.4 → the picture is changing (film advanced; a focus change stays
  far above it, as the thumbnail is far coarser than any defocus). A change
  of LED colour on the same frame changes the picture's contrast more than
  its layout and is not expected to go below 0.4 (not measured on a real
  negative yet); if it ever does, the cost is only a reset best;
- then steady (consecutive thumbnails correlate ≥ 0.95) for 0.8 s → a new
  frame: the best and tracking reset, the reference becomes the new picture,
  and the readout says "New frame: best reset" for 3 s
  (`FrameResult.new_frame`). The monitor's caption repeats it.
Magnification and a click on the readout reset it too (they already reset
the best).

## Tests
`FocusMeter`: median of 5,
best from the steadied value, `focus_closeness` table. `AreaTracker`: a
4-px drift is followed and a different picture isn't; capped. Detector: a
blurred copy is not new; a different picture that settles is, once; a still
picture isn't. Canvas: "Focus" text and the new-frame note.

## Revision 1 (2026-10-31): one steady number
The user: "The focus bar is now distracting as it flickers (presumably due to
noise). I only really want one number. Maybe it should reset after camera
zoom is toggled off?"
- The readout is ONE number, "Focus N": no bar, no "% of best".
- `focus_closeness` is now `r^4`: 0.99 → 96, 0.95 → 81, 0.90 → 66, 0.50 →
  6. The `1 − √(−ln r)` stretch had an infinite slope at best focus, so grain
  noise of half a per cent swung it by ~7 points exactly where it is read; r^4
  moves ~2 points for the same noise and still spreads the top.
- `FocusMeter.MEDIAN` 5 → 15 (half a second at 30 readings a second), and the
  canvas changes the shown number at most 4 times a second.
- A magnification change resets the best twice: at once and again 1.5 s later,
  when the new picture is actually arriving (frames already on their way set
  the best at the old scale).

## Revision 2 (2026-11-01): the bar back, short, beside the number
The user: "Crossed wires about the focus meter. I'd like the bar back in,
visually showing percentage. Doesn't have to be mega long though."
- The readout is **Focus N** plus a 72 px bar (`LiveCanvas.READOUT_BAR_W`)
  filled to N % — the SAME shown number, updated at most 4 times a second
  from the 15-reading median, so the bar can't flicker the way revision 2's
  bar (raw score / best) did. Filled light grey, green at N ≥ 95. A note
  ("New frame: best reset") replaces both. The bar is inside the readout's
  box, so a click on it starts a new best as before.
- Test: the bar's fill is N % of its width, follows the number, and gives way
  to the note (`LiveCanvas._readout_bar`).

## Revision 3 (2026-11-07): look inside the frame, not at the carrier
The user's 35 mm recording: "New frame: best reset" never came, so the best
was never reset between frames. The detector looked at the whole view, which
on 35 mm is mostly sprocket rows, rebate and the carrier's mask: the same in
every frame. Measured on the recording (64 × 36 thumbnails, correlation):

| where it looks | frame 1 vs frame 2 | one frame while focusing (lowest) |
|---|---|---|
| the whole view (before) | 0.87 | 0.89 |
| the middle 60 % of the view | 0.11 | 0.84 |
| inside the frame guide | 0.30 | 0.85 |
| the guide's inner 80 % | 0.24 | 0.86 |

On his 120 recordings (focusing and magnifying one picture) the lowest was
0.77 whole / 0.63 middle / 0.78 guide, so `DIFFERENT` stays 0.4.
- It looks at the frame guide's inner 80 % while the guide is shown and the
  camera isn't magnified (`ViewParams.frame_area`, the same guide the Live
  histogram uses), else the middle 60 % of the view
  (`live_feed.new_frame_box`). The guide's turn is left out: a box 80 % of
  its size sits inside it.
- It keeps the reference and the previous picture WHOLE (grey, 320 px wide,
  `new_frame_view`) and cuts the same box from both for each comparison. So
  the guide moving (following the frame, a snap, your hand) is never a new
  picture, and the guide snapping onto the new frame while it settles can't
  swallow the change it follows. No reset on an area change is needed.
- The monitor now sends the guide to the feed whenever it moves (a drag,
  Size / Position / Format, following, a snap). Before, the Live histogram
  kept looking where the guide had been until something else changed.
- Replayed through the detector at the recordings' 4 pictures a second: the
  35 mm recording, said once (before: never); a 120 recording of three
  frames, said three times (before: twice; on the third the whole view still
  correlated 0.55); the 120 recording of one frame, never (before: never).

Tests (`tests/test_live_feed.py`): the box (guide inner 80 %, middle 60 %,
a bad or off-picture guide falls back to the middle); on a synthetic 35 mm
strip, winding on is said once inside the guide and not across the whole
view (two frames there correlate 0.92), and still once when the guide snaps
5 px while the frame settles; focusing (3 % larger and blurred) and dragging
the guide a fifth of the view on a still picture are not said; the Processor
uses `frame_area`. `tests/test_frame_exposure.py`: the monitor sends the
guide as `frame_area`, follows its moves and sends none while magnified.

## Revision 4 (2026-11-09): "__" until focus has varied

The user: after a new frame the readout said 100, "giving the illusion it's
sharp, without new data of moving focus". `FocusMeter` now also keeps the
lowest steady reading since its reset and reports `known` once that has been
at most `KNOWN_BELOW` (0.90) of the best — a real change, well outside the
reading's own noise (~2 %). `update` returns (steady, best, known) and
`FrameResult.sharp` carries it; until known the readout reads "Focus  __".
The bar is drawn as before (full and green at the best). Tests:
`tests/test_live_feed.py::test_the_best_is_known_only_once_focus_has_varied`,
`tests/test_live_canvas.py::test_the_readout_says_dashes_until_focus_has_varied`.
