# Skin picker: balance a frame on its skin tones

The user, 2026-11-22: "something where you can draw a circle on skin tones and
it balances for those?" Answers to the three questions asked first: the line
is **the labs' by default, the video line on right-click**; on negatives the
**Printer Lights**, otherwise **Temperature/Tint**; **an amount you set**.

# Skin picker: see, and if you like correct, a frame's skin tones

The user, 2026-11-22: "something where you can draw a circle on skin tones and
it balances for those?" First answers: the labs' line by default, the video
line on right-click; on negatives the **Printer Lights**, otherwise
**Temperature/Tint**; **an amount you set**. Then the draft (revision 2,
below) showed that pulling a face onto a LINE is wrong for any face that is
not typical, and the user chose: "right click options could include options
1 and 2" = **show** the face on the vectorscope against the labs' range, or
**pull** it gently into that range; the face silhouette as the button's
picture.

## 0. Why not "put the face on the line" (measured, 2026-11-22)

A simulation on the user's lab scans (the lab's own scan as the right
answer; magenta, green, blue and warm casts added; the face balanced by
per-channel gains in linear light, as a Printer Lights move acts), 3 frames
with faces, 12 cases, mean ΔE (OKLab ×100) against the lab's scan:

| | ΔE |
|---|---|
| cast left alone | 2.0 |
| face pulled onto the 140° line | 2.9 (worse than nothing) |
| face pulled into the labs' range, 129–146° | 1.8 |

- On faces the lab printed near the line (139°, 144°) the line fixes green
  and magenta casts well (green 2.0 → 0.6, magenta 2.5 → 1.1).
- On a face the lab left pinker (126°) it doubles the error (magenta 3.1 →
  7.2: the sky went mint, the shirt yellow; warm 1.8 → 7.7).
- The skin line runs nearly along warm–cool: a warm or cool cast slides the
  skin ALONG it, so skin shows green–magenta casts only.

So a face can't tell a cast from the person's own skin; the user can, by
seeing the rest of the frame. Hence: show by default, pull only into the
range people's skin actually spans, never onto one line.

## 1. Goals

- A face button (a silhouette in profile) in the white balance row. Drag a
  circle on a face (several with Shift).
- **Show** (default): the scopes read only the circles; the vectorscope
  shows the skin range as a wedge. Nothing in the picture moves; the user
  nudges the Printer Lights (or Temperature/Tint) by eye.
- **Pull into the skin range** (right-click): a face OUTSIDE the range is
  turned to its nearest edge (only its hue: brightness and saturation
  kept), by the amount set; a face inside is left alone.
- On a negative converted on its film base (density) a pull is a Printer
  Lights move (R/G/B Shift); on a positive or a reference-frame conversion
  Temperature and Tint.

## 2. Non-goals

- No automatic face finding. No local (per-face) correction: a pull
  balances the frame. No change to saturation or exposure.
- No new stored per-frame setting: it moves sliders that already exist.
- Not the conversion: the auto white point, AWB guard, auto tone and clip
  guard re-measure each time a conversion runs and would pull the colour
  back towards their own neutral. Printer Lights is already the first stage
  after the conversion, on its own density numbers, before any clamp.

## 3. The skin lines (measured)

On FreeCCR's vectorscope (Rec.601 Cb/Cr of the display values, `core/scopes`)
angles are `atan2(Cr, Cb)`.

- **Video skin line**: the +I axis, `scopes.skin_tone_direction()` =
  **132.5°** here (the textbook 123° is the same line in the U/V plane).
- **Lab scans' skin line**: measured on the user's own lab scans, 12 skin
  patches (faces, forearms, a baby; sun, shade and indoor) on 3 frames from
  his two labs: lab A median 140.9°, lab B 139.9°, all **140.4°**, range
  123–149°. OKLab hue median 66°. So both labs put skin about 8° yellower
  than the video line. Faces vary ±10° within a lab: they balance the frame,
  they don't pin each face.

`core/skin_balance.py`: `LAB_LINE_DEG = 140.4`, `video_line_deg()`.


**The ranges.** The middle 80 % of the 12 lab patches (10th–90th
percentile) = **129–146°** ("your labs' skin range", `LAB_RANGE`). The video
choice is the same range moved onto the video line (−7.9°): **121–138°**
(`video_range()`).

## 4. UX

