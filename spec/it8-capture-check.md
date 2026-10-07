# IT8 wizard: capture check on Step 3

## Goal

When locating the patches, tell the user, per photo, whether the chart capture
is clipped or underexposed, and how hard the profile's white balance will
push the weakest channel. That lets them choose between retaking and
**Prevent channel clipping** (spec/profile-no-clip.md) knowing why.

## UX

Two lines under the Step 3 controls row, updated live with "Valid patches"
(same samples, so it follows the corners):

- **Exposure**: per channel, labelled by photo for a trichrome target
  ("Red photo (DSCF1517.RAF)") or by channel for a single shot. Brightest
  valid patch as % of full scale, a coloured dot and a verdict:
  clipped (red, with patch count), close to clipping (amber, >= 92 %),
  good (green, 40-92 %), a little low (amber, 20-40 %), underexposed
  (red, < 20 %). Low/near verdicts give the stops to the 75 % target.
- **Light balance**: the profile's largest WB boost. <= 1.25x: "even".
  Otherwise the boosted channel, the factor, and the raw level above which
  scans clip (1/boost). The advice leads with the better fix (more light or
  exposure on that channel) and offers Prevent channel clipping
  (experimental) second; when that is ticked it turns green but still names
  the better fix. The two lines are separate paragraphs with a 6 px gap, so a
  wrapped Exposure line doesn't run into Light balance.
- Tooltip: what each verdict means and what to do; for trichrome, that
  changing ONE photo's exposure changes the recorded balance (scan negatives
  with the same change), while changing all three equally keeps it.

## Data / math

`PatchSample.clip_frac` (per-channel fraction at the sensor ceiling, already
computed by `sample_patches`; optional field, default None).
`it8_profile.assess_capture(samples, ref) -> CaptureReport | None`: pure.
Peaks from valid patches; clipped count = patches with a channel's
`clip_frac >= 0.02` (the same rule that invalidates them); gains from the
lightest valid neutral (`_pick_wb_id`, as the fit uses), green = 1;
`boost = max(1, max gain)`.

Levels are fractions of the sensor's full scale: the profiling decode is
camera-native with manual white-level scaling.

## Tests

`tests/test_it8_capture_check.py`.
