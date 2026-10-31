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
