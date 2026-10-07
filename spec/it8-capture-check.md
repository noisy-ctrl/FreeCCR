# IT8 wizard: capture check on Step 3

## Goal

When locating the patches, tell the user, per photo, whether the chart capture
is clipped or underexposed, and how hard the profile's white balance will
push the weakest channel. That lets them choose between retaking and
**Prevent channel clipping** (spec/profile-no-clip.md) knowing why.

## UX

A row under the Step 3 controls: **Check exposure** and **Speeds at ISO**
[As shot | 50 ... 6400, third stops]. Nothing is calculated until Check
exposure is clicked, because the default corner positions sample the wrong
areas; the text says "Place the four corners on the grid, then click Check
exposure." After the click the report follows corner moves live; loading
another chart or card resets it. The ISO choice persists (`it8/calc_iso`,
0 = as shot): suggestions are worked out at that ISO (time scales inversely
with ISO, `iso_equivalent_time`), the header reads "speeds for ISO 160; shot
at ISO 400", and "Keep" is only offered at the shot's own ISO.

The report is plain text under that row. **No colour coding**: traffic-light
colours clashed with the channel colour names, and a green "handled" state
wrongly praised the least preferable fix.

- **Exposure (ISO n)**: one line per photo for a trichrome target, e.g.
  "Red photo (DSCF1517.RAF): 20% at 1/10 s, underexposed. Suggested: 0.4 s."
  Level = brightest valid patch as % of full scale; verdict = clipped (with
  patch count) / close to clipping (>= 92 %) / good (40-92 %) / a little low
  (20-40 %) / underexposed (< 20 %). The shutter speed and ISO come from each
  photo's EXIF (Fujifilm RAF: from the embedded preview JPEG, which exifread
  can read when the RAF container defeats it). ISO is shown per photo only
  when the three differ. Without EXIF the advice falls back to stops.
- Suggested speed: the LONGEST standard third-stop speed that keeps the
  brightest patch no more than 1/6 stop above the 75 % target (near-ties go
  to the safer, shorter speed); "Keep" when that is the current speed. A
  clipped photo's true level is unknown, so it gets one stop shorter "or
  shorter". Shutter speed is the lever because each trichrome photo has its
  own exposure, while the light's channels are often already at full power.
- Single-shot target: per-channel levels, then one "Whole shot" suggestion
  set by the brightest (or a clipped) channel.
- **Light balance**: the profile's largest WB boost and the raw level above
  which scans clip. For trichrome, the boost the suggested speeds would leave
  (equal levels even out the light, so it typically drops to ~1x). Prevent
  channel clipping (experimental) is presented as a fallback, ticked or not.
  Trichrome also gets: shoot the negatives with the same three speeds, or all
  three changed by the same amount, because the profile records that balance.
- The two paragraphs are separated by a 6 px gap.

## Data / math

`PatchSample.clip_frac` (per-channel fraction at the sensor ceiling, already
computed by `sample_patches`; optional field, default None).
`it8_profile.assess_capture(samples, ref) -> CaptureReport | None`: pure.
Peaks from valid patches; clipped count = patches with a channel's
`clip_frac >= 0.02` (the same rule that invalidates them); gains from the
lightest valid neutral (`_pick_wb_id`, as the fit uses), green = 1;
`boost = max(1, max gain)`. `suggest_shutter`, `predicted_boost`
(neutral scaled by each photo's speed ratio) and `read_shot_exposure` are
pure helpers in `it8_profile`; the dialog caches EXIF per file.

Levels are fractions of the sensor's full scale: the profiling decode is
camera-native with manual white-level scaling.

## Tests

`tests/test_it8_capture_check.py`.
