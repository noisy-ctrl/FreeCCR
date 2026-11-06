# Film Look: auto balance per frame (experimental), and colour-only looks

Status: spec 2026-11-04 (the user's request), refined once before building.

The user, after the white-point fix: "would it be possible to make a 2nd version
of the luts that don't shift the image radically? … Can we try item 3 only
[auto balance per frame]? Can this be turned off and on in settings and
labelled experimental? Can we make a separate version of the luts that don't
affect the luma tone curve? Are colour only?"

## Why a look shifts colour per frame

A Film Look is a decode out of density (spec/film-look-lut.md): it replaces
FreeCCR's own rendering, and with it FreeCCR's idea of neutral. Equal density
above the base is grey in FreeCCR's own render; in the Portra look it comes out
yellow-green on purpose (mid grey in: L* 48, a* −7, b* +27), because a real
Portra grey does not have equal densities. A frame lands neutral only when its
densities sit in the balance the lab's scanner put them in, and the lab
balances every frame by itself. With the sliders at 0, the nine lab frames are
neutral on average but each is off: mean abs Δa* 3.4, Δb* 4.1, ΔL* 7.2, median
ΔE 7.9 (with each frame's best Channel Levels, 0.7).

## 1. Auto balance per frame (experimental)

### Measured: what the lab did (leave-one-out on the nine frames)

For each frame, the Channel Levels offsets that best match the lab (offsets
only, no per-channel gain) were split into a COLOUR part (each offset minus
their mean; no change to overall density) and an EXPOSURE part (the mean).

- Colour: the lab's correction follows the frame's mean density colour (each
  channel's mean density over the picture, minus the three's mean) almost
  exactly, at about half strength: correlation 0.95, slope α 0.52–0.56 (labs
  correct only part of a frame's cast so a scene that really is one colour, a
  sunset or a lawn, keeps some of it). Model:
  `colour offsets = α · (T − colour(mean density))`, T the lab's balance point
  (a constant per look).
- Exposure: correlates only −0.67 with the frame's mean density; predicting it
  took |ΔL*| from 7.18 to 6.76, nearly nothing. The operator set it by taste.
  Not automated: exposure stays Master Shift.
- T cannot be read off the look itself: the density the look renders neutral at
  the frame's level (−0.065, −0.053, +0.118) is much further from equal than
  the lab's balance point (−0.047, −0.026, +0.073) — real scenes do not
  average to grey — and using it did worse than nothing. So T and α are
  measured by the fitter from its own pairs and stored in the cube.

Leave-one-out (T and α from the other eight frames each time), mean over the
nine frames:

| | median ΔE | mean abs Δa* | mean abs Δb* |
| --- | --- | --- | --- |
| sliders at 0 | 7.85 | 3.36 | 4.11 |
| auto colour (exposure left at 0) | 6.37 | 1.95 | 2.38 |
| exposure set right, no colour | 4.93 | 3.23 | 5.47 |
| exposure set right + auto colour | **1.79** | **1.58** | **1.72** |
| exposure set right + the lab's own colour | 0.95 | 0.07 | 0.13 |

(As built: `look_balance.fit_balance`, a joint least-squares fit of α·T and α,
on `look_fit.picture_mean_density`; the worst frame with the brightness set
is 6.2.) So once the brightness is set (Master Shift, by eye), auto colour
takes the frame from ΔE 4.9 to 1.8 of the lab: about two thirds of the cast
removed. From all nine frames: T = (−0.046, −0.026, +0.072), α 0.55. Nine
frames from one roll and one lab: hence **experimental**.

Mean density beat LATD (density of the mean transmittance), the median, a
20 % trimmed mean and low-chroma pixel subsets.

Through the app's own Convert (auto white point, auto crop after converting)
on four of the frames (in the fit; whole-picture means, so only a rough
check), the full look's mean b* against the lab scan's: DSCF0050 −8.9 → +0.2
(lab +0.5), DSCF0053 −3.4 → +1.1 (lab +1.2); a* on DSCF0065 +3.3 → −0.3 (lab
−1.1) and DSCF0056 +2.6 → −0.3 (lab −1.6), where b* moved 15.2 → 11.2 and
13.4 → 9.4 against the lab's 14.4 and 13.0. No pixel of the colour-only
renders reached the clip.

### Behaviour

- **Settings → General → Film Look → "Balance each frame's colour for the Film
  Look (experimental)"**, QSettings `look/auto_balance`, **default off**,
  `ccr_backend.look_auto_balance`. Tooltip says what it does, that exposure
  stays Master Shift, and that it was measured on one lab's scans.
- Acts on a frame only when: it has a Film Look whose cube carries balance data
  (below), it is converted in black-point mode (`mode: "bw"` with a black
  point: the look's own base), and the setting is on. Otherwise nothing.
- Hidden, like Auto Gain: the offsets are added to the R/G/B Shift values the
  render receives; the sliders still read the user's own values, which
  fine-tune on top. Turning the setting off gives the plain look back.
- Colour only: the three offsets sum to 0, so overall density (brightness)
  is untouched.
- Measured on the frame's PREVIEW base (`resized_raw`), so the preview, zoom,
  slices and export get the same offsets: never from the image being
  rendered (a zoom tile has a different mean). Picture area: the crop
  (`crop_rect`), else `conversion_inputs["picture"]`, else the middle 80 %.
  Before the user's Channel Levels (the user's shifts must not be
  cancelled), after the look's input map (revision 6), every 4th pixel each
  way (centred, so a gradient is not biased), pixels inside the look's domain
  only (−0.25 ≤ d ≤ 1.25 in every channel; the holder and sprocket holes fall
  outside).

### Data

The fitter writes one comment line into the cube:

    # FREECCR_BALANCE <T_r> <T_g> <T_b> <alpha>

(`look_lut.BALANCE_MARK`; `LookLUT.balance` = (T (3,), α) or None). T and α
from all the fitter's frames by `look_balance.fit_balance` (joint least
squares; α clamped to 0.2–1.0 and T refitted for it). `tools/fit_look.py`
writes it and logs T, α and the held-out check (`look_fit.balance_check`).
The user's existing "Portra 400 Lab.cube" is re-issued with the line added
(same table, number for number) rather than refitted. A cube without the
line: the setting does nothing for it.

## 2. Colour-only looks

### Behaviour

- Every look in both Film Look lists also appears as **"<name> (colour
  only)"**, right after it. Stored as the same `look_lut` value with the suffix
  `|colour` (`look_lut.COLOUR_SUFFIX`), so every path that copies the value
  (attach, sync, paste, copy, catalogue) carries it unchanged.
