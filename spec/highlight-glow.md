# Glow (Lab Finish)

A soft highlight glow: bright areas bloom gently into their surroundings,
like a diffusion filter on the enlarger or the soft spread of light round
highlights in a good print. A slider in the Lab Finish section, under
Shadow Neutralise, with right-click options.

## Goals / non-goals

- **Glow** 0..100 (default 0), key `glow`, "sat" sync group (with the other
  Lab Finish tools), created right after Shadow Neutralise.
- Right-click: **Size** (how far the light spreads, "Tight" ↔ "Wide"),
  **Start** (how bright a tone must be to glow, "Lower" ↔ "Higher"),
  **Show where it acts** (the glow field as an overlay), Reset. Stored as
  `adjustment_settings["glow_shape"]`, None when default.
- Non-goals: halation (a red-orange fringe from the film base) and the HDR
  export's gain-map glow, which is a different thing (it is not in the
  picture's pixels).

## Processing (`core/glow.py`)

On display values (sRGB-encoded 0..1), float32 throughout, light in LINEAR
light (glow is light adding up):

1. **Sources.** `b = lin · s(L)`: each pixel's linear RGB times a soft knee on
   its lightness, `s = smoothstep((L − (start − 0.10)) / 0.20)` (OKLab L of
   the decision view, so grain can't switch a pixel on and off). Only tones
   round and above Start give light; it keeps their colour, so warm sunlight
   glows warm.
2. **Spread.** The sources are box-averaged down (energy-conserving) to a
   size where the narrow kernel is still ≥ 4 px, blurred with two Gaussians
   — σ and 3σ, weighted 0.65 / 0.35, a natural core with a longer tail like
   real diffusion — and scaled back up bilinearly. σ = Size as a fraction of
   the picture's long side (0.3 % at 0 to 4 % at 100, exponential; default 40
   → about 0.9 %), so the glow is the same at every render size. The field is
   smooth by construction (blurred far below its sampling), so scaling it up
   leaves no steps; a test checks second differences at export size.
3. **Hold the highlights' own detail.** `g = k · glow · (1 − h(L_h))`,
   `h = smoothstep((L_h − (start − 0.20)) / 0.40)`: a bright area takes none
   of its own glow (from Start + 0.2 up) while the tones round it get the
   spread. A wider ramp than the source knee, so the result stays monotone
   along a ramp. `L_h` is the decision lightness smoothed along edges at
   half the glow's size (guided filter, radius 0.5 σ, ε 0.1²): texture
   inside a highlight can't modulate the hold, outlines stay put. *Revision
   1 read the hold per pixel at 0.75 and kept only 0.78 of a big
   highlight's texture (darker texture took more glow, flattening it); now
   0.94 is kept (high-pass, test pinned at > 0.93).*
4. **Combine without clipping.** Screen in linear light,
   `out = 1 − (1 − lin)(1 − g)`: never above 1, never darker, and the local
   slope `(1 − g)` ≤ 1, so grain is never stretched (it is gently compressed
   where the glow is strong).
5. Pixels where `g < 1e-6` are returned exactly.

k = amount/100 · 0.6.

## Pipeline

The very last colour stage, after Shadow Neutralise (light spreading over
the finished picture, as a diffusion filter would; neutralised shadows next to
a light take its warm spill), in `adjust_image` and on the OpenCL result in
numpy. `glow`, `glow_shape` appended LAST to `adjust_image` /
`adjust_image_opencl`; `CCRImage` passes them from each layer's settings.

## Measured

- Grain where it acts (16 px blocks lifted > 0.01, high-pass σ 1.5), 13
  real frames (5 Noritsu lab scans, 8 camera scans) at 100: median 0.75–0.89,
  worst block 0.999 — it never grows (the lift compresses it slightly, as a
  veil of light does).