- **The face button** (`ImagePreview.skin_action`, "Skin"): a head and
  shoulders, filled (the user's pick of three drafts), in the canvas toolbar
  right after the **Sample circle** (the user: "next to the area
  vectorscope tool"), before Collage; `src/icons/skin-icon.png`, drawn by
  `tools/make_toolbar_icons.py` (`skin`). To fit the 900 px toolbar every
  picture button lost 2 px (`ICON_BUTTON_PAD` 5 → 3; 896 of 900 px at the
  Mac-wide font). Checkable: lit while drawing; the Sample circle and Skin
  take turns. Switching it on refuses, with a hint, an unconverted negative
  ("Convert the frame first") and a black and white frame; a Pull refuses
  while an area layer is active.
- Tooltip: "Skin: drag a circle on a face, from its centre (Shift-drag adds
  another face). Shows the skin on the vectorscope against the range of
  hues your labs print skin at. Right-click to have it pull a face that's
  outside that range back in."
- On: crosshair cursor; hint "Drag a circle on a face. Shift-drag adds
  another. Esc when done." A drag from a face's centre draws a circle (a
  dark ring under a warm-white dotted one); a plain drag starts a new set,
  Shift-drag adds to it; a circle under 4 px is ignored. Off (the button,
  Esc, another tool, another frame) removes the circles.
- **Show** (default): the scopes read only the circles (header "· skin");
  the vectorscope draws the chosen range as a faint wedge with its centre
  line, the other choice's centre line dotted. The hint names the face's
  hue: "Skin at 120°: 9° outside your labs' range, towards magenta."
  ("towards magenta" below the range, "towards green" above; "inside your
  labs' range" when it is).
- **Pull** (right-click): on release the frame is balanced so the circles'
  hue reaches the range's nearest edge (× How far). Inside the range:
  nothing moves ("Skin at 135°: already inside your labs' range."). Every
  pull starts from the values the frame had when the button was switched
  on, so pulls replace each other and the same pull twice gives the same
  answer; each is one undo step. The hint says what moved: "Skin pulled
  from 120° to 129° (Printer Lights M −1.5, G +1)".
- **Right-click the button** (`_skin_menu`, headings as disabled items):
  - When you draw a circle: **Show it on the vectorscope** / **Pull it into
    the skin range**
  - Skin range: **Your labs' (129–146°)** / **Around the video skin line
    (121–138°)**
  - How far a pull goes: 100 / 75 / 50 / 25 % (greyed while Show is on)

  Remembered: `skin/mode` ("show" | "pull"), `skin/range` ("lab" |
  "video"), `skin/amount` (default show, lab, 100).
- Refusals, as a hint, nothing moved: no colour in the circles (chroma
  under `MIN_CHROMA` 3 of 255); more than `MAX_OFF_DEG` 90° from the range
  ("That doesn't look like skin"); the sliders can't reach it (balances as
  far as they go and says so).

## 5. Processing

1. **Pixels.** Each circle (scene) is mapped to the full image like the WB
   Picker's click (`map_displayed_to_full`): centre and radius, then its
   box of `resized_raw` and a disc mask. The boxes are laid side by side
   in one patch (padded, masked) and rendered through the real pipeline by
   `CCRImage._neutral_measure_fn(patch, settings, combine)` (Auto Gain from
   the whole base, no areas, no dust), as the WB Picker does.
2. **Which pixels count.** On the render at the starting values, the masked
   pixels are kept between the 15th and 85th percentile of Rec.601 luma
   (eyes, brows, beard shadow and shine drop out). The selection is FIXED
   from then on, so every later render is measured on the same pixels and
   the solve sees a smooth function.