- It keeps the look's colour (its chromaticity) and takes the luminance from
  what FreeCCR would show without the look: the same frame's value with no
  look (the input map undone, so a white-point conversion keeps its own tone),
  or Cineon Log → Workspace of it when that box is ticked.
- So FreeCCR's tone is in charge: **Auto Gain, the baked exposure, the default
  brightness −8, auto tone, Cineon and the conversion clipping guard all work
  as without a look**; only the full look suppresses them. The Cineon box
  stays enabled.
- Auto balance (part 1) applies to colour-only looks too.

### Processing

In the look's slot (after Channel Levels, where Cineon would be):

    out = look(d)                              # the look's display value
    ref = unmap(d)                             # FreeCCR's own value
    ref = cineon(ref)   if Cineon is ticked
    lin_o, lin_r = decode(out), decode(ref)    # sRGB, extended past 0 and 1
    Y_o, Y_r = Rec.709 luminance of each
    lin = lin_o · Y_r / Y_o                    # the look's chromaticity, ref's luminance
    out = encode(lin)

- `decode`/`encode` extend linearly below 0 and as the power curve above 1, so
  the shadow margin and highlight headroom survive (un-clamped like the stage
  it replaces).
- Where Y_o ≤ 1e-6 or Y_r ≤ 0 the pixel is ref's grey at ref's luminance (the
  look has no colour to give there; keeps the margin, continuous at 0).
- Nothing is clipped here; a colour pushed past 1 is headroom, as everywhere
  else in the windowed stage.
- float32 throughout; at a grey look output the result is ref's grey exactly.

## Integration

- `core/look_fit.py`: `picture_mean_density`, `fit_offsets`, `balance_check`;
  `tools/fit_look.py` writes the line and the report's `balance`.
