# Auto white point (per frame)

> Superseded in part by `spec/white-point-choice.md`: the brightest *near-grey*
> area, a choice per frame, and the White Point sampled per frame.

## 1. Problem

Conversions often come out tinted (a magenta/violet cast is the common
report). Sampling the White Point on an area of the frame that is close to
clipping, then converting again, fixes it, and conversions done that way are
"consistently better". That is a manual step per frame which can be done for
the user.

What the manual step does: with a white point the two-point density
conversion maps each channel's density D (above the base) to `D / Dmax[c]`,
where `Dmax[c]` is the sampled area's density. The three `Dmax` values carry
the colour: making them the white makes that area neutral. Without a white
point the default slope uses one scalar for all three channels, so any
difference between the dye layers' contrast (film stock, light source,
an imperfect base) shows up as a cast that grows towards the highlights.

## 2. Goals / non-goals

- With no sampled White Point and no film stock selected, Convert (Current
  and All) gives every frame its own white point, found automatically, the
  way the user samples it by hand.
- It must not undo a real scene colour: a sunset's warm sky or a frame whose
  brightest area is blue sky must keep its colour.
- A sampled White Point and a film stock keep their current meaning (they
  are the user saying what white is), so auto only replaces the default slope.
- Switchable: Settings → General → Conversion → "Auto white point (per
  frame)", on by default (`convert/auto_white_point`).

Non-goals: shadows/highlight tone (that is `spec/auto-tone.md`); changing
the black point; a learned model.

## 3. Measuring a frame (`core/auto_white_point.py`)

On the unconverted 1080 preview, inside the picture area (inset 3 %):

1. `D[c] = log10(base[c] / value[c])` with the frame's resolved black point.
2. Brightest area: pixels whose mean `D` over the channels lies between the
   99.0th and 99.8th percentile (the top 0.2 % is glints, dust, a sliver of
   holder). Its per-channel median is the frame's `Dmax`. Under 50 pixels, or
   a `Dmax` under 0.15 in any channel, is no estimate.

The picture area is, in order: the frame's crop; the auto crop result already
found this session; a fresh auto crop detection (also kept, as Auto Crop
would); the middle 80 % of the scan. A centre-only estimate was tested and
picks up the holder or film edge on some 35 mm frames, so the crop matters.

## 4. Keeping the scene's colour

The colour of `Dmax` is its ratio `r = Dmax / mean(Dmax)`. It is compared
with a reference ratio and the difference split into temperature
(warm/cool, along R−B) and tint (green/magenta):

- **Reference.** The median ratio of the other frames on the roll (at least
  3 with an estimate). A film/light cast is common to every frame and so is
  in the reference; a sunset is not. Without enough frames, equal channels
  (the default slope's colour).
- **Tint** is removed up to 0.15. Green/magenta highlights are almost never
  the scene; this is the reported magenta.
- **Temperature** against the roll is removed only up to 0.06: the rest is
  the scene. Against equal channels (no roll), a warm deviation is removed
  up to 0.06 and a cool one up to 0.20, because colour negative under white
  light reads cool against equal channels, and a warm highlight is more
  often a real sunset.

`Dmax_used = mean(Dmax) · (reference + limited tint + limited temperature)`,
and the white point is `base / 10^Dmax_used`: the frame's own level, so the
brightest area lands at white and Auto Gain places the image as before.

### Evidence (30 frames, 6 rolls: trichrome balanced/unbalanced, white-light
120 and 35 mm)

| Strategy | Result |
|---|---|
| Default slope (now) | White-light rolls cyan; a mis-anchored trichrome frame violet. |
| Frame's brightest area, unguarded | Fixes those, but turns two sunsets blue and greys a blue sky. |
| Roll ratio only | Keeps sunsets, but leaves per-frame casts (a beach frame magenta). |
| Guarded (this spec) | Fixes the casts, keeps both sunsets and the skies. |

## 5. Integration

- `CCRBackend.auto_white_point` (default True), restored by MainWindow from
  `convert/auto_white_point`; `uses_auto_white_point()` is True when it is on
  and there is no sampled White Point and no film stock selected.
- **Convert All** (`_apply_auto_bwpoint_to_all`): after the black points are
  resolved, a second pass measures every frame's `Dmax` (the raws are still
  unconverted); then each frame converts with its guarded white point, the
  reference being the median of the *other* frames. Progress runs in three
  equal parts when auto white point is in use.
- **Convert Current**: measures this frame; the reference comes from the
  other frames' recorded estimates (`conversion_inputs["auto_wp"]["dmax"]`)
  and, for unconverted frames with a known picture area, a quick measurement
  (their border + the known crop), cached per raw.
- Recorded as `conversion_inputs["auto_wp"] = {"dmax", "used", "ref",
  "temp", "tint"}` (JSON-safe lists/floats). The white point itself goes in
  `["bw"]` like a sampled one, so no replay site changes.
- A frame with no black point (unanchored) or no estimate converts as before
  (default slope).
- The anchor line reads "auto black + white point per frame" when it applies.

## 6. Tests

- Synthetic frame with a neutral bright patch under unequal channel
  contrast: `highlight_density` finds the patch's density; the converted patch
  is neutral.
- Holder excluded: an opaque border outside the picture rect does not move
  the estimate.
- Guard: a warm deviation against the roll is mostly kept; a tint deviation
  is removed; caps hold; no reference → the warm/cool asymmetry.
- `roll_reference` needs 3 estimates.
- Backend Convert All: white points recorded per frame, the reference is
  leave-one-out, sampled White Point / film stock / the setting off each turn
  it off; an unanchored frame gets none.
- Panel: the anchor label; Settings toggle round trip.
