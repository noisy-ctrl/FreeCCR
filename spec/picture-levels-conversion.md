# Levels from the picture: a new default conversion

The user, 2026-11-28: "Could we change the conversion behaviour alongside? Start with the contrast/exposure/density curve. I'm not fixed to how CCR currently samples for the above. Whatever is best but maybe we could keep a legacy option in settings for how things currently are if we do change." Then: "Please go ahead with these and wb changes."

## 1. Why

The user compared five of his trichrome frames converted by FreeCCR (defaults) with the reference converter (defaults). The reference converter's frames came out cleaner and fuller. FreeCCR's sat low and grey:

| | Reference converter: black / middle / white | FreeCCR today |
| --- | --- | --- |
| Street frame (R, G, B out of 255) | 3 5 3 / 157 171 182 / 251 252 253 | 28 20 11 / 125 119 113 / 217 218 219 |

The reference converter's default was measured on the five frames. Its picture densities were read off the merged scans (quantiles per channel, inside the picture), and its output was read off its exports (quantiles per channel, same area). It is reproduced to 3.1–7.8 levels RMS per frame by two steps:

1. **Levels per channel, on the negative's light (transmission), set from the picture.** Black comes from the thinnest 0.1 % of the picture and white from the densest 0.2 %:
   `u = (T_black − T) / (T_black − T_white)`
   Each channel gets its own two points, so a cast common to the whole frame goes, as auto levels removes it.
2. **One fixed tone curve F**, shared by every frame and channel, from u to display:
   F(0, 0.1, …, 1) = 1.8, 3.8, 11.1, 22.6, 36.4, 53.4, 79.9, 110.7, 146.1, 192.2, 253.5 (out of 255).

Levels in density, the way FreeCCR's two-point conversion works, fit worse: 2.3–11.9 levels RMS. Adding a per-frame gamma barely helped either model (3.3–7.4 levels), so none is used.

FreeCCR's conversion today works differently. Black is the film base, measured on the rebate. White is the auto white point, guarded by the roll. Then come Auto Gain, Auto tone's toe and shoulder, the −8 brightness baseline, and the clipping guard. A frame's contrast therefore depends on how dense its film is, not on its picture.

## 2. Goals

- Make the new conversion the default: levels from the picture, then the measured curve. A frame converted with default settings should look like the reference converter's default.
- Keep today's conversion as a Settings choice, "Film base and white point", byte for byte.
- Frames already converted keep their conversion. Re-converting uses the current choice.
- Every slider keeps working on the new frames. Channel Levels and Printer Lights still grade in density, ahead of the curve.
- Everything replays unchanged: zoom, export, slices, the catalogue and the tether worker.

## 3. Non-goals

- **The roll guard.** The reference converter balances every frame on its own, and so does this. Whether to guard the frame's colour against the roll (as the auto white point does) is a separate decision. It is on the user's list for 2026-11-29.
- **Temperature and Tint.** Making them "corrective rather than additive" has its own spec: `spec/anchored-white-balance.md`.
- **A per-frame method choice.** Not in this version. To convert one frame the old way, switch the setting and convert that frame.
- **Reference-frame conversions, Positive mode and the no-anchor invert.** Unchanged.

## 4. What the user sees

- **Settings → General → Conversion → "Levels"**, a list at the top of the group:
  - "From each picture" (default)
  - "Film base and white point"

  The note under it: "From each picture: Convert sets each channel's black from the clearest part of the picture and its white from the densest, then applies one fixed tone curve — every frame fills the range and loses its overall cast. Film base and white point: black is the film's clear base and white the frame's brightest near-grey area, checked against the roll, as before. New conversions only."

  The choice is stored as `convert/levels` = `"picture"` or `"base"`, and kept on `ccr_backend.conversion_levels`.

- **The Film B/W Point section, with "From each picture" chosen.** A muted line at its top reads: "Levels come from each picture (Settings → Conversion). These are used with Film base and white point."
  - Set White Point, Film Stock (and its save button) and the White Point list + Show are greyed.
  - Set Black Point stays live. The roll's black point still matters for the old method, false colour's stop zones, the tether nudge's own steps and Capture's film base.

