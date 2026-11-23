# Developer edge effect

The user, 2026-11-23: "A micro contrast tool? … Sometimes, things don't feel
very 3d." Then, of the options: "Could we try a developer edge effect?" and,
of the draft on his Gold 200 frames (`developer-edge-effect-draft.png`): "0.1
looks good for a default."

## What it is

During development, fresh developer diffuses from thin areas into dense ones
beside them and exhausted developer the other way, so a dense area develops a
little denser at its border and a thin area a little thinner (Mackie lines;
the Eberhard effect where narrow areas meet). It is a fraction of a millimetre
wide, it is why colour films' datasheets plot sharpness above 100 % at a few
cycles per mm (DIR couplers are built in to make it), and it is part of why a
negative can look crisp and three-dimensional.

The tool adds that effect to the scan: contrast between the size of the grain
and the developer's reach, on the negative's density, with the reach set in
millimetres on the film.

## Goals / non-goals

* One slider, **Edge Effect** 0..100 in Details under the sharpening, key
  `edge_effect`, default 0 (off: the render is byte for byte as before).
* Right-click the label: **Reach** (0.05–0.50 mm, default 0.10) and **Each
  dye layer on its own** (default off: the three together, so no colour
  fringes) → `adjustment_settings["edge_shape"]` (None when default), plumbed
  like `shadow_lift_shape`.
* Resolution independent: the reach is in mm on the film, so the fitted view,
  a zoom and every export agree, and 35 mm and 120 look alike.
* Not a sharpener: the grain stays as it is, and plain areas (sky) are left
  alone. Sharpening stays where it is, for the finest detail.
* Non-goal: modelling the developer chemically (agitation, dilution). One
  amount and one reach.

## Processing (`core/edge_effect.py`)

On the base, right after the dust heal and before every other stage
(`CCRImage.apply_adjustments`), so it acts on the negative's density as a
developer does and everything downstream (Channel Levels, the look, the tone
tools) sees it. `v` = the de-windowed value (window-normalised: linear in
density on a `bw` conversion; a non-windowed base is read as `/65535`).

    x     = mean of the channels (or each channel with "each dye layer")
    band  = G(σg) * x − G(σd) * x            σg = 0.02 mm (grain), σd = reach
    E     = G(σd) * band²                    the band's energy, smoothed over the reach
    n²    = 10th percentile of E in the picture (crop, else the middle)
    r     = E / (CORE² n²),  w = r² / (1 + r²)         CORE = 3
    v'    = v + a · LIMIT · tanh(w · band / LIMIT)     a = amount / 100, LIMIT = 0.04

* **Band, not a high-pass**: the inner blur at the grain's size keeps the
  grain out (an unsharp mask at the same radius took grain ×1.65).
* **Coring decided on smoothed energy**: where the band is no stronger than
  the grain's own share of it (plain sky), `w` → 0; the weight is read from
  energy smoothed over the reach, so grain can't steer it, and it is C¹.
* **Soft limit**: a developer's effect saturates; `tanh` caps a halo at
  `a · LIMIT` (≈ 10 levels at 100 on a full-contrast edge, measured 6).
* Pixels per mm: the crop's long side in pixels over the frame's long side on
  the film (`film_formats.long_mm` of the frame's format: Crop panel → capture
  guide → auto crop's → the crop's proportions; unknown = 35 mm's 36), without
  a crop the buffer's long side.
* Wide blurs run on a copy reduced so sigma stays ≥ 8 px and scaled back up
  (within 1.3e-4 of a full blur at sigma 22 px; ~20× quicker).
* float32 inside, the result rounded to the base's codes once; amount 0
  returns the input object.

The app's version matches the approved prototype within 1 code (16-bit)
inside the frame on his frames 001 and 005 at 1080 and 4000 px (the outer 2 px
differ: BORDER_REFLECT). Time here: 0.06 s at 1080 px, 1.4–1.7 s at 4000 px,
3 s at 8000 px (field only once per base; the amount alone afterwards).

Measured on his Gold 200 frames (draft, amount 100, 0.1 mm): cloud form ×1.14
at the fitted view and ×1.35 at 100 %, sky mottle ×1.06, grain ×1.02, halo 6
levels beside the lighthouse (unsharp mask: ×1.23, ×1.61, ×1.65, 16 levels).

## Integration

* `CCRImage._apply_edge_effect(image, amount, shape, ws)` after
  `_apply_dust_removal`; skipped with `skip_dust` (closed-loop patch renders:
  spatial, meaningless on a patch). Cached per image on the buffer's content +
  options (three entries: preview, draft, detail render; up to 6500 px,
  float16), since the base is re-rendered on every slider move; the amount
  is applied afresh each time, so moving Edge Effect itself is quick.
* CPU and GPU render the same picture: it runs in numpy before either.
* Panel: `create_slider("Edge Effect")` right after Masking (laid out
  under sharpening's "Bypass until export", which isn't its own); `edge_effect`
  appended LAST to `ADJUSTMENT_KEYS` (positional zip); in the `details` sync
  group and the Details paste row with `sharpen_*` (`_SHARPEN_KEYS`); the
  options ride the same group/row. Label tooltip explains it; options popup
  `widgets/edge_popup.py`.
* Catalogues: the key and options are additive; older catalogues read 0.

## Tests (`tests/test_edge_effect.py`)

No-op at 0 (same object); a step edge gets a border and a fringe within the
limit and the step itself kept; grain on a flat patch barely grows (≤ ×1.05)
while an unsharp mask of the same reach grows it; mid-scale structure grows;
resolution independence (the same frame at two sizes, the profile across an
edge matches in mm); each-layer vs together on a neutral edge are equal and
together makes no colour fringe on a coloured edge; options normalise; the
panel stores `edge_effect` / `edge_shape`, syncs and pastes them with Details.
