# Density crosstalk correction

## Problem

An RGB-backlit scan measures each dye layer of a colour negative through a
narrowband light, but the bands overlap the dyes' absorption curves a little:
the red channel sees some magenta dye, the green channel some cyan and yellow,
and so on. That **crosstalk** desaturates and shifts colours. A camera profile
fitted to a slide target corrects the wrong dyes and, worse, mixes the channels'
LINEAR values before the film base is removed. The orange mask is a per-channel
multiply on the light, which a mix does not commute with, so a residue is left
that is largest near the base: purple shadows (measured: blue's lead over green
in the darkest tones doubled, mid-tones turned cold).

## Goal

A correction applied where it is physically correct: in **density**, after the
film base has been removed. There the base is exactly 0 in every channel, so
any linear mix leaves it at 0, and the shadows cannot pick up a cast.

## Non-goals

- Fitting the matrix from scan pairs: that ships with the scan-matching (LUT)
  tool, which needs the same registered pairs. This spec defines the stage and
  the six numbers that tool will fill in.
- Area layers: a base-level correction, global only (like Cineon).
- Non-density bases: reference-frame (v0.2.3, linear) conversions, two-point
  LINEAR conversions, the no-anchor conversion (no measured base, so the base
  is not at 0) and Positive mode. The stage is skipped there and the panel says
  so.

## UX

Collapsible **Crosstalk Correction** directly ABOVE Channel Levels (the panel
reads in pipeline order, and this runs first), collapsed by default. Six sliders, -100..100, default 0:

    R <- G   R <- B   G <- R   G <- B   B <- R   B <- G

"R <- G" is how much of green's density is mixed into red (coefficient =
slider / 200, so +-0.5 at the ends). Negative values unmix, separating the
colours more; positive values mix. A muted hint explains what it does and, for
an image where it can't apply, why. Disabled while an area layer is selected.
Own sync group (**Crosstalk Correction**) and one paste row. Double-click
resets a slider to 0.

## Math

On a windowed base, display value `d = (code - WS_B) / (WS_W - WS_B)` is
optical density above the film base (times the conversion's slope), 0 at the
base. Each output channel is

    d'_i = d_i + sum_{j != i} c_ij * (d_j - d_i)

i.e. a 3x3 matrix whose rows sum to 1 (`M_ii = 1 - sum_j c_ij`). Consequences:

- the film base (all 0) stays 0;
- any neutral (equal densities) is unchanged, so greys never shift;
- only the differences between channels (colour) are re-mixed.

The result is re-encoded with `encode_window` (same precision as the base).
All-zero coefficients return the input untouched (no cost).

## Pipeline position

`CCRImage.apply_adjustments`, immediately after dust removal and before Auto
Gain is measured, Channel Levels and everything else, so later stages (and the
Auto Gain measurement) see the corrected base, and Channel Levels shifts cannot
move the base away from 0 before the mix. Applies when the base is windowed
and `conversion_inputs` is `mode: "bw"` with a black point and either no white
point (black-point-only, always density) or `density: True`
(`crosstalk_applies`). Every render path (preview, thumbnails, zoom, export,
the WB/AWB solves) goes through `apply_adjustments`, so they all agree.

## Data

Six adjustment keys `xt_rg, xt_rb, xt_gr, xt_gb, xt_br, xt_bg`, appended to
`ADJUSTMENT_KEYS` after `band_feather` and before the Chroma NR keys (the
sliders are created in that order). Saved with the image's adjustments like any
slider; catalogs without them read 0 (identity).

## Tests

`tests/test_density_crosstalk.py`: matrix rows sum to 1, identity is free,
base and neutrals preserved, sign convention, applies-to-mode rules,
integration through `apply_adjustments`, panel keys/group/paste/area gating.