- `core/look_lut.py`: `BALANCE_MARK`, read/write the line; `COLOUR_SUFFIX`,
  `split_look_name`, `colour_only_name`, `is_colour_only`, `is_full_look`;
  `resolve_look` takes the value and ignores the suffix; `for_frame(lut, ci,
  colour_only)` returns a `FrameLook` for a colour-only look even with no
  input map; `FrameLook` gains `colour_only` and `unmap_input`;
  `keep_luminance(out, ref)`.
- `core/look_balance.py` (new, Qt-free): `picture_rect(img)`,
  `picture_mean_density(base, rect, look, windowed)`, `balance_offsets(mean,
  balance)` (sum-zero, density units), `fit_balance(means, offsets)`.
- `core/ccr_processor.py`: the three look sites call one helper,
  `_look_stage(d, lut, cineon_log)`, which applies the look and, for a
  colour-only look, keeps ref's luminance; Cineon steps aside for a full look
  only.
- `core/ccr_image.py`: `look_balance_shift(img, look)` → (r, g, b) slider
  units or None; `apply_adjustments` adds it to `ch_r/g/b_shift`, and only a
  FULL look (`full_look`) suppresses Auto Gain / eb / auto tone / brightness
  −8 / clip gain (`_auto_gain_for_base` too).
- `core/ccr_backend.py`: `look_auto_balance`; `_full_look(img)` for the
  clipping guard.
- `ui/main_window.py`: loads `look/auto_balance`;
  `on_look_auto_balance_toggled` stores it and re-renders.
- `widgets/settings_dialog.py`: the "Film Look" group and its checkbox, staged
  and applied on Done like the others.
- `widgets/sliders_panel.py`: list entries; Cineon enabled with a colour-only
  look; the set-look hint per kind; display name "(colour only)", which the
  paste row picks up.

## Tests

`tests/test_film_look_balance.py`, plus one in `tests/test_look_fit.py`:

- Cube: the balance line round-trips, a stale one is replaced, a malformed one
  is ignored, a cube without it has `balance` None; α is kept in range.
- `balance_offsets` sum to 0 and are α·(T − colour(mean)); `fit_balance`
  recovers a known T and α; the fitter's functions recover them from
  synthetic frames and the held-out check beats no correction.
- `picture_mean_density`: the crop is used, pixels outside the domain are left
  out, the input map applies, every-4th sampling matches the full mean within
  1e-3 on a gradient; `picture_rect` order.
- `apply_adjustments`: on → the offsets added to the user's shifts (sliders
  unchanged), measured on `resized_raw` whatever image is passed, summing to 0
  with Master Shift untouched; off, no balance data, a reference or no-anchor
  conversion, no look → nothing added.
- Colour-only: luminance equals ref's and chromaticity the look's; a grey look
  output gives ref exactly; headroom and the shadow margin survive; in the
  pipeline with Channel Levels and with Cineon ticked; the map round-trips
  through `unmap_input`; a full look renders byte for byte as before;
  CPU == OpenCL; Auto Gain and the default brightness are kept for a
  colour-only look and dropped for a full one; auto balance applies to it.
- Panel: both lists show "(colour only)" entries with the suffixed data; the
  Cineon box stays enabled for one; the paste row names it. Settings: the
  checkbox is staged and applied.

## Refinement (before building)

- *Should auto balance also set exposure?* No: measured, it barely helps
  (|ΔL*| 7.18 → 6.76) and a wrong guess is a big visible jump. Master Shift.
- *Per-channel gains?* No: the lab's per-frame contrast is small and
  unpredictable from the frame (ridge-damped in the fit already).
- *Where is T stored?* In the cube, because it belongs to the lab rendering
  the look was fitted from; another look has another T.
- *Hidden or written into the sliders?* Hidden, so toggling the setting is
  reversible and a re-crop re-measures; written-in values would also be
  copied to other frames by Sync, which is wrong for a per-frame balance.
- *Colour-only as a checkbox or list entries?* List entries ("a separate
  version of the luts"), encoded in the value so no copy/sync/paste path
  changes.
- *Gamut*: a colour-only pixel may exceed 1 in one channel where the look was
  darker than FreeCCR's tone; that is headroom like any other in the
  windowed stage and clamps at the end as before (none reached the clip on
  the four real frames checked).
