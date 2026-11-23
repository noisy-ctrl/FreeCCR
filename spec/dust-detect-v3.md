# Dust: finding whole strands, not edges, and healing all of them (revision 3)

The user, 2026-11-23, after the patch-fill heal (spec/dust-patch-fill.md):
"Dust tool seems loads better … but I think we need to A) Improve detection,
visually and from IR B) Improve selection (see screenshot). There are
remnants of strands left. C) differentiate more clearly what is IR if there
isn't already a colour scheme."

His frame R0026 12 (trichrome + a 30 s IR shot, Colorplus), Dust 100, his
learned model: 118 found, 177 spots painted over. Measured on it here:

* **Remnants** come from the heal, not the finder. A found spot replaced only
  the speck its tracer found inside the spot's area. His top-left strand is a
  faint, dotted, green-only line; on the 4500 px detail render the tracer
  found one bright blob on a 16-point stroke and replaced 40 px of it, so the
  rest of that stroke stayed (reproduced; at 2000 px the strokes are too
  narrow to trace, the whole area goes, and it came out clean). The curl
  kept its bottom loop the same way.
* **Strokes along the bright wall's edges** are false finds. At a strong edge
  the IR and the colour shots disagree by a fraction of a block, which leaves
  a thin dark line in the de-pictured IR; the colour shots step across it and
  don't dip on it. At Dust 100 those pass (default model p 0.08–0.14).
* **Strand ends are missed** where the IR shows the fibre only as a row of
  faint dots: its pieces score p 0.03–0.07 and aren't joined.
* **It was slow**: 15–18 s for one heal at 4500 px, most of it tracing long
  strokes in boxes as big as their length, and PatchMatch's patch gathering.

## 1. Edge ghosts are never flagged (`ir_dust.edge_ghost`)