- Spreading on the reduced copy vs a full-size Gaussian (2160 px): max
  difference 2e-3 (half an 8-bit code, at a light's edge), mean 3e-5.
- Speed (2-core container, slower than the user's Mac): 1080 px preview
  150–220 ms; 12 MP export 3.4 s (linearise 0.5, decision L 0.7, hold 0.6,
  encode 0.6, spread 0.2).

## UI / data model

Plumbed exactly like `shadow_shape` (load / attach on rebuild / reset / one
undo per popup / "sat" sync group / copy / its own paste row). The overlay
shares the Golden/Shadow layer (`ImagePreview.set_glow_mask`), drawn in amber;
its map (`glow.weight_map`) is the display lift at 100, × 4 (a lift of a
quarter of the range reads full). The popup's before/after strip is the
middle third of a picture three times wider (Size is a share of the long
side; a strip rendered as a whole picture would show the glow 3× smaller
than on a frame), drawn at 2× and scaled down.

## Tests (`tests/test_glow.py`)

0 is a byte-identical no-op; far from any highlight the picture is returned
exactly; a bright spot glows into its dark surroundings, falling off with
distance; the brightest area keeps its own detail (a texture inside a big
highlight changes little); output in 0..1, never darker than the input; a
ramp stays monotone; grain is never stretched; the glow field is smooth at
4320 px (no steps); the same picture at preview and export size glows alike
(the export downsampled matches the preview); Size widens it, Start moves
where it begins; options normalise; GPU == CPU; panel: slider in Lab Finish
under Shadow Neutralise, popup writes `glow_shape` with one undo, paste row,
copy, sync, the mask follows the popup.

## Revision 2 (2026-10-23): only light inside the crop glows

Renders are of the whole frame and the crop comes after, so light OUTSIDE a
crop (the white holder a negative scan turns into, sprocket holes, light
round the film) bloomed into the picture's edges. Measured on a dark picture
in a white surround at 100: +116 8-bit levels at the edge, +36 at 20 px in.

Now `CCRImage` passes the crop (`glow_area` = normalised box + angle,
appended LAST to `adjust_image` / `adjust_image_opencl`, and to
`FinishGPU.finish`). The sources are multiplied by the crop box's coverage
`m` (analytic, one-pixel soft edge, `glow.crop_mask`; the GPU's `glow_mask`
computes the same) and the glow is the NORMALISED spread
`spread(b·m) / max(spread(m), 1e-3)`: inside the crop every pixel sees the
average light of the part of its neighbourhood that is picture, so the edge
neither takes light from outside nor dims (a bright field running into the
edge glows the dark beside it as in the middle, within 15 %). Outside the
crop the values don't matter (cropped away). No crop, or a crop of the
whole unrotated frame (`normalize_glow_area` → None): the old path, byte
for byte. After: 0 levels of lift from the surround (< 2e-6).


## Revision 3 (2026-10-23): only the near-white highlights, and tight

The user's direction: glow should come only from tones above about 95 %
(almost always), and stay tight, about 5 % of the frame, unless the highlight
itself fills the frame.

- **Start** is now a display level, 85–99 %, default **95 %**: the source
  knee runs from the OKLab L of that grey (`glow.grey_lightness`: cbrt of its
  linear value; 95 % → L 0.961) to white, a smoothstep on the decision
  lightness as before. It began at L 0.65 (Start 75 − the 0.10 knee), so
  skies and pale walls bloomed. A warm lamp at (1, 0.92, 0.78) is 93 % bright
  overall and no longer glows at the default; Start lower lets it.
- **Size** became **Reach**: how far from a highlight's edge the glow fades
  out, as a share of the long side, 2–10 %, default **5 %**. Reach = twice the
  tail's sigma = 6 core sigmas (`REACH_SIGMAS`). Measured on a white disc
  (r = 60 px on 1080): the glow at Reach is 5 % of its value at the edge and
  under 0.002 at twice Reach. A highlight that fills the frame glows over it
  anyway (the reach is from its edge). Size went to 4 % sigma (24 % reach).
- The hold ramps from 0.30 below Start's lightness to full at white
  (`HOLD_SPAN`; it was Start ± 0.20), so a ramp stays monotone and a
  highlight still keeps its own detail.
- New option keys, `reach` (tenths of a percent) and `above` (%), so options
  saved as `size` / `start` under the old meaning fall back to the defaults
  instead of being misread. The popup shows them as "5.0 %" / "95 %".
- The GPU twin takes the same knees from `glow.knees`; its kernels are
  unchanged.

Tests: the old ones on the new options, plus "only near-white glows by
default, and it stays tight" (an 85 % wall is returned exactly; the fall-off
by Reach).


## Revision 4 (2026-11-19): halation, and Auto

The user: "Can you add an auto button to the glow settings? It looks at the
spread of the brightest areas and more to set size. It looks a bit
artificial too. Can you research how glow/halation looks on film? And make
it more organic?"

### What film does (research, then measured)

- Halation is light that passes through the emulsion, reflects off the back
  of the film base and re-exposes the emulsion from behind round a bright
  area. The layer nearest the base is the red-sensitive one, so the halo is
  red-orange: orange right at the edge, redder further out. An anti-halation
  layer weakens it but doesn't stop it, so very bright lamps, specular
  highlights and sunlit edges against dark keep a thin warm rim
  (sources in the reply).
- Measured on the user's lab scans (35 mm, 6774 px long side), across
  clipped-highlight edges into dark, in linear light, fitting the light past
  the edge with Gaussian edge profiles:
  - a sunlit patch on asphalt (`060940030037`): the image's own edge
    (sigma 4 px), a **red-orange rim, sigma 10 px = 0.15 % of the long side,
    R : G : B = 1 : 0.29 : 0.09**, and a weak neutral bloom (sigma ~60 px,
    0.9 %, about 1/17 of the rim's strength); R/G rises outward (1.9 at 4 px,
    2.6 at 8 px): orange then red;
  - sky over a dark arch (`060940010002`): the rim at **sigma 6 px = 0.09 %,
    R : G : B = 1 : 0.34 : 0.35** (the bloom there is hidden by palm fronds).
- So film's glow is mostly a **thin warm rim**, about 0.1 % of the frame on
  35 mm: about 0.04 mm on the film, so a smaller share of a larger format.
  The old Glow was only the wide neutral bloom (reach 5 %), a soft veil in
  the light's own colour, which is what looked artificial.

### The change

- Glow = **bloom** (the old field, unchanged) mixed with **halation**:
  `g = (1 − h)·bloom + h·halo`, then the hold and the amount as before. With
  `h = 0` the halation is never built and the picture is the old Glow.
- **Halation** (`core.glow.halo_field`): its source is the light's
  luminance, not its colour (the rim is the film's colour): `y = Y(lin)·s`,
  Rec.709 Y. Each channel spreads it with its own Gaussian, `sigma_c =
  rim · (1, 0.75, 0.5)` and weight `(1, 0.40, 0.12) · HALO_GAIN` (0.8), so
  the rim is orange at the edge and red further out: past a white edge on a
  20 % grey, at the defaults, R/G is 1.7 at 1 px, 1.5 at 8 px, 1.0 by 32 px
  (where only the bloom is left); measured on the scans 1.9 at 4 px, 2.6 at
  8 px with the halation alone. `rim` is a share of the long side. With a
  crop, normalised by the mask's spread per channel, as the bloom.
- **The halation's knee** reads `_halo_lightness(L)`: the 3×3 brightest of a
  3×3 median of the decision lightness (× the crop mask when there is one).
  The brightest, because the knee is steep (Start to white): a pixel a
  highlight's edge only partly covers fell under it, which at a 1080 px
  preview moved the source a whole pixel inward and showed the 1–2 px rim
  ~40 % weaker than the export scaled down; now 15 % at the edge pixel and
  12 % beyond it (the bloom itself differs by 7 % at its edge pixel). The
  median first, because the edge-preserving denoise leaves grain at edges
  and the brightest of noisy values made the rim carry grain (×1.18 in its
  first pixel; ×0.84–0.95 with the median).
- Options (right-click), new keys added to the old ones; shapes saved
  before take their defaults:
  - **Halation** `halo` 0–100 %, default 75: how much of the glow is the red
    rim ("Soft bloom" ↔ "Red rim").
  - **Rim** `rim` 0.03–0.40 % of the long side (hundredths of a percent),
    default 0.12 % (35 mm).
  - Reach (the bloom) and Start as before.
  - The popup gains a close-up strip: a bright edge in a 4000 px picture at
    5×, so the rim (5 px there) can be judged.
- **Auto** (`core.glow.auto_shape(rgb01, shape, long_mm)`), a button in the
  popup, measured on the frame on screen rendered WITHOUT its glow, inside
  its crop (`SlidersPanel._glow_auto_inputs`), on a copy 512 px long:
  - **Start** so the lightest ~1 % glows: the 99th percentile of each
    pixel's lightness as the display level of a grey as light (the scale
    Start is set in; a saturated red at 98 % red is not near white), 85–99 %;
  - **Reach** from how big the light areas are (those at Start): each
    area's inner radius (the largest circle inside it, the frame's edge
    counting as an edge, so a band of sky is as big as it is tall), the
    area-weighted median, `Reach = sqrt(radius × 0.06)` (a bigger light
    glows further, less than in proportion: 2 % for a lamp, 3 % for a bulb
    2 % of the frame across, ~6 % for a window), shortened by
    `sqrt(0.05 / share)` when more than 5 % of the frame is light (a band of
    sky over 37 % of the frame: 2.7 %), 2–10 %; nothing light: Reach kept;
  - **Rim** from the film format, a fixed 0.043 mm on the film
    (`film_formats.long_mm`): 35 mm 0.12 %, half frame 0.18 %, 6×4.5 / 6×6
    0.08 %, 6×7 0.06 %, 4×5 0.04 %, 8×10 0.03 % (the frame's Crop-panel
    format, else its capture guide's, else what auto crop found or its
    crop's proportions say; unknown keeps the Rim);
  - Halation and the amount are left as they are.
  - On three of the user's lab scans: the sunlit patch → Start 89 %, Reach
    2.3 %; the arch → 98 %, 4.3 %; the court → 93 %, 2.4 %.
- GPU: `finish_gpu` gains the halation field (`med3` + `max3` for the knee,
  the source in luminance through `glow_area_h`'s `luma` flag, one
  separable pass with a kernel per channel, `gauss3k_h/v`, OpenCV's kernels
  zero-padded to one length) and mixes it in `glow_apply` (`field_at`).
  CPU == GPU within 0.02 of a 16-bit code on the test scenes (pocl).
- Cost (numpy, this machine): 30 MP export 7.4 → 9.1 s; a 2400 px render
  0.49 → 0.77 s.

### Tests

- `halo = 0` never builds the halation; the rim is warm (R > G > B past a
  white edge, R/G > 1.5) and redder further out; the default is warm at the
  edge and neutral 64 px out; it is tight (by 4 rims < 5 % of its edge
  value) and widens with Rim; the preview is the export scaled down (above);
  never darker, in range, a ramp stays monotone; grain doesn't grow (bloom
  ring, default ring, the rim's own first pixels); the crop keeps the holder
  out; options normalise (old shapes take the new defaults); GPU == CPU with
  halation on, reduced and cropped; Auto: Start follows the lightest
  percentile and ignores a saturated red, Reach grows with the light but a
  big sky is shortened, nothing light keeps Reach, Rim from the format and
  Halation kept; the popup's Auto sets the sliders and the close-up shows a
  warm rim; the panel measures the frame without its glow, inside its crop.