3. **Measure.** Mean display RGB of the kept pixels (render scale /257, so
   0–255) → Y, Cb, Cr (the vectorscope's weights); θ0 = atan2(Cr, Cb),
   ρ0 = hypot(Cb, Cr).
4. **Target (Pull only).** The nearest edge of the range, θe = clamp(θ0,
   lo, hi); inside the range nothing moves. θ = θ0 + a·wrap(θe − θ0) (a =
   How far), ρ = ρ0, Y = Y0: T = (Y0, ρ0 cos θ, ρ0 sin θ).
5. **Solve (closed loop on the real render, no model).** Knobs: Printer
   Lights = the R, G, B Shift values (half-unit steps, ±100) from the
   starting values, three targets (Y, Cb, Cr), so the face keeps its
   brightness; Temperature/Tint = two integers, two targets (Cb, Cr).
   Damped Newton with a Jacobian measured by finite differences on the
   render (step 4 units), halving the step until the error falls, at most
   6 iterations, stop under 0.3 levels; the result is rounded to the knobs'
   steps and re-measured; the best measured is kept (never worse than the
   start). `core/skin_balance.solve(measure, start, target, step, lo, hi,
   quantum)` is Qt-free.
6. **Apply.** `SlidersPanel.on_skin_sampled(values, report)`: one undo step,
   the sliders set with signals blocked, `on_slider_changed()` +
   `_settle_preview()`, the Printer Lights readout updated.

Which knobs: `conversion_inputs["mode"] == "bw"` (a film-base or no-anchor
density conversion) → Printer Lights; a reference-frame conversion or
Positive mode → Temperature/Tint.

## 6. Integration

- `core/skin_balance.py`: ranges, `range_target`, `describe`, `keep_pixels`,
  `solve`, `refusal`.
- `CCRImage.solve_skin(patch, mask, knobs, line_deg, amount)` → (values,
  report) using `_neutral_measure_fn`.
- `ImagePreview`: `skin_action`, `skin_mode`, `set_skin_mode(on)`,
  `skin_press/move/release` (Shift adds), `skin_circles` (picture shares),
  `_refresh_skin_items`, `skin_reading()` (Show, on the displayed pixels),
  `_skin_patch(img)` (Pull, on `resized_raw`), `skin_pull()`,
  `skin_menu()` (the seam), `skin_options()` / `set_skin_option()` with
  `_skin_settings` injectable; `_reference_sample_region` and
  `_capture_display_image(circles=)` make the scopes and the histogram read
  the faces; the view's `_skin_owns_mouse`.
- `SlidersPanel.on_skin_sampled(values, text)`: one undo step, rendered
  now, the Printer Lights readout updated.
- `ScopesPanel`: the chosen range as a wedge with its centre line, the
  other's centre line dotted.
- QSettings `skin/mode`, `skin/range`, `skin/amount`.

## 7. Tests

- `range_target`: inside the range → None; outside → the nearest edge, × a
  of the way, Y and ρ kept, the short way round; refusals at low chroma and
  > 90°; `describe` says "towards magenta" / "towards green" / "inside".
- `keep_pixels`: drops the darkest and brightest 15 %.
- `solve` on a synthetic smooth 3 → 3 map converges to < 0.3, rounds to
  the quantum, never returns worse than the start.
- Show: the circles feed the scopes; no slider moves; the hint names the
  hue.
- Pull, on a converted synthetic frame with a magenta "skin" patch
  (Printer Lights): the rendered patch ends within 1° of the range's edge,
  Y within 1 level, ρ within 3 %; 50 % lands half way (± 1°); a patch
  inside the range moves nothing; a second identical pull gives the same
  values; Positive mode moves Temperature/Tint and not the Printer Lights.
- Two circles (Shift) count together; a plain drag replaces the set; Esc
  clears; B&W and an active area layer disable the button.
- The vectorscope draws the wedge; the right-click choices persist and How
  far greys with Show; the WB row fits the panel.

## 8. As built (2026-11-22)

- On a converted synthetic negative with pink "skin" (112°, 103°, 67°): a
  pull lands 0.4–0.5° short of the range's edge (the shifts' half-unit
  step), brightness within 0.4 levels, chroma within 1 %, in 7–11 renders
  of the patch (≈ 0.02 s); 50 % lands within 1–2° of half way; the same pull
  twice gives the same values. On a positive, Temperature/Tint: 101° →
  128.8°.
- In the window: Show "Skin at 112°: 17° outside your labs' range, towards
  magenta."; Pull "Skin pulled from 112° to 128° (Printer Lights C +1.7,
  M −0.7, Y +1.3)."; 50 % then 100 % gives back exactly the 100 % values;
  the video range pulls to 121°.
- A pull that finds the face already inside the range after earlier pulls
  this session puts those pulls back ("earlier pull undone"); a value
  changed by hand since the last pull becomes the new starting point.

## Revision 2026-11-23 (the user's first use)

- **"I don't see it working" was a bug.** A circle was mapped to the picture
  twice: through the picture item's own transform, then through the
  display transform. On a sharp (Retina) view the item's transform also
  holds the hi-res prescale, so every circle landed at twice its place,
  usually off the picture, and the pull read nothing and said nothing; a
  mirrored frame read the other side. `_skin_patch` now maps scene points
  through the inverse display transform only, exactly as the WB Picker's
  click does (tested on a mirrored frame and with a prescale). Nothing
  fails silently now: "Couldn't read the picture under the circles".
- **Every drag adds a circle**, and all of them are read together (their
  pixels pooled); no Shift needed. A drag too small to be a circle is a
  click. **Right-click a circle to remove it.**
- **A circle is a button.** Each carries a small label, "Pull" or "Read"
  (the right-click choice), lit with the circle's inside while the pointer
  is over it (pointing-hand cursor); **a click on any circle acts on them
  all**: Read names the hue, Pull balances. Nothing happens on release of a
  drag any more. The answer is written under the circles ("Skin 112° · 17°
  towards magenta", "Pulled 112° → 129°", "already inside the range:
  nothing to pull") as well as in the panel's hint line, and goes when the
  circles change. Changing a right-click choice after an answer gives the
  new answer; before one it only relabels the buttons. `_SkinLabel` (fixed
  screen size), `skin_circle_at`, `skin_remove_at`, `skin_hover`,
  `skin_act`, `skin_show`, `_skin_set_result`.
- **The picture is a dotted outline** of the head and shoulders (dots 1.7
  units, 2.0 apart; the shoulders drawn from the middle out so the dots
  meet evenly), the user's choice of three spacings.