A candidate that is line-like, at least `GHOST_LW` (5) times as long as it is
wide, and shallow in the IR (z depth under `GHOST_DEPTH` 5) is a ghost when,
along its (thinned) skeleton, the colour shots' log level (summed, σ 1
block) steps across it by at least `GHOST_STEP` (0.25 ln, median of
|a − b| at `GHOST_OFF` 3 blocks either side) and dips on it by less than
`GHOST_DIP` (0.3) of that step (median of mean(a, b) − centre). Dust only
darkens; an edge steps. `Candidate.ghost`; `flagged` skips ghosts and never
joins them; the model and its learning are untouched (a hard rule, so a
learned model can't let them back in).

Measured: frame 12, 22 of 213 candidates (every stroke along the two walls,
plus the frame edges outside the crop); the four hand-labelled Gold 200
frames, 66–77 of ~1600 each, **0 of the 59 labelled dust** and 6 of the 126
labelled not-dust. A deep IR dip (≥ 5) is never a ghost: two real specks
with tails along texture (z 6.6, 7.4) would otherwise have gone.

`DustMap.vlog` keeps the colour shots' smoothed log at half the block grid
(float16, ~6 MB on 50 MP) for the tails' edge check.

## 2. A strand is followed past where the IR loses it (`ir_dust.tails`)

From each free end of a flagged, line-like, non-ghost piece (its skeleton
pruned of spurs, `prune`), the way out is the main axis of the piece's pixels
within `TAIL_AXIS` 20 blocks (a thinned end hooks: 50° off on his strand).
The walk starts where the piece's own mask ends and goes on in straight
chunks of `TAIL_CHUNK` 8 blocks, each turning ≤ 20° and stepping ≤ 1.5
blocks aside (the first: ≤ 4 aside, the end is a guess), read over the
whole chunk so a dotted fibre counts. A chunk lands when, on the joint field
0.7·z/|tz| + 0.3·vz/|tv|, it is darker than chance (`tail_thresholds`: the
1st percentile of the best chunk at 300 seeded random places in the frame's
middle, per map, so a clean 30 s IR shot and a noisy 8 s one each get their
own), darker than both sides 3 blocks out by 0.3 of that, dark in the IR by
itself (≤ 0.6·tz: followed on the colour shots alone, tails ran along lines
and edges of the picture), not lying along an edge (§1's rule on `vlog`), and
at most 40 % over its own piece. One chunk may fall short; two landed chunks
make a tail; ≤ 80 blocks; it stops on reaching another flagged piece. Spots
kind `ir`, `ir_tail` = the piece, its r and core; no features.

Measured: his strand A gets 70 blocks of tail (the faint lower third); Gold
200 frames 0–6 tails a frame at Dust 100, looked at one by one: on fibres, or
on a dark line the IR also shows beside a bright stripe.

**Not found yet** (left for later): a fibre that is in one colour shot only
and not in the IR (it moved between shots). Picture lines can also be one
colour, so that needs its own evidence.

## 3. Selection: an IR find always loses what the IR saw (`dust_fill`)

* `ir_core(spot)`: where the IR itself saw the dust: a stroke's stored
  `ir_rc` (new finds: the IR's own half-width, `ir_dust.core_width`: pixels at
  least half as deep as the deepest within 4 blocks along it, + 0.75 block),
  else half its generous radius (`IR_CORE_STROKE`, his existing spots), a
  dab's half its own radius (`IR_CORE`). The spot's area itself stays as
  generous as before: the IR and the colour shots can be 3 blocks apart along
  a fibre (measured), and a narrower area left the strand.
* The hole of an IR spot = the traced speck ∪ its core; with nothing standing
  out, its core (not the whole area). Marks, AI finds and brush strokes are
  as before.
* The tracer is **anchored** for IR spots: the core seeds it wherever the
  picture stands `GROW_Z` clear, and it is followed at `GROW_Z`, not relative
  to its peak (a fibre's faint stretches are as much the fibre), one polarity
  only (dust prints white).
* The tracer reads **colour** too: planes = the grey + each channel, each
  normalised by its own ring, a channel counting `CH_PENALTY` 0.6 less
  (measured: the channels' best grain score is 0.5 above the grey's at p50
  and p90). His strand is green only: a third as strong in the grey.
* A long stroke is traced in **pieces** of ≤ `PIECE_LEN` 48 px (its own peak
  each); top-hat sizes come from the area's inscribed radius, not its area
  (`sizes_for`), and the box round each piece from those sizes (`_boxed`),
  not from its length.
* The finest PatchMatch level **copies on every pass** (`CENTRE_ITERS` =
  `ITERS_FINE`): an averaged guess is smoother than any real patch and drew
  smooth sources (a 9 px hole kept 0.67 of the grain; copying 0.88–0.98).
  Bench (spec/dust-patch-fill.md): MAE 17.48 → 17.71, detail 0.97 → 0.98.
* Speed: patches gathered from a sliding-window view; votes accumulated over
  the hole's pixels only.

Measured on frame 12 (his spots → found again here, Dust 100, default
model): strand A and the curl leave nothing at 2000 and 4500 px (before: a
stretch of the strand and the curl's bottom loop at 4500); the two walls'
edges are left as they were (before: the edge redrawn and its fringe taken
off). Time for one heal: 4500 px 17.8 s → 7.5 s, 2000 px 5.9 s → 2.6 s,
1080 px 0.87 s → 0.58 s. With his existing 177 spots and the new heal:
11.2 s at 4500.

## 4. Colours (C): a draft for sign-off, not built

There is a scheme already (amber = the IR shot, blue = the colour shots'
marks), but it shows only before Paint Over and is named only in the status
line. Drafted (`dust-colours-draft.png`): a legend on the canvas, bottom left
like False colour's, and after Paint Over thin dashes round what was healed
in the colour of what found it (white for the brush).

## Tests

`tests/test_dust_detect_v3.py`: the ghost rule (a step edge, a dark line,
flat), the IR scene's ghost not flagged while a fibre is; pruning a spur and
the staircase end; tails on a dotted continuation and none on a
colour-shots-only line or with no continuation; the stroke core is narrower
than the area; a dotted, faint IR strand heals end to end where the old
selection left it; a one-channel defect is traced; long strokes split.
Updated: `test_dust_patch_fill` (an IR spot with nothing standing out loses
its core), `test_ir_dust` (Before may show the kept detail render).
