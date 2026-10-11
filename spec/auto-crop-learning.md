# Auto crop learning: remember the crops you confirm, and check the roll

Builds on `spec/auto-crop.md` (the v2 item there).

## Goal

Auto crop gets four kinds of thing wrong on real rolls: a border left along
an edge, a cut into the picture, the wrong area (or no crop at all), and crops
that differ from frame to frame. Two pieces of evidence the detector never
used fix most of that:

1. **Your corrections.** A copy stand, holder and camera height make the same
   frame every time, and you correct the same way every time. Remember the
   crops you confirm, per scan setup, and use them as priors.
2. **The roll.** Frames converted together on one setup share a frame size.
   A frame that doesn't match the rest is re-detected at the roll's size.

## Non-goals

- No machine learning model, no cloud, nothing leaves the computer. A small
  JSON file of box geometry (no pixels, no file names).
- Not learning creative crops (see "What counts as a correction").
- Not changing how a crop is stored, rendered or exported.

## What is a setup

`camera make + model | preview width x height | single or merged`. The
preview size pins the camera orientation and the decode; merged (trichrome)
captures are their own setup. Within a setup, different holders and formats
(35mm strip, 35mm holder, 645) separate by **frame size**: examples whose
confirmed box is within 3% on both sides form one *learned frame*.

## What counts as a correction

