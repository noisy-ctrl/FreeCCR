# AWB guarded by the roll

The user (2026-10-31) agreed to: guard AWB with the roll the way auto white
point is guarded, so a sunset stays warm, while still taking out casts.

## Why
AWB (`core/awb.compute_awb_wb`) drives the rendered frame's estimated
illuminant to grey: a closed loop that REPLACES the white balance with the
full neutralising pair (temperature, tint). On a frame whose colour is the
scene (sunset, tungsten interior, open shade) that removes the scene.
Auto white point solved the same problem with a roll guard
(spec/auto-white-point.md); AWB had none.

## The rule (`awb.guard_wb(full, roll)`, in density units)
The sliders become density on the R/B and G/(R,B) axes exactly as the
White Balance stage applies them (`ccr_processor`): temperature
`log10((1+s)/(1-s))`, `s = T/100·0.40`; tint `log10((1+0.3t)/(1-t))`,
`t = tanh(N·0.02)·0.26`. Then, as auto white point does:
- With a roll (≥ 3 OTHER frames with a recorded full solve): the roll's
  correction (median of theirs) is applied in full; this frame's own extra is
  removed only up to **0.06** on temperature and **0.15** on tint.
- With no roll: temperature may warm by up to **0.20** (a cool cast is
  removed) but cool by at most **0.06** (a warm scene keeps its warmth); tint
  up to **0.15** either way.
Back to sliders by the exact inverses, rounded, clamped to ±100.

## Where
- `CCRBackend.awb_for(img, fill_roll)` → `(temperature, tint, note)`:
  solves the frame's full pair (`compute_awb_wb`), records it on the image
  (`img.awb_full`, catalogue key), gathers the roll from the other converted
  frames' `awb_full`; with `fill_roll` it first solves up to 6 other converted
  frames that have none (the AWB button; a busy cursor), never on a worker.
- The AWB button uses `awb_for(img, fill_roll=True)`; its right-click offers
  **Neutralise Fully** (the unguarded pair, as before).
- `maybe_auto_awb` (after conversion, Settings) uses `awb_for(img,
  fill_roll=False)`, so it never renders other frames, and records `awb_auto`
  as before.
- The hint says which applied: "guarded by the roll (N frames)" or "no roll
  yet: warm scenes keep their warmth".

## Tests
Density conversions round-trip; no-roll caps (a warm scene's cooling capped,
a blue cast removed); with a roll, the roll's correction passes and the frame's
extra is capped; fewer than 3 others = no roll; `awb_for` records `awb_full`,
fills the roll only when asked; the button's hint; Neutralise Fully gives
the full pair.
