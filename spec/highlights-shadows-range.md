# Highlights / Shadows — range and keep-endpoint controls

## Problem

Highlights and Shadows add a broad "bump" to the tone curve:

    x' = x + (h/100)·0.30·x³(1−x)/P  +  (s/100)·0.30·x(1−x)³/P      P = 0.10546875

Pure black and pure white are pinned exactly, but the bumps peak at 75 % / 25 %,
reach well into the midtones (mid-grey moves 0.18 at ±100) and are steep at the
ends, so the tones *just inside* black and white move a lot:

- +100 Highlights clips everything above ~71 % to white;
- −100 Highlights pulls a 95 % highlight to ~83 % (only perfect white stays);
- −100 Shadows crushes everything below ~29 %; +100 lifts near-black to grey.

On screen that reads as the white / black point moving. Users want, sometimes,
to work on the highlight or shadow *region* while the brightest highlight stays
white and the deepest shadow stays black — and to choose how far into the
midtones each tool reaches.

## Goals

- Per tool (Highlights, Shadows), two settings, reached by **right-clicking
  the slider's label**:
  - **Keep white point** / **Keep black point** — the effect fades to zero
    with zero slope before the endpoint, and is strength-limited so the curve
    is monotone and never clips. Only tones strictly inside the region move.
  - **Range** (Narrow … Wide) — how far into the midtones the tool reaches.
- Defaults reproduce today's render **byte-for-byte** (no catalog changes).
- Works on area layers (each layer carries its own shape), syncs with the Tone
  group, copies / pastes with one row, undoes in one step.

## Non-goals

- Spatially adaptive (local-contrast) highlight/shadow recovery as in
  Lightroom. This stays a global per-channel tone curve.
- Changing the default behaviour.

## Data

`settings["tone_shape"] = {"hl_keep": bool, "hl_range": 0..100, "sh_keep": bool,
"sh_range": 0..100}`, in the active layer's settings dict (global or area), absent
when every value is default (`keep` False, `range` 50). Nested like `curves`;
the slider → dict rebuild re-attaches it alongside the curves.

## Maths

`r` = range (default 50). Both region terms are computed from the input `x`
and added, as today.

**Classic (keep off)** — today's bump with the exponent driven by range:

    α = 3^(1 + (50 − r)/50)            r = 50 → 3 (today), 0 → 9 (narrow), 100 → 1 (wide)
    highlights:  w = x^α (1 − x) / P(α)        P(α) = (α/(α+1))^α / (α+1)
    shadows:     w = x (1 − x)^α / P(α)
    x' = clip(x + (v/100)·0.30·w, 0, 1)

At r = 50 the existing inline code runs unchanged (byte-identical).

**Keep endpoint** — a bump with zero value *and* zero slope at both ends of a
region that stops short of the midtones and ends exactly at the endpoint:

    highlights region [lo, 1],  lo = 0.8 − 0.6·r/100      (r = 0: top 20 %, 100: from 20 %)
    shadows    region [0, hi],  hi = 0.2 + 0.6·r/100
    g(t) = (4 t (1 − t))²,  t ∈ [0, 1] across the region, 0 outside
    max |g′| = 32·t(1−t)(1−2t) at t = (3 − √3)/6  ≈ 3.0792
    c = (v/100) · min(0.30, 0.9 · width / 3.0792)
    x' = x + c · g

The cap keeps the slope ≥ 0.1 everywhere, so the curve is monotone, the region
ends are untouched and nothing reaches 0 or 1 that was not already there. When
both tools are active (their regions can overlap), the combined curve is
additionally forced monotone (`maximum.accumulate`).

**Application.** A custom shape is evaluated as a 4097-point curve over
[0, 1], direct-indexed (uniform grid, no search) and linearly interpolated
(error ≈ 0.01 of a 16-bit level) by `_apply_tone_curve`, shared by both paths.
The OpenCL kernel keeps its inline default. With a custom shape set,
`adjust_image_opencl` runs the kernel stages that precede Highlights/Shadows
(Channel Levels on a non-windowed base, Gain, Brightness — WB is already
consumed in numpy) plus the custom curve in numpy, in `adjust_image`'s exact
order and arithmetic, then zeroes them for the kernel: parity is exact and the
rest stays on the GPU (≈ +25 ms on a 1080-px preview).

## UX

- Hovering either label shows "Right-click: range and endpoint options".
- Right-click opens a small popup at the cursor: title, **Keep white point**
  (Highlights) / **Keep black point** (Shadows), **Range** slider with
  *Narrow* / *Wide* ends, a live curve preview (the curve at the current slider
  value; at 0, the ±100 curves faintly, to show the region), and **Reset**.
- Changes render live, debounced like a slider; the whole popup session is one
  undo step.
- A customised tool's label is underlined (the theme is deliberately
  colour-neutral) and its tooltip states the setting, so a non-default shape is
  visible without opening the popup.
- The popup is a frameless `Qt.Popup` window (closes on an outside click),
  not a QMenu, so dragging its slider never dismisses it.

## Integration points

- `ccr_processor`: `normalize_tone_shape`, `tone_region_curve`; `tone_shape`
  appended LAST to `adjust_image` / `adjust_image_opencl` (positional callers).
- `CCRImage.apply_adjustments` and `_adjust_for_area` pass
  `tone_shape=s.get("tone_shape")`.
- `SlidersPanel`: label context menus, popup, `_attach_curves` also attaches the
  shape, `_load_active_layer` loads it; copy / paste row / Tone-group sync.

## Tests

- Default and absent shape are byte-identical to today, CPU and GPU.
- Classic at r = 50 equals the old formula; narrower range leaves midtones
  closer to identity, wider moves them more.
- Keep mode: monotone; 0 and 1 fixed; tones within 1 % of the endpoint move
  < 0.5 % at any range and ±100; nothing new reaches 0 or 1; tones outside the
  region untouched.
- Both tools in keep mode with overlapping regions stay monotone.
- GPU path equals CPU with a custom shape; area layers receive the shape.
- Panel: label context menu exists; popup edits write `tone_shape` to the active
  layer; a slider move keeps it; reset removes it; copy/paste row; sync copies
  it with Tone and preserves it otherwise.