A crop confirmed with **Done** (or Enter) in the Crop panel, on an image that
auto crop has looked at this session (Convert with Auto crop on, or the
panel's Auto button), whose box covers at least 25% of the scan. Done with
the auto box untouched counts too: that confirms it. Manual cropping on an
image auto crop never saw is not learned, so a purely creative crop never
teaches it. Once an image has taught a frame, a later Done on it updates that
example only when every side moved less than 4% (a refinement); a bigger
change is a creative re-crop and is ignored.

Stored per example (all in preview pixels of the unrotated scan):
the confirmed box (centre, size, angle), the detection it started from
(box, angle, the search's gate-box size, confidence, strip flag, format) when
there was one, and a timestamp. 120 examples per setup, oldest dropped.

## Learned frame (per cluster)

- **size**: median confirmed size; **centre**: median confirmed centre;
  **spread**: robust spread of the centres (1.4826 x MAD; 5% of the scan when
  there is one example; floor 1%).
- **habit**: per side, the median inward shift from detection to your box,
  as a fraction of the detected box (positive = you pulled that edge in). Only
  from sides the detector SAW (a soft side is placed by your frame size
  instead, below) and from examples where every seen side moved less than 6%
  (a habit, not a rescue of a wrong detection); also the median angle change.
  Clamped to ±6% / ±1°. The detection it is measured against is the box
  BEFORE any learned habit, so a habit is learned in full, not as a residual.
- **out**: the detector's box for this frame before the habit,
  `size / (1 − habit L − habit R)` per axis.
- **gate**: the size the detector's search box has when it finds this frame:
  confirmed size + the detector's own shrink (median gate − output over
  examples, 2 x (inset + 5 px) by default) + both habit shifts in pixels.

## How detection uses it

`detect_frame(raw, priors=[...])`, each prior in the deskewed frame:

1. **Candidates.** For each learned gate size, every candidate column
   proposes a box of that width starting or ending there (and the learned
   centre proposes one), crossed with the same for rows: ~25 x 25 extra boxes
   per learned frame, not a bigger edge list (that would square the search).
   So a frame with one invisible side still has the right box on the table.
2. **Score.** A box within 3% of a learned gate size scores
   `+1.0 x (1 − size error / 3%) + 0.5 x position prior` (Gaussian on the
   learned centre and spread), and is not penalised as an unknown format: your
   frame is a format. Everything else in the score is unchanged.
3. **Support.** A learned size alone is not a frame: the match only counts
   when at least two sides are vouched for by the scan (a seen edge, a flat or
   holder band outside, a strip gap). Otherwise the result is the detector's
   own (and usually unusable, which leads to the fallback).
4. **Soft sides.** A side whose gate couldn't be seen sits at the start of
   the rebate, so it can only be too far OUT. When the other side on that
   axis was seen, the soft side moves INWARD to your frame size (within 8%);
   never outward.
5. **Habit.** Then each edge is moved by your habit and the angle by your
   angle change.
6. **Confidence.** A match lifts *low* to *medium* (your frame size is strong
   evidence); *none* stays none.
7. **Fallback.** When nothing usable is found and a learned frame sits still
   (≥ 2 examples with spread ≤ 3% of the scan, or one example off a strip),
   your usual box is used as it is: *medium*, "your usual frame".

The result says so: `FrameCrop.learned` and "matched your frame" /
"your usual frame" in the summary.

## Roll check (Convert All with Auto crop)

Detect every frame first (Convert All already does with Auto black point; the
manual-black-point path now does too), then: with ≥ 3 usable frames whose gate
sizes agree within 3% (at least half of them), their median is the roll's
gate size. A frame that is unusable or more than 3% off it is re-detected with
the roll size as a prior (size only, no position). A frame of the right size
with a soft side has that side moved inward to the roll's output size, per
axis, measured only from frames that saw both gates on that axis (≥ 2 of
them; with fewer the axis is left alone). The summary counts these ("3
re-found at the roll's frame size"). The raw scans are still in memory at
that point, so this costs one extra detection (~0.4 s) per odd frame.

On the development scans this changes only one frame by more than 0.3%
(1070: its invisible right gate pulled in 2%, matching the roll). An earlier
version that also moved soft sides OUTWARD, and measured the roll size from
soft frames, pushed two holder frames' edges onto the holder; hence the
inward-only, both-gates-seen rules.

## Re-crop all frames

A button under the Auto crop checkbox: after correcting a frame or two, run
auto crop again on the rest with what it has learned and the roll check,
without re-converting (~0.9 s a frame: a 0.44 s half-size decode for a
converted frame, plus detection). It leaves alone every frame you confirmed
with Done or cropped by hand (a crop is "auto crop's" only while it equals
the box auto crop set, `img.auto_crop_rect`); an unchanged result pushes no
undo step. Cancellable; the summary is the Convert All one.

## Settings

General → **Auto crop**: "Learn from my crops" (on by default,
`crop/learn`), a line saying how many frames are learned on how many setups,
and **Forget learned crops** (asks first). File:
`<APPDATA or ~/.config>/FreeCCR/auto_crop_memory.json`.

## Integration

- `core/crop_memory.py`: store, setup key, `LearnedFrame`, `record`, `priors`.
- `core/auto_crop.py`: `priors` argument; `FrameCrop.geom` (gate / output
  boxes) and `.learned`.
- `CCRBackend.detect_frame_crop(img, raw, extra_priors)` looks up the priors
  and keeps the result on the image (`img.auto_crop_last`);
  `learn_confirmed_crop(img)`; `_roll_consistent_crops`.
- `ImagePreview.confirm_crop` calls `learn_confirmed_crop` after committing.

## Tests

- Store: round trip, cap, key, Forget.
- Habit: synthetic scans where the "user" always pulls the left edge in 3% →
  after two confirmations the next detection lands within 0.5% of that.
- Wrong area: a scan whose unconstrained best box is wrong is fixed once the
  frame size is learned; a scan of a *different* format on the same setup
  still finds its own frame (no false pull).
- Fallback: a blank-edged scan with a learned holder frame gets the usual box.
- Creative crops: a small box, an image auto crop never saw, and a >4% re-crop
  after a confirmation are not learned.
- Roll: one odd frame in a batch of strips is re-detected at the roll size.
- Regression: all 50 development scans unchanged with an empty memory.

## Refinement notes

- Extra *boxes*, not extra *edges*: adding each learned width to the edge list
  would take the pair search from ~200 to ~600 pairs per axis (~9x the work);
  learned boxes cost ~600 score calls per learned frame (~10 ms).
- The habit is measured against the detector's OUTPUT box and applied to it,
  so it captures both your margin preference and any systematic detector
  offset on that setup; the gate size for the search is derived separately so
  the search still looks for edges where they are.
- Strip gap anchoring (spec/auto-crop.md 3b) runs as before and also scores
  with the learned bonus, so a learned strip frame and the gaps agree.
- A prior is converted into the current scan's deskewed frame (centre through
  the same rotation as the scan); sizes are box-frame sizes, so the small angle
  difference between scans does not matter.
- Roll priors carry no position (frames move along a strip), only size.
- Empty memory = exactly the old behaviour (regression-checked on all 50 scans).
