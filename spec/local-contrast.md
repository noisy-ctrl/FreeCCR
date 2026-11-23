# Local Contrast: one list, five ways

The user, 2026-11-23: things "don't feel very 3d". After the developer edge
effect (spec/developer-edge-effect.md) and the lighting / detail draft
(`lighting-detail-draft-*.jpg`): "Could we create all? Changeable via a
dropdown box under details?" and "Edge Effect should be in the drop down
too."

## Goals / non-goals

* Details → **Local Contrast** list: Off, Edge Effect, Lighting & Detail,
  Local Laplacian, Detail Bands. One at a time; each shows its own sliders
  under the list, the others are hidden (their values are kept).
* Every method is an exact no-op with its sliders at 0, leaves grain as it
  is, never clips, never halos visibly, and is resolution independent.
* Non-goals: running two methods at once, area layers (whole image only,
  like sharpening), a GPU twin (it runs in numpy after the kernel, so CPU
  and GPU give the same picture).

## The methods

Edge Effect is unchanged (on the base's density, its own slider and options,
spec/developer-edge-effect.md); it now runs only when it is the chosen
method. The other three are output-referred, like sharpening: they run on the
finished picture right before sharpening (after the area composite and the
B&W collapse), on LOG LUMINANCE, and are applied as one gain per pixel on
linear RGB, so colour ratios are kept.

Common (`core/local_contrast.py`):

    lin  = sRGB → linear of the picture (core.oklab._lin)
    Y    = Rec.709 luminance,  l = log2(max(Y, 2^-14))
    P    = the picture's long side in px (the crop's, else the buffer's)
    σg   = 0.02 mm on the film (the edge effect's grain size, px/mm as there)
    fine = l − G(σg)·l        the grain's own band, never changed
    l'   = the method's new log luminance;  g = l' − l
    g>0 is limited by the headroom h = −log2(max channel): g ← h·tanh(g/h)
    out  = lin · 2^g → sRGB; a pixel whose g is 0 is returned exactly

* **Lighting & Detail** (`light`): base = edge-aware smoothing of l
  (domain-transform recursive filter, Gastal & Oliveira 2011: σs = 4 % of P,
  σr = 0.6 stop, 3 iterations; 1 stop left a halo by a dark tower against
  sky, 0.013 of white vs 0.005), detail = l − base − fine.
  `l' = a + c·(base − a) + (1 + (d − 1)·w)·detail + fine`, a = the base's
  85th percentile in the picture (the lighting turns about a bright level, so
  a sunset's glow stays and the shadows open). Sliders **Lighting**
  (c = 2^(L/100): 0.5..2) and **Detail** (d = 2^(D/100): 0.5..2).
* **Local Laplacian** (`llf`, Paris, Hasinoff & Kautz 2011; the fast form of
  Aubry et al. 2014): each output Laplacian level is interpolated from the
  levels of K remapped copies of l, remap
  `r(i; g) = g + c·Δ + (d − c)·σr·(tanh(u) − tanh(u/4))/(3/4)`, u = Δ/σr,
  σr = 0.5 stop: slope d for small steps (detail), back on the line g + c·Δ
  past about 4σr, so a strong edge gets no extra step; its slope stays
  ≥ 0.2 over every slider position, so it never turns back. K references
  spaced ≤ σr over the picture's 0.1–99.9 % range (8..16); the pyramid runs
  down to about 2 px and its top level is turned about the same bright level
  a by c (otherwise the frame-sized lighting would never move); levels finer
  than the grain (2^(k+1) ≤ 2σg) keep the input's Laplacian; the detail
  slope is per pixel, 1 + (d − 1)·w. Same two sliders, the same mapping.
* **Detail Bands** (`bands`): differences of edge-aware smoothings of l (a
  guided filter guided by l, `oklab.guided_smooth`, eps (0.35 stop)², so a
  strong edge stays in the coarser one; plain Gaussian bands threw +0.16 of
  white beside a hard edge, these +0.025) at the grain's size and box radii
  0.25 %, 1 %, 4 % of P: Texture, Structure, Shape. Each band's change is
  `w_b · lim·tanh((2^(S/100) − 1)·band/lim)`, lim 0.20 / 0.30 / 0.30 stop.
  Sliders **Texture**, **Structure**, **Shape** (−100..+100).

**Grain** (`core_weight`, the edge effect's coring): w = r²/(1 + r²), r = the
band's energy smoothed over 0.25 % of P / (9 × its 10th percentile in the
picture), so detail no stronger than the grain (plain sky) is left as it is;
decided on smoothed energy, so grain can't steer it. Measured on his Gold 200
frames at 4500 px, Detail +60 alone (Bands +60 each): grain on plain sky
×1.01–1.04; with Lighting −30 too, the sky is lifted and its grain rises
with its tone (×1.09), as with any brightening.

Measured on his frames: see the draft sheets (`local-contrast-*.jpg`).

## Data model

* Sliders (ADJUSTMENT_KEYS, created right after Edge Effect, so the zip
  holds): `lc_lighting`, `lc_detail`, `lc_texture`, `lc_structure`,
  `lc_shape`, −100..+100, default 0. Lighting and Detail are shared by the
  two methods that have them.
* `adjustment_settings["local_contrast"]` = "off" | "edge" | "light" |
  "llf" | "bands". Missing = "edge" when `edge_effect` > 0, else "off"
  (frames edited before the list), so `local_contrast.method_of(s)` is the
  one reader. Stored whenever the list is used; Reset clears it.
* Plumbed like the tools' options (load, rebuild, reset, sync with Details,
  copy). Paste: one Detail row **Local Contrast** (`local`; the old `edge`
  id is read as it) carries the method, the lc_* sliders, Edge Effect and its
  options.

## Integration

* `CCRImage.apply_adjustments`: Edge Effect where it was, only when the
  method is "edge"; the others in `_apply_local_contrast` right before
  sharpening; both skipped with `skip_dust`.
* Panel: Details → after the sharpening Bypass box: the **Local Contrast**
  row (label + list), then the method's rows (`_sync_lc_rows`). Choosing a
  method is one undo step.

## Tests (`tests/test_local_contrast.py`)

Each method: no-op at 0 (same object), a smooth ramp stays monotone and
nearly untouched, grain on a plain patch ≤ ×1.06 at +100, a step edge's
overshoot (halo) small (Lighting & Detail and Local Laplacian ≤ 0.02 of the
step; Bands ≤ the limit), nothing clips, the same in shares of the frame at
two sizes, colour ratios kept. `method_of` and the legacy edge rule. Panel:
the list's rows show/hide, choosing stores the method with one undo, edge
effect only renders when chosen, paste / copy / sync / reset.