- **Convert (the frame on screen).** The hint reads "Converted with levels from the picture (outlined: where they were measured)." The measured area flashes, using the same flash as the white point areas.

- **Convert All.**
  - The question reads: "Convert all N images? Each frame's levels are set from its own picture. Any existing conversions will be replaced."
  - The summary reads: "Levels from the picture on N frames; M cropped to the frame; K not converted."

- **Hot Folder Tether.** Captures convert as they arrive with no black point set; the tether worker converts whenever the method is "From each picture". The lead frame's "set a black point" nudge and its hint aren't shown, and the banner's "No black point set" note is empty, because nothing needs one.

## 5. Measuring (core/picture_levels.py)

`measure(raw, rect=None) -> Levels | None`, on the unconverted preview scan (`resized_raw`, the array order used everywhere: black / white tuples are in it).

**Area.** It's measured inside the picture rect:
- the crop, else the auto crop found this session, else a fresh detection, as `plan_frame_conversion` finds it today;
- inset by `INSET` = 3 % of its size on each side, so the gate's blur and edge don't count.

With no picture rect, it's measured in the middle of the scan, leaving `BUFFER` = 7 % on each side (the reference converter's default).

**Pixels.**
- Every 2nd pixel each way is sampled, which is about 75 k on the 1080 px preview.
- A pixel is left out if any channel is clipped (≥ `CLIP` = 65280).

**Black.** Black per channel is the `BLACK_PCT` = 99.9th percentile of the channel's values: the thinnest 0.1 %.

**White.**
- White per channel is the `WHITE_PCT` = 0.2th percentile, taken over the pixels no denser than `HOLDER_DENSITY` = 2.7 above that channel's black. This keeps a sliver of opaque holder from becoming white: the user's holder reads 3.3 above the base, while picture content reaches at most about 1.8.
- The density range `D_c = log10(black_c / white_c)` is floored at `MIN_RANGE` = 0.3. A flat channel gets a white 0.3 denser than its black rather than an extreme stretch.

**When it gives up.** It returns None when fewer than 1000 pixels are left, or when a channel's black is under 64 (no light). The plan then falls back to the old method, and the hint says so.

**Result.** `Levels(black, white, record)`, where `record` is:

```
{"from": "picture", "rect": [x1, y1, x2, y2] or None,
 "black_pct": 99.9, "white_pct": 0.2, "range": [D_0, D_1, D_2]}
```

## 6. Converting

The conversion is the existing two-point density conversion: `ccr_normalize_with_bwpoint(img, black, white, density=True)`, whatever the Density toggle says. The windowed base is the density above the picture's black, divided by the channel's range: 0 at black, 1 at white, with the margin and headroom kept.

`conversion_inputs` is the same `"bw"` recipe, plus one key:

```
{"mode": "bw", "bw": (black, white), "density": True, "slopes": None,
 "fine_rot": …, "picture": rect, "levels": record}
```

Every replay site already hands `bw` / `density` back to the converter, so zoom, export, slices and the catalogue need nothing new. `_ci_to_json` copies unknown keys as they are, and `levels` is plain JSON.

`plan_frame_conversion(img)` with `conversion_levels == "picture"`:
- reloads the frame as now;
- finds the crop as now;
- finds the picture rect: the crop's, else `frame_picture_rect`, which detects one;
- measures.

It returns `{"black", "white", "slopes": None, "rect", "crop", "density": True, "levels": record, "auto_res": None, "auto_source": None, "awp_rec": None}`. No auto black point and no white point are resolved: they would be thrown away.

`apply_frame_conversion` passes `plan.get("density", self.density_bwpoint)` to the converter and writes `levels` when the plan has it.

`_finish_tone`, with `levels` recorded, skips Auto tone's measurement (it's dropped) and skips the clipping guard. AWB already skips any frame with a white point.

**Convert All** with "picture" reloads the converted frames, then finds the crops as now (`_convert_all_crops`, with the roll check). It then converts each frame with its plan, so a frame's crop rect is the one it gets. The summary gains `"picture": n`.

