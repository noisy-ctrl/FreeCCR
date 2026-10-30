# Convert, then crop; and nothing clips at conversion

Status: built 2026-10-30. The user's requests: "Convert uses the same process
as autocrop … I'd like it to convert then crop" and "make sure 0% is clipping
on conversion, rather than 1% or whatever it's set to … code both, changeable
in settings". Revises `spec/auto-crop-buttons.md` (Convert no longer stays out
of cropping) and `spec/auto-tone.md` (the shoulder may now reach everything).

## 1. Convert, then crop

### Behaviour
After a frame converts, it is cropped to the frame auto crop finds, exactly
as **Auto Crop** would, when:

- Settings → General → Conversion → **Crop each frame after converting it**
  is on (QSettings `convert/crop_after`, default on, `ccr_backend.crop_after_convert`);
- the frame is one Auto Crop All may change (`auto_crop_candidates`' rule: no
  crop, or still the crop auto crop set; never one drawn by hand or confirmed
  with Done);
- the detection is usable (high or medium confidence).

It is one undo step taken after the conversion (`apply_auto_crop`), so Ctrl+Z
removes the crop and leaves the conversion.

Every Convert does it: Convert Current (panel and toolbar), Convert All, and a
tethered capture (which converts like Convert Current,
`spec/tether-convert-like-convert.md`).

### Measuring inside the same frame
The detection runs BEFORE the conversion, on the unconverted scan (no extra
decode), when a candidate has no detection yet this session
(`img.auto_crop_last`). Its box is then the picture area every conversion
measurement already uses (`frame_picture_rect`: white point, auto tone, and the
clipping guard below), so the frame is measured inside exactly the crop it
then gets.

Convert All collects the candidates' detections, runs the roll check
(`_roll_consistent_crops`, without re-detection: the scans are converted by
then) and applies them; its summary counts the frames cropped.

### Tethered captures
A capture inherits the previous frame's look, crop included
(`look_template`). The template now also carries whether that crop was auto
crop's (`auto_crop_rect`), so a capture that inherited an AUTO crop is still a
candidate and gets its own frame (if detection is usable; otherwise it keeps
the inherited box). An inherited hand crop is kept, as before.

## 2. Nothing clips at conversion

### Modes
Settings → General → Conversion → **Highlights and shadows at conversion**
(QSettings `convert/clip_mode`, `ccr_backend.clip_mode`):

| value | label | what it does |
|---|---|---|
| `rolloff` (default) | Roll off the ends (nothing clips) | brightness as before; the highlight shoulder reaches the picture's brightest point and the black end is raised just enough |
| `brightest` | White at the brightest point (nothing clips) | the picture's brightest point is placed at white by a gain that replaces Auto Gain; no shoulder |
| `legacy` | As before (the brightest 1–2 % may clip) | exactly the old render |

It rides Auto tone (its parameters live in `conversion_inputs["auto_tone"]`),
so with Settings → Auto tone off the combo is disabled and nothing changes.

### What "clips" means here
A pixel of the picture area clips when, in the 8-bit render the preview and
histogram use (`convertScaleAbs`), any channel is 255 (white) or 0 (black),
after a 3×3 median. The median keeps a dust speck, a hot pixel or a glint a
few pixels across from deciding the frame (it needs 5 of the 9 neighbours);
any clipped area bigger than that counts. The target is zero such pixels.

### Measured (2026-10-30, the uploaded scans, default render, before)
Inside the picture, frames converted with their own film base: white 0.03–1.5 %,
black 0–1.2 % (13 frames: GFX trichrome merges and single RAFs). Render of a
1080 px preview: 185–230 ms here.

### Rolloff
1. As before, Auto tone measures lift / toe / shoulder on the converted base
   after Auto Gain.
2. First guess: the shoulder's `top` becomes the picture's robust maximum (the
   largest value of the 3×3-median of the per-pixel brightest channel, after
   Auto Gain; no 1.3 cap, no "share above white" strength) plus `TOP_PAD`
   0.04 — a point mapped exactly to 1.0 is 255, i.e. clipped — with knee and
   sigma from the same rules (`auto_tone.shoulder(top)`).
