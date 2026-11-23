# The heal's source: the nearby patch whose surroundings match

The user, 2026-11-23, after the circles fix: "I also think it's where it's
sampling from. Where dust is on a straight horizontal object, the tool often
samples from outside that line. It needs a fair bit more work. It looks like
it's creating artefacts most of the time. Or creating objects where there
aren't any. It also looks too crisp."

## Cause

The automatic source was chosen among placements TOUCHING the spot by
"Δhue + Δsat + ΔV against the darker half of the hole, plus the lowest
texture". Nothing looked at the structure round the spot. On a wire or an
edge the plainest touching patch is the sky or wall beside it, so the line
broke and a disc of whatever touched the spot was stamped in.

## Bench (`/home/claude/scratch/heal`, not shipped)

Eight of the user's lab scans at preview size (1080 px), 40 dust specks and
fibres each (specks 1–3 px core with a soft halo, fibres 14–40 px, brightness
of film dust), put on detected lines, edges, plain sky and texture (never on
the black film border). Brushes as Fit-to-speck makes them. Per spot: the
mean error against the clean scan inside the brush (8-bit levels), the share
of brushed pixels more than 12 levels off after a 3×3 blur ("wrong
structure"), and the fine detail inside against the clean picture's
(3×3 high-pass std; 1 = right).

| | error | wrong structure | fine detail |
|---|---|---|---|
| Old touching rule | 36.0 | 51 % | 1.10 |
| Prototype | 19.7 | 33 % | 1.00 |
| As built | 21.2 | 33 % | 1.02 |

Lines 47 → 26, edges 50 → 22. At 2160 px replaying the preview's plan: 19.5
(preview 18.3). Tried and dropped: a smooth fill plus synthetic grain (more
error, wrong grain), the median of the best 3–5 sources (−8 % error but
detail 0.8: soft), a fine-detail penalty (worse on lines: a line through a
spot has more detail than its surroundings), a wider search (no better,
slower).

## The search (`_heal_patch`, the fresh pick)

- Candidates: every placement within `reach` = clamp(4 × the piece's size
  (its bbox's long side, or twice its half-thickness), 12, 160 × max(1,
  half_th/4)) px whose sample area is clean (no dust, nothing outside the
  crop: `mask_pad`) and whose own ring is at least 75 % clean.
- Score: the masked SSD between the spot's ring and the candidate's ring,
  on sqrt(display value) (de-windowed on a windowed base), × (1 + 0.15 ×
  distance / reach), + 0.5 × the masked SSD between the candidate's inside
  and a smooth (Telea) fill of the dust, both blurred σ 1.5 — copying an
  object into plain picture costs (the smooth fill is made once per heal,
  only if a piece searches).
- The clone and its tone membrane are unchanged; the outward feather
  (spec/dust-feather-outward.md) blends it.
- Each piece of a long stroke picks its own source (fibres 19.4 → 18.8
  against one shared offset).
- No clean placement within reach → the diffusion fill, as before.

## Plans

Plan records gain a 7th field, the rule (`_HEAL_RULE` = 2), kept by the
catalogue. An AUTOMATIC record without it (the touching rule's) is searched
again at the preview's scale; a cross-scale replay of a plan holding any
such record plans afresh at the preview's scale first. A user's own re-pick
(`manual`) is always kept. Pins and the per-patch offset are honoured
within 1.5 × reach + 2 px (was: touching).

## Tests

`tests/test_dust_removal.py`: strokes, two overlapping dabs and stripes sample
within reach; a prior plan's pin carrying the rule is reused verbatim, an
untagged automatic one is searched again, a manual one is kept;
`tests/test_dust_crop_display.py`: a moved stroke re-picks near its new place.
