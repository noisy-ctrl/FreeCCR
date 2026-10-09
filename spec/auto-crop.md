# Auto crop: find the frame, straighten it, crop to the picture

## Goal

On Convert (optionally) or from the Crop panel, find the picture area of each
scan, straighten it, and set the crop to it, so Auto Gain, the histogram and
the levels see only the photograph. Pairs with Auto black point: the same
Convert can measure the film base, convert and crop with no manual sampling.

## Non-goals (v1)

- Learning from corrections (planned v2, see below). v1 only *records* nothing.
- Multiple frames in one scan (that is Slice).
- Positive (slide) scans are not tuned for; detection may still work.
- Changing the conversion, the export path or `apply_crop_to_image`. Auto crop
  only *produces* `crop_rect` / `crop_angle`, exactly like the Crop panel.

## What it has to handle (the test set)

30 scans from three setups, all GFX50S II, 1080 px previews:

| Setup | Look | Hard part |
|---|---|---|
| 35mm in a masking holder (white light) | black holder, rounded window, picture fills it | a leader frame half fogged (dense): the crop must still be the full 3:2 frame |
| 35mm strip, colour negative (white light) | sprocket rows top and bottom, neighbouring frames at the sides | a gate edge where the picture is as thin as the base is invisible |
| 645, B&W and trichrome colour | black holder, then a strip of clear rebate, then the picture | the strongest edge is the holder, not the picture |

The camera sits ~0.4° rotated to the film in every set.

## Method (`src/core/auto_crop.py`)

All on the raw (unconverted) 1080 preview, in log10 luminance `L`.

1. **Straighten.** Find the angle in ±4° that makes the strongest straight
   edges axis-aligned: maximise the peakiness of the column projection of
   |∂L/∂x| and the row projection of |∂L/∂y| (coarse 0.25° then 0.02°).
   Rotate `L` by it (`getRotationMatrix2D`, about the image centre).
2. **Candidate edges.** Per column and per row, the *coverage*: the fraction of
   the line where the gradient exceeds τ = 0.05 log units with one consistent
   sign (gate edges are straight and run the full frame). The 10 strongest
   columns and rows, plus the scan borders, are candidates.
3. **35mm strip detection.** Bright (clipped) runs near the top/bottom that
   form ≥ 4 evenly spaced, evenly sized holes are sprocket rows. On a strip
   the frame must lie between them, must be 3:2 (±1%, weight 15: a strip
   frame that isn't 3:2 isn't a frame), sits centred between the rows (an
   off-centre box is penalised), and a missing gate edge is mirrored from the
   visible one about the band centre. The **gap between frames** is found by
   level, not flatness: columns whose median over the picture rows equals the
   base read from the rebate between the sprocket holes (±0.035 log). Gap
   boundaries become candidate edges, and a box with gap just inside a side
   edge is penalised. (The trim, step 6, is skipped on strips: the rows and
   3:2 already place the frame, and picture at base density would look like
   rebate to it.) A strip frame is always landscape: the sprocket rows run
   along its long side.
3b. **Strip gaps place the side edges** (after the search). A gap is a run of
   ≥ 8 columns that are at the rebate's base level (±0.02 log) AND flat down
   the picture rows (median-filtered std < 0.02), no wider than 10% of a
   frame: a thin picture can match the base level, but rarely stays flat for
   500 rows, and a wider run is thin picture merged with a gap. Two gaps one
   frame width apart (height × 1.51, ±3%; measured 1.48–1.54 gap-to-gap on 24
   strips from two cameras) are the frame. One gap places one side; the other
   keeps the search's edge when that is a real edge (coverage ≥ 0.5) a frame
   width away, otherwise goes at the frame width and is reported **soft**. A
   frame never contains a gap. Sides placed by a gap need no gradient of their
   own for the confidence rules. This is what crops a thin, underexposed
   frame whose gates are invisible, and stops a box taking in the gap.
4. **Rectangle search.** Every candidate pair × pair (each side ≥ 35% of the
   scan) is scored:
   `Σ coverage(edge over its own span) − 4·Σ outside-band roughness + area −
   w·format misfit`. The *outside band* is a 6 px strip beyond each edge,
   tried at several distances so a blurred holder edge doesn't hide it, its
   roughness the standard deviation on a 5 px median-filtered copy (dust and
   hairline scratches are not structure); a band far darker than the picture
   (holder, with vignetting) counts as flat. The scan border scores a token
   coverage (0.1) and a small roughness. Format misfit: distance to the
   nearest of 3:2, 645, 6×6, 6×7, 6×9, half-frame, XPan (either way round),
   with 3% slack (gates and masks vary), weight 3; half-frame and XPan carry
   a small penalty so they never win on proportions alone. Strips: weight 15,
   1% slack, 3:2 only, plus a *format snap* that tries each side at the exact
   3:2 position.