3. Closed loop on the real render (no model of the later stages: contrast,
   saturation, the negative look and AWB all come after): render, count white
   clips; if any, bisect the reach `k` in [1, 4] (`top' = top + (top − 1 +
   0.03)·(k − 1)`, 3 renders); then the same for black with an extra lift `L`
   in [0, 0.06] on top of Auto tone's (4 renders: an over-lift greys the
   shadows). Renders are memoised; each is the full preview of the picture
   area plus a 2 px margin (whole frame when area layers or Glow need it),
   with Auto Gain measured on the whole base. An earlier half-size search
   missed small clipped areas and was dropped.
4. Recorded: `auto_tone` (the shoulder and lift used) and
   `conversion_inputs["clip_guard"] = {"mode", "white", "black", "renders"}`
   (the clipped shares left, normally 0; at a range's end the loop stops and
   the share says what is left).

### Brightest
1. `m` = the picture's robust maximum (as above) at gain 1; the frame's gain is
   `g = 0.985/m` (within Master Gain's range) — the brightest point lands just
   under white.
   Stored as `conversion_inputs["clip_gain"]`; while present it REPLACES Auto
   Gain's value (render, drafts, the WB solve's override) whether or not Auto
   Gain is on, since it is part of how this frame was converted. The shoulder
   is dropped; lift and toe stay.
2. The same closed loop: if white clips, bisect `g` down to 0.7·g; black as
   in rolloff.

### Measured after (2026-10-30, `tools/measure_conversion_clipping.py`)
Nine frames (five GFX trichrome merges, four single RAFs), each mode:

| | white clipped | black clipped | median picture level vs legacy | renders | time here* |
|---|---|---|---|---|---|
| legacy | 0.03–1.5 % | 0–1.2 % | — | 0 | — |
| rolloff | 0 % | 0 % (one frame 0.001 %) | ±1 on most; +7 / +8 on two dark frames (the black lift) | 1–9, mostly 5 | 0.7–2.2 s |
| brightest | 0 % (one 0.002 %) | 0 % | 17–51 % lower | 1–9, mostly 5 | 0.3–1.7 s |

\* this sandbox's CPU OpenCL (~200 ms a render); the user's GPU renders faster.

### Never
- on a conversion with no film base (`bw[0]` None, the no-anchor invert, which
  is graded by hand), a Film Look frame, a non-windowed base, Positive mode;
- when the picture area is unknown (no crop, no usable detection);
- on later slider changes: it fixes the conversion's default render, the user
  grades from there.

### Quality rules
The shoulder and lift are the existing smooth curves (C¹, monotone); a gain is
a gain. Nothing is decided on a downscaled map: the loop only searches one
scalar on the half-size render and confirms at full preview size.

## Integration
- `core/clip_guard.py` (Qt-free): `clip_counts(u16, rect)`, `robust_max(d, rect)`,
  `bisect_clear(f, lo, hi, steps)`.
- `core/auto_tone.py`: `shoulder(top)` factored out of `measure`.
- `CCRBackend.guard_clipping(img, rect)`; `_finish_tone(img, rect)` = auto tone
  + AWB + guard, used at every fresh-conversion site and after the density
  reprocess.
- `CCRImage.apply_adjustments` / `_auto_gain_for_base`: `clip_gain`.
- `CCRBackend.plan_frame_conversion` (detect for candidates, `plan["crop"]`),
  `crop_after_conversion(img, frame)`, Convert All's crop pass,
  `TetherWatchWorker._convert_like_current`, `look_template` /
  `apply_look_template` (`auto_crop_rect`).
- Settings dialog + `MainWindow.on_crop_after_convert_toggled` /
  `on_clip_mode_changed`, restored at start-up.

## Tests (`tests/test_convert_crop_and_clip.py`)
- clip counting: a lone white pixel doesn't count, a 2×2 patch does; rect.
- rolloff on a synthetic negative that clips today: zero clips after; the
  median of the picture's luma moves < 2 %; `clip_guard` recorded.
- brightest: zero clips; `clip_gain` stored and used by the render and by
  `_auto_gain_for_base`; Auto Gain off doesn't undo it.
- legacy: the render and `conversion_inputs` are exactly the old ones.
- no anchor / Film Look / Auto tone off: untouched.
- Convert Current crops a candidate (one undo step after the conversion, undo
  keeps the conversion); a hand crop and a confirmed crop stay; setting off →
  no crop; Convert All crops and counts; a tethered capture crops; an inherited
  auto crop is replaced, an inherited hand crop kept.
- Settings round trip for both.
- `tools/measure_conversion_clipping.py` (not a test) reports before/after on
  real scans.
