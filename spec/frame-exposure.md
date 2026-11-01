# Each frame's own exposure, metered inside the frame guide

Status: specified and built 2026-11-01.

The user (2026-11-01): "The exposure ratios/amounts are balanced to the film
base. But this doesn't take into account the density of the exposed negative
on a frame by frame basis. If we set the frame guide, can the waveform look
only at that, and can the software suggest longer/shorter shutter speeds (or
more or less light) to optimise for the frame? Obviously, when zoomed, this
assessment pauses and isn't affected."

His answers: the **Live histogram** looks inside the guide; a **live estimate
plus a Meter button** (one RAW test shot per colour, exact); **suggest, with
one click to use it for the next Shoot**; and **yes**, conversion scales the
roll's black point by each frame's own exposure.

## 1. What is suggested, and why it is nearly always longer
A film base's exposures put the base (the thinnest film) at `TARGET` 0.85 of
the RAW's range. A frame's picture is denser than the base everywhere, so its
brightest part in the scan (its thinnest density) sits below the base. The
suggestion lifts that part to `TARGET`: an expose-to-the-right per frame,
which lifts a dense negative's highlights (the darkest, noisiest part of the
scan) out of the noise. It is shorter only when something inside the guide is
brighter than the base (light round the holder, a pinhole).

Only the **shutter speed and ISO** change per frame, never the LED level: a
file records its shutter and ISO, not the light, and conversion needs to know
each frame's exposure (§5). The film base's own ladder applies (`plan_exposure`:
ISO 100 up to 1 s, then ISO up to two stops, then longer, 30 s ceiling); a
frame that would need shorter than 1/250 s at the base ISO stays at the
shortest speed with a note.

## 2. The Live histogram inside the guide
With the frame guide on, the Live histogram is made from the whole live frame
inside the guide (its rotated box, `frame_meter.guide_mask`), not from what is
on screen, so the window's zoom and pan don't change it. While the camera is
magnified (Camera zoom not Whole) it pauses (the picture is a part of the
frame) and its caption says so. Guide off: as before (the view).
`ViewParams.hist_area` (the guide dict or None) and `hist_paused`.

## 3. The live estimate (`core/frame_meter.live_estimate`)
A ratio within ONE live picture, so the camera's live view brightening and
gain cancel: the film base patch (the Film Base click, `_neg_base_pos`, a 3 %
square; it must lie outside the guide) against the frame's thinnest part, the
99.5th percentile of each channel inside the guide on a 3×3-blurred copy
reduced to ≤ 640 px. Both are taken to linear light by the sRGB curve (the
live view's real curve is the camera's own; the estimate says "≈"). With one
factor for all colours (the live view is lit white; the shots are narrowband,
so its channels are not the shots'): `r = max_c(frame_c / base_c)`, so no
colour goes past the base's place. Refused with a reason: no base click, base
inside the guide, base clipped in the live view (≥ 250), base too dark (< 12),
guide off, magnified. Every second while the guide is on, the median of the
last 5 estimates is shown. The plan aims ⅓ stop under `TARGET`
(`LIVE_MARGIN_STOPS`), as a live view can be that far off.

## 4. Meter (exact, `FrameMetering`)
**Meter** takes one RAW test shot per colour at the film base's exposures (its
LED levels, its shutter and ISO), with live view held, through the film-base
measuring machinery (same temp folder, hold, put-back and Stop rules; the shot
never reaches the hot folder). Each file is read inside the guide
(`read_frame`): per colour photosite, block means of 16×16 photosites, black
subtracted, as a share of black-to-white, read in strips so memory stays
small; the 99.5th percentile of the blocks inside the guide is that colour's
thinnest level. Per colour: `k = level / E` (level per ISO-100 second) →
`plan_exposure(k, …, target TARGET)`. A level ≥ `CLIP_TRUST` (0.95) is
clipped: the plan is a lower bound and the note says to meter again.
Trichrome: three shots (~15 s on the GFX); one exposure: one.

## 5. Conversion follows each frame's exposure
A frame shot longer than the film base clips its rebate, so auto black point
can't read it there and falls back to the sampled or roll black point, which
was read at another exposure. FreeCCR now knows each frame's exposure
(`CCRBackend.image_exposure`: EXIF shutter × ISO / 100 via
`ccr_merge.shot_exposure`; a 3-way merge is normalised to its shortest
colour, so its exposure is that minimum) and:
- `set_black_point(bgr, exposure)` records the exposure of the frame it was
  sampled on (`black_point_exposure`, persisted as
  `convert/black_point_exposure`); both sampling routes pass it.
- The fallback is scaled by `E_frame / E_black` (`resolve_auto_black_point`,
  and plan_frame_conversion without auto black point).
- The roll consensus is formed exposure-normalised: Convert All scales each
  frame's own base to a common exposure before `roll_check` and back to each
  frame's; `recorded_roll_black` scales the others' bases to this frame's.
