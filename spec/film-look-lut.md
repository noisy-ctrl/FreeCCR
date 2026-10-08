# Film Look — a fitted 3D LUT as the decode out of density

## Problem

A lab scanner (Noritsu, Frontier) does not just invert a negative: it reads the
film's densities, balances each frame, and renders them through its own
film-specific tone and colour mapping. That mapping is the "lab look" people
want from a home RGB-light scan. FreeCCR's black-point conversion already
produces the right *input* for it — optical density above the film base, per
channel — but has no way to render density the way a lab does. Cineon
Log → Workspace is a fixed, neutral decode; Channel Levels and Channel Balance
are hand controls.

The look can be **measured**: scan negatives that already have lab scans, align
each pair, and fit the function that takes our density to the lab's output.

## Goals

1. **Fitter** (`tools/fit_look.py`): from pairs of (lab JPEG/TIFF, our RAF
   triplet or single RAF) of the same negatives, fit one shared look plus
   small per-frame corrections, validate it on held-out frames, and write a
   `.cube`, a JSON report and side-by-side comparison images.
2. **Film Look** in the app: load a `.cube` and apply it as the decode out of
   density, in the exact pipeline slot Cineon Log → Workspace occupies, so
   everything below it (Channel Balance, Master Gain, White Balance, the tone
   sliders, curves) keeps grading display-referred data.

## Non-goals

- Automatic per-frame density/colour correction (the lab's auto-balance). The
  per-frame corrections the fitter reports map onto Channel Levels sliders the
  user sets by eye; an "auto" is a later step.
- Area layers: whole-image only, like Cineon (areas grade the decoded base).
- Reference-frame and Positive conversions: the look is fitted on the
  black-point density base. If set on another base it still applies (the stage
  is a pure function of the value), but the result is not meaningful; the
  panel says so.
- A GPU kernel: the stage runs in the numpy pre-stage, like Cineon.

## Model (what the fitter fits)

For frame `k` with density base `d` (FreeCCR's default-slope base,
`d = 0.8·log10(black_point / x)`, the display value of the windowed base):

    lab_sRGB  ≈  F( d·(1 + g_k) + o_k )

- `F` — the shared look: a parametric prior (a 3×3 matrix in density, then a
  monotone cubic tone curve per channel) plus a smooth 3-D residual on a 17³
  grid (trilinear, Laplacian-regularised, pulled to 0 where there is no data,
  so unseen colours fall back to the prior instead of extrapolating wildly).
- `o_k, g_k` — per-frame density offset and per-channel contrast (the lab
  operator's density / colour corrections). Gauge: both are mean-zero over the
  frames, so "no correction" means "the roll's average lab rendering".

Validation is leave-one-out: fit on all frames but one, then fit only the
held-out frame's six numbers and measure CIEDE2000 against its lab scan. That
is exactly what a user does with a new frame (set its Channel Levels by eye),
so it is the honest accuracy figure.

The per-frame correction maps onto Channel Levels exactly
(`x = (d + s)/(1 − G)` with `s = shift/150`, `G = gain/150`):

    gain_slider  = 150 · g / (1 + g)
    shift_slider = 150 · o / (1 + g)

The report splits the shifts into Master Shift (their mean) + per-channel
remainder, so exposure and colour read separately.

## Fitter pipeline

1. **Decode** each scan through FreeCCR itself (`ccr_merge.merge_raw_channels`
   for a triplet — the same merge the app uses — or rawpy for a single RAW;
   camera profile None, half-size decode).
2. **Black point per frame** from the clear rebate: the brightest horizontal
   band in the top strip, centre columns, median. Per frame, not per roll,
   because a light can drift between triplets (measured: one channel's level
   changed ~1.8× between triplets at an identical shutter speed); a per-frame
   base cancels any per-channel light scaling exactly.
3. **Register** to the lab scan: SIFT on CLAHE-equalised luminance, ratio
   test, RANSAC homography, trying the four flips (scans are often emulsion-side
   mirrored); the lab file with the most inliers is the pair, so pairing is
   automatic. Typical reprojection error ≈0.3 px at the sampling scale.
4. **Sample**: 6×6-px blocks at ¼ lab resolution; keep blocks inside the image
   area (rebate excluded), unclipped in the lab file, and flat on both sides
   (edges are where residual misregistration bites).
5. **Fit** (numpy only — SciPy is not used: FreeCCR pins numpy < 2) and
   **validate** as above.
6. **Write** a 33³ `.cube` (`DOMAIN_MIN/MAX -0.25 … 1.25`, R fastest), the
   report and comparison images.

## UX

- Channel Levels section, directly under **Cineon Log → Workspace**: a
  **Film Look** combo — `None`, every `.cube` in the looks library, and
  `Import…`. Importing copies the file into the library
  (`<app data>/looks`, next to `camera_profiles`).
- Selecting a look **disables Cineon** (both are the decode; one at a time) and
  turns **Auto Gain off for that image's render**: the look already places the
  tones, and Auto Gain is a display-referred gain *after* the decode that would
  re-expose it. (The baked legacy auto-exposure `eb` is suppressed for the same
  reason, and so is the negative-look brightness baseline `brightness_base = −8`
  — a darkening power curve that is part of the *default* render; the look is
  the render, fitted with every slider at 0.) The user's own **Master Gain** still applies after the look, as it
  does after Cineon. Exposure with a look is **Master Shift** (a density offset, the
  lab's density key); colour is **R/G/B Shift**.
- Whole-image: disabled while an area layer is the edit target.
- Syncs with the Channel Levels group (like Cineon); one paste row
  ("Film Look: <name>").

- Crosstalk Correction still runs first (it is a base correction). A look is
  fitted with crosstalk at 0, so the two should not be stacked; the crosstalk
  hint says so when a look is set.

## Data

`adjustment_settings["look_lut"]` = the library file name (e.g.
`"Noritsu Lomo 800.cube"`), absent for None. It joins `GLOBAL_FLAG_KEYS`, whose
re-attach / sync / copy paths now carry the flag's **value** rather than
`True` (identical for the boolean flags). A missing file renders as if no look
were set and logs once.

## Processing

`look_lut.apply_look(d, lut)`: `d` float32 display values; per channel map the
domain to grid coordinates, clamp, trilinear-interpolate (8 gathers), in row
chunks of ~1 Mpx so a full-resolution export never builds multi-GB index
arrays. Output is
sRGB-encoded display values, the same contract as `apply_cineon_to_workspace`.
Position: `_apply_working_space_recovery`, right after Channel Levels, in place
of Cineon; the non-windowed normalised pass likewise; the OpenCL numpy
pre-stage runs when a look is set. `look_lut` is appended at the END of the
`adjust_image` / `adjust_image_opencl` / `_apply_working_space_recovery`
signatures (positional callers), as a parsed `LookLUT` object;
`CCRImage.apply_adjustments` resolves the name through a small cache keyed on
path + mtime.

## Tests

- `.cube` write → read round trip; DOMAIN handling; 1D/garbage rejected;
  identity cube is identity; trilinear exact at the nodes.
- Pipeline: a look replaces Cineon in the slot (Cineon flag ignored while a
  look is set); Channel Levels runs before it; Auto Gain suppressed; CPU/OpenCL
  parity; no look → byte-identical to before.
- Panel: combo lists the library, selection writes the key, area gating, paste
  row, sync and copy keep the name.
- Fitter: synthetic pairs from a known look + per-frame corrections are
  recovered (fit core only, small sizes); a synthetically warped/mirrored image
  registers back.