**Convert Selected** and the tether worker already go through plan + apply.

`reprocess_all_for_density_bwpoint_change` (the Density toggle) leaves frames with `levels` alone: they are always density.

## 7. Rendering: the print curve

The curve is a decode in the Cineon / Film Look slot. It runs right after Channel Levels on the de-windowed value, so Levels and Printer Lights still shift density.

`picture_levels.print_curve(conversion_inputs)` returns a `PrintCurve` when `levels.from == "picture"`, mode is `"bw"`, `density` is set and both points are known. It returns None otherwise. Old frames therefore render exactly as before.

Per channel c, on the density fraction x (after Channel Levels):

```
u = (1 − 10^(−D_c·x)) / (1 − 10^(−D_c))          back to transmission levels
out = F(u)                                         the measured curve
```

- **F** is a Fritsch–Carlson monotone cubic through the 11 knots, which are uniform in u. Outside [0, 1] it carries on as a straight line at its end slopes: 0.079 at black and 2.40 at white, in display units per unit of u.
- **Below black** (x < 0, clearer than the picture's black), u goes negative. The output goes into the shadow margin and the window clamp floors it, as before.
- **Above white**, u approaches `1/(1 − 10^(−D_c))` and the output reaches at most about 1.06. That is the headroom the White Point slider can bring back.

**Order of the decodes.** The slot takes one decode at a time: a Film Look, else Cineon (if ticked), else the print curve.
- A Film Look on a picture-levels frame already works. `look_input_map` turns a density two-point conversion into the look's density (`0.8·D_c`).
- With Cineon ticked, the frame renders as any density two-point frame does with Cineon.

**What the print curve switches off.** These are display-referred additions to the old default render, and the curve already places black and white:
- Auto Gain;
- the baked `eb`;
- the −8 brightness baseline;
- Auto tone;
- the clip gain.

This is the same list a full Film Look switches off, in `apply_adjustments` and `_auto_gain_for_base`. Everything after the decode (Master Gain, White Balance, White Point, the tone and colour chain, Lab Finish, curves) is unchanged.

**Plumbing.** `PrintCurve` goes to `adjust_image` / `adjust_image_opencl` as the `look_lut` argument, so the windowed recovery, the non-windowed pass and the OpenCL numpy pre-stage carry it with no new parameter.
- It has no `map_input`, so `_look_input` returns its input.
- `_apply_look` calls `lut.decode(d)` when the object has one.
- `colour_only` is False.

Only `apply_adjustments` builds it. Area layers grade the decoded base, as with a look.

**Speed.** `exp2` per channel plus a uniform-knot Hermite evaluation, in 1 M-pixel chunks. About 30 ms on the 1080 px preview.

## 8. The curve's data

`core/print_curve_data.py` holds `KNOTS` (11 values, 0..1). It is WRITTEN by `tools/fit_print_curve.py` from `tools/print_curve_quantiles.json` (the five frames' density quantiles and the reference converter's output quantiles, per channel, inside the picture). Never edit it by hand.

The fit:
- uses quantiles P 0.1–99.8, dropping outputs at 0–1 and 254–255, which are clipped and censored;
- is least squares with piecewise-linear interpolation weights and a small second-difference penalty;
- is made monotone.

The tool prints each frame's RMS: 4.8, 5.6, 7.8, 7.7, 3.1 levels.

## 9. Quality (the standing requirements)

- **Smooth and monotone.** The decode is monotone in x for every D_c > 0. F is C¹ and strictly increasing, with a minimum slope of 0.06. So a 16-bit-fine ramp stays monotone and smooth.
- **Float32.** It runs on float32 throughout; the window's uint16 is the only quantisation.
- **Identical everywhere.** It's the same on CPU and GPU: one numpy function in the shared pre-stage.
- **Resolution independent.** The levels are measured once, at conversion, on the preview, and stored. Zoom, tiles and exports replay the same numbers.

## 10. Tests (tests/test_picture_levels.py)

**Measuring**
- A synthetic negative's black / white land on its known 99.9 / 0.2 percentiles.
- The rect and inset are honoured.
- A holder strip denser than 2.7 is ignored.
- A clipped corner is ignored.
- A flat channel gets the 0.3 floor.
- No light gives None.

**Converting**
- The plan with "picture" measures and records `levels`, and its `density` is True with the toggle off.
- The plan with "base" is the old plan, byte for byte: the same dict as before, compared on a frame.
- `conversion_inputs` round-trips through the catalogue.

**Rendering**
- A frame converted with picture levels renders black at F(0) and white at F(1) on its measured patches, within 1 level.
- A patch at density fraction x renders F(u(x)).
- Auto Gain, `eb`, −8 and Auto tone don't apply. The clip guard returns None.
- The same frame converted with "base" renders exactly as before.
- Channel Levels' Master Shift moves the picture in density, before the curve.
- A Film Look replaces the curve, and so does Cineon.
- A 16-bit-fine ramp through the decode is strictly increasing and in range.
- CPU and OpenCL renders are equal.
- Export equals the preview at the same size.

**The curve**
- F at the knots is the data.
- F is monotone with a minimum slope over 0.05.
- The ends extend linearly.

**User interface**
- Settings writes `convert/levels`, and the main window reads it at start.
- The Film B/W Point section greys its white point controls with "picture".
- Convert All's question and summary are worded as above.
- The tether nudge isn't set with "picture".

**On the user's frames.** The street frame (`DSCF0440_RGB.dng`, the one merged scan here) is converted and its output quantiles compared with the reference converter's export. Black, middle and white should land within a few levels.

## As built (2026-11-28)

- **Plan functions.** `CCRBackend.plan_picture_levels(img, crop=_FIND)` and `plan_frame_conversion_base(img)`, which is the old plan body unchanged. `plan_frame_conversion` tries the first when the setting is "picture" and falls back to the second.
- **Convert All.** `_convert_all_picture_levels` reloads every frame, runs `_convert_all_crops`, then plans and applies each frame. A frame the picture can't measure is converted from its film base and named in the summary.
- **Default for tests.** `default_conversion_levels()` reads `FREECCR_DEFAULT_LEVELS`. `tests/conftest.py` sets it to "base", so the existing tests keep testing the film base method; `tests/test_picture_levels.py` sets "picture".
- **Measured on the street frame**, the merged DNG here, converted by FreeCCR with sliders at 0. Black / middle / white per channel (R G B):

  | | Black | Middle | White | RMS against the reference, P1–99 |
  | --- | --- | --- | --- | --- |
  | Picture levels | 4 4 3 | 115 102 115 | 254 254 254 | 6.0 levels |
  | Film base method | 29 20 12 | 97 77 70 | 219 219 220 | 33.6 levels |
  | Reference converter | 2 3 2 | 116 113 121 | 253 253 253 | |

  The measured ranges were 1.246 / 1.346 / 1.590. The reference analysis of the same frame gave 1.255 / 1.351 / 1.606.

## Refinement (2026-11-28)

- **Density, not transmission, in the base.** The legacy linear two-point conversion is already transmission levels: `d = (T_black − T)/(T_black − T_white)`. Using it would have made F the whole decode. But Channel Levels' shift would then be an additive lift in transmission, and Printer Lights and Film Looks expect density. So the base is density and the decode undoes it per channel. That is exact: `u(x)` is the transmission levels of the same two points.
- **The Density toggle is ignored, not obeyed.** A picture-levels frame with the linear base would render the same at sliders 0 but grade differently. One model is simpler. Frames on the old method still follow the toggle.
- **Sampled points are ignored, not mixed in.** A sampled White Point on a picture-levels frame was considered (sample sets white, picture sets black). It was left out: two sources for one frame's levels with no way to see which won. The section's note says what applies.
- **The −8 baseline goes.** It is part of the old default look. The measured curve already includes everything between the levels and the display.
- **Measured on `resized_raw` before conversion, never on the render.** So the numbers are the same however the frame is later graded, zoomed or exported.
- **Holder guard.** 2.7 density sits between the densest picture content measured on his roll (1.74) and his holder (3.27). The clipped-pixel guard takes out open light round the film.