5. **Peel.** When the band outside an edge is far darker than inside (≥ 0.8
   log), that edge is the holder. Walk inward through its blur to the rebate
   plateau; if a second step follows within 5%, that is the gate: move there.
   Otherwise stop at the plateau and mark the edge **soft** (the picture is at
   base density there; the gate is invisible).
6. **Trim.** On every edge, scan border included: past any ramp, a narrow band
   (≤ 3%; ≤ 6% for holder) that is holder-black or at the rebate's level,
   followed by a step to the picture, is cut off.
7. **Inset.** Move each edge inward to where its blur ends (90% of the step),
   then 0.6% of the short side.

Output: `FrameCrop(rect, angle, confidence, soft, strip, fmt, reason)`.
`rect` is normalised in the *unrotated* image, the centre mapped back through
the inverse rotation, and `angle` is the detection angle: `apply_crop_to_image`
samples `C + R(angle)·q` and `R(a)` is exactly the inverse of
`getRotationMatrix2D(a)`, so the crop reproduces the straightened box.

**Confidence.** *none*: the best box is (almost) the whole scan with three or
more border edges (the picture fills the scan, or nothing was found): not
cropped. *low*: two or more edges on the scan border, an edge with coverage
< 0.2, or proportions > 10% from every format: not cropped, reported. *medium*:
a soft edge, one border edge, or an edge with coverage < 0.5. *high*:
otherwise. High and medium are applied.

## UX

- Crop panel: an **Auto** button. It reads the raw preview (re-decoded,
  without touching the conversion, when the frame is converted), detects, and
  puts the result in as the pending box and straighten. Nothing is committed
  until **Done**, so it is reviewable and adjustable; the hint states the
  confidence and any soft edges. A frame it can't find leaves the box alone
  and says why.
- Convert controls: **Auto crop** checkbox under Auto black point
  (`convert/auto_crop`, default off). Convert Current / Convert All then
  detect on the raw just before converting and set the crop after it, high and
  medium only. One undo step per frame removes just the crop (taken after the
  conversion). Convert All ends with a summary: cropped / not cropped (why).
- A crop set this way replaces any previous crop and clears the image's
  micro-rotation (the angle lives in the crop now), as the Crop panel does.

## Integration

- `ccr_backend.auto_crop` (bool), `ccr_backend.last_auto_crop_summary`.
- Detection hooks: `SlidersPanel._on_convert_current_bwpoint`,
  `CCRBackend.apply_bwpoint_to_all_images` and `_apply_auto_bwpoint_to_all`,
  each on `img.resized_raw` while it still holds the raw.
- `CCRBackend.apply_auto_crop(img, result)` applies `rect`/`angle`, clears
  `fine_rotation_angle`, refreshes the preview.
- Crop panel → `ImagePreview.auto_detect_crop()`.
- Catalog: nothing new (crop_rect / crop_angle already persist).

## v2: learning from corrections (planned)

Record, per scan setup, the confirmed crop vs the detected one (locally, with a
Settings view and a Reset). Use the confirmed frame size/position and skew as
priors, so a fixed copy stand converges on its own geometry. Learn only from
confirmed crops, never from unchecked guesses.

## Tests

- Synthetic scans: holder + rebate + picture (645), a strip with sprocket rows
  and neighbours, a fogged half frame, a picture that fills the scan (none), a
  rotated frame (angle recovered to ±0.05°).
- The crop reproduces the box: `apply_crop_to_image(raw, rect, angle)` equals
  the straightened-and-cut box.
- Confidence rules; Convert hooks set the crop only for high/medium and push
  one undo step; Auto button sets the pending box and does not commit.
- `tools/eval_auto_crop.py` reruns the detector on a folder of scans and writes
  the contact sheets used to develop it (real scans stay out of the repo).

## Refinement notes

- Detection always runs on the raw preview, never the converted positive: the
  holder/rebate polarity rules assume negative-scan levels.
- The 1080 preview is enough: a pixel is ~0.035 mm on 35mm; the inset is
  larger than the detection error measured on the test set (≤ 2 px).
- The fogged-leader case is why fog is *picture*, not border: only holder,
  rebate-level bands and clipped holes are border.
- An edge hugging the scan border with no room for an outside band is scored
  like the border itself (nothing vouches for it); otherwise a neighbour
  frame's edge 6 px from the border looked perfectly framed.
- Result on the 30 development scans: all cropped correctly (5 high, 25
  medium — medium is mostly "soft": a side where the picture is as thin as
  the base). ~0.3–0.5 s per frame at 1080 px.
- Second set (20 scans: 19 white-light 35mm strips, partly cut top rows, two
  near-base interiors, plus an IT8 chart slide): before the gap rule one thin
  frame came out portrait and one took in the gap and 35 px of the next
  frame; one more kept 17 px of gap. With it all 20 crop cleanly (16 high);
  the first 30 are unchanged. The IT8 slide crops to the whole target film,
  not the patch grid inside it.