- An unknown exposure on either side = no scaling (exactly the old result).
The white point follows (it is scaled by the black point's ratio), and the
recorded `["bw"]` is what was used, so replay is untouched.

## 6. The monitor
Under the Film base row (shown with it): **This frame** line + **Meter** +
**Use for This Frame**.
- The line: "This frame: ≈ 1⅓ stops longer (live view)" / "This frame,
  metered: R +1.0 · G +1.3 · B +0.7 stops" / the reason there is none.
- Use for This Frame: the next Shoot uses the frame's exposures
  (`_frame_exposures`, one Shoot only, then back to the film base's), and the
  caption says so. Off when the suggestion is under ⅙ stop from the base's.
- Meter needs: a USB camera with settable shutter, a film base measured for
  this scan type, the guide on, not magnified, the Scanlight for trichrome.
- A new frame (the meter's new-frame detector) or a guide change clears a
  metered result and an unused "Use" (they describe the old frame).

## 7. Tests
`frame_meter`: guide mask follows a rotated guide; guide histogram counts only
inside it; live estimate on synthetic frames (a base strip and a frame whose
brightest part is 1 stop under it → ≈ 1 stop; refusals); `read_frame` on a
synthetic Bayer mosaic (block percentile, black/white, strips); suggestions
through `plan_exposure` (longer, ISO step past 1 s, shorter capped at 1/250,
the live margin). `FrameMetering` state machine. Processor: `hist_area`
histogram from the full frame, unchanged by view zoom; paused. Conversion:
exposure ratio from EXIF (single, merge), sampled fallback and roll consensus
scaled, unknown → unchanged. Monitor: the line, Use for This Frame feeds the
next Shoot once, Meter end to end with a fake camera and reader, paused while
magnified.

## Refinements (second pass)
- The suggestion is shown only when the selected film base was measured for
  the current scan type (`exposures_for` not None); otherwise the line says
  "Measure the film base first".
- Stops are per colour, `log2(E_new / E_base)` with `E = exposure_of(shutter,
  iso)`, shown to ⅓ stop ("+1⅓", "−⅔", "±0").
- "Use for This Frame" works whether or not "Shoot at these exposures" is
  ticked (it is a one-off): `_shoot_exposures` returns `_frame_exposures`
  once, then clears them.
- The live estimate runs on the GUI thread on a ≤ 640 px copy every second
  (`FRAME_ESTIMATE_MS` 1000); a few milliseconds.
- `read_frame` returns a `base_exposure.Reading` (levels per channel letter),
  so `FrameMetering.landed` reads it like the film base's readings.
- Both measurements read `METER_INSET` (3 %) in from the guide's sides, and a
  RAW block counts only when all of it is inside: a guide laid a little over
  the rebate (or a block straddling the gate) would otherwise read the base
  and suggest no change. The histogram shows the guide exactly. On a reduced
  live frame the mask is also eroded by one pixel (an edge pixel there blends
  inside and outside).
- With any frame's exposure unknown, the Convert All roll check scales
  nothing (exactly the old one); with all known it no longer mistakes a frame
  shot longer for light round the film (tested).

## Revision 1 (2026-11-01): Meter says what it needs instead of greying out
The user: "The per frame meter option is greyed out... How can I use?" Meter
was disabled whenever the film base wasn't measured for the current scan
type, with the reason only in the small line above it. Now Meter stays
clickable (off only while a shot, measuring or another metering runs) and its
tooltip ends "First: …" with what is missing (`_meter_blocker`: a film base
for this scan type — naming one measured for the other type — the USB camera,
the Scanlight for trichrome, the frame guide, Camera zoom back to Whole); a
press says the same in the caption. Use for This Frame's tooltip says why
there is nothing to use yet. The line names the scan type it needs.

## Revision 2 (2026-11-01): no click, no photos, fewer buttons
The user: "Can software not guess, based on white light levels and
histogram? Or at least there be a quick option without photos needing to be
taken. I'd also like to have fewer buttons on the UI. Maybe the full version
(that takes photos) could be enabled in settings."

The live estimate already took no photos; what held it back was the Film
Base click it compared the frame with. Now it finds the film base itself,
beside the frame guide, in the same live picture. The live view is lit
white, and the comparison is a ratio inside one picture, so the light's level
and the live view's own brightening cancel. Without the base in the picture
there is nothing to compare with: the live view's absolute level is the
camera's choice (it brightens a dense frame), so it says so rather than
guessing.

### The film base beside the guide (`frame_meter.find_base_beside`)
On the ≤ 640 px copy, 3×3-blurred, in the guide's own (turned) axes:
- On each of the four sides, the band from `BESIDE_NEAR` (1.5 %) to
  `BESIDE_FAR` (5 %) of the guide's short side OUTSIDE it: past a guide laid
  on the gate's edge, short of 35 mm sprocket holes (~8 %), inside the gap
  between frames on a strip (~8 %).
- Each band's middle (`BESIDE_CORNER` 8 % off each end, away from the
  corners) in `BESIDE_SEGMENTS` (6) pieces; a piece needs `BESIDE_MIN_PX`
  (12) pixels on the picture.
- A piece is a base candidate when, per channel in linear light: even
  (IQR / median ≤ `BESIDE_EVEN` 0.10), unclipped (≤ 2 % of its pixels at
  `BASE_CLIP` 250 in any channel), not too dark (median ≥ `BASE_DARK` 12),
  and at least as bright as the frame's thinnest part
  (`max_c(frame_c / piece_c)` ≤ `BESIDE_SLACK` 1.10): the base is the
  thinnest film, so anything darker than the frame's brightest part is
  picture or holder.
- Candidates are grouped (greedy, brightest first) when every channel is
  within `BESIDE_AGREE` (12 %) of the group's first. A group counts only when
  its pieces are on at least TWO sides (as auto black point's "high"
  confidence: one bright edge on a real negative can be bare light). Of
  those, the group with the most pieces; a tie goes to the darker (film,
  not light past it). The base = per-channel median of its pieces' medians.
- Nothing found: the reason names what was seen ("clipped in the live view"
  when pieces were rejected for that and none passed, otherwise "couldn't
  find the film base beside the frame: lay the guide on the frame's edges,
  or click the base once").

### Which base
A Film Base click (Negative Preview) still wins: it is the user's own
choice. With none, the base beside the guide. `LiveEstimate.source` is
"click" or "beside"; the line says "(live view)" as before, and its tooltip
says which base it compared with.

### Fewer buttons
**Meter** (the RAW test shots, §4) is hidden unless Settings → Live Capture
→ "Show Meter: this frame's exposure from RAW test shots" (`live/frame_meter`,
default off). The row is then the line and **Use for This Frame** only. The
setting is read when the line refreshes (every second while the film base
row shows), so it applies without reopening the monitor; a metering already
running keeps its button until it finishes.

### Refinement (second pass)
- The base piece medians are taken on the blurred copy but the evenness on
  the same pixels: the blur is what makes an 8-bit live view's noise (σ ≈ 2
  levels) pass 0.10 at a base as dark as ~40/255.
- The local axes grid (`lx`, `ly` per pixel) is computed per call (≈ 0.3 M
  floats at 640 × 427: a few ms once a second; no cache to go stale).
- The guide may run off the picture: pieces outside it simply have no
  pixels. A guide covering the whole picture finds nothing (and says so).
- `live_estimate(bgr, guide, base_pos=None)`: the click path is unchanged
  (its refusals included); `find_base_beside` is only the no-click path.
- Use for This Frame's "nothing yet" tooltip no longer tells the user to
  click the base or press Meter; it gives the line's reason.

### Tests
A synthetic live frame: a picture inside the guide, an even base band round
it (with a dark lettering patch on one side and bare light further out) →
found, ratio as with a click on the band; rotated guide; one side only → not
found; base darker than the picture's brightest → not found; a clipped band
→ the clipped reason; the click still wins. The monitor: Meter hidden by
default, shown with the setting; the estimate runs with no click.

## Revision 3 (2026-11-01): Expose each frame for itself
The user: "Can there be a check box to enable auto exposure changes."
**Expose each frame for itself** (`frame_auto`, under the This frame line,
`live/frame_auto`, default off): every Shoot over USB uses the frame's own
suggestion, what Use for This Frame does once, without the click; Use is
hidden while it is ticked (and ticking it drops an unused arm).
`_shoot_exposures` refreshes the line first (the suggestion as it is at the
press), then, in order: an armed Use (still one-off); the suggestion when it
changes something (≥ ⅙ stop) — caption "Shooting at this frame's own
exposures: ≈ +1⅔ stops (live view)."; otherwise the film base's exposures,
and when there was no estimate at all the caption says why ("No estimate for
this frame (…): shot at the film base's exposures."). A metered reading is
the suggestion while it stands. With no film base for the scan type, Shoot
behaves as before. The automatic frame guide (spec/auto-frame-guide.md)
pauses the estimate while no whole frame is in view, which lands in the
no-estimate case.

Tests: ticked → two Shoots in a row both at the frame's exposures with the
caption; no base beside the frame → the film base's exposures and the reason;
Use hidden while ticked.

## Revision 2026-11-01 (4): no warning on the line
The user, with a 35 mm roll in the carrier: "The warning that comes up …
('The film base...') is wrong. Don't think there needs this warning anyway?"
(It read "The film base shows on only one side of the frame guide" with the
rebate above and below the frame.) The "This frame" line now shows only an
estimate (or a metered result); when there is none it is empty and hidden.
Why there is none stays in the Use for This Frame button's tooltip, and the
Shoot note of Expose each frame for itself still says it at shot time
(`_frame_reason`). A Film Base click INSIDE the frame guide is ignored by the
live estimate (an older click: another roll's, or a gap since wound on into a
frame): it looks beside the guide instead of answering "the click is inside
the frame guide".
