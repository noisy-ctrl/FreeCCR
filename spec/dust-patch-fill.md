# Dust heal: replace only the speck, rebuilt from many small patches

The user, 2026-11-23, after the ring-match source search (spec/dust-heal-ring-match.md):
"still circular artefacts. Still strange circular selections over little lines",
"it's not really a functioning tool yet", and, showing Affinity Photo's inpainting
brush taking a hair off the same frame invisibly: "Affinity just nails it".

## What was wrong (measured on his frame R0025_35mm_Colorplus_004, 12 IR finds)

1. **The whole area was replaced.** An IR find is a dab sized from the IR's blob
   (`ir_dust._spots_of`: area-equivalent radius × 1.35 + 1 block), and the IR shot
   is out of focus against the colour shots, so a 3 px hair across a wire's edge
   got a dab 30 px across (spot 1). Everything under the dab went.
2. **It was filled from one place.** One source disc per segment, picked at the
   preview's size and replayed at the export's: across an edge there is rarely a
   place where the same edge lies at the same offset, so the disc showed (spots
   1, 8) and a hair's segments stair-stepped (spot 4).

Five of the twelve heals were visible discs or blocks at half size; the rest sat on
flat sky or shadow.

## The heal (core/dust_fill.py)

Decided on the buffer being rendered, at its own resolution; nothing is planned at
the preview's size and replayed.

### 1. The speck's own pixels (`speck_mask`)
- Luminance on the heal's own scale (sqrt of de-windowed display values).
- Morphological top-hats (opening for a speck brighter than the picture round it,
  closing for a darker one) at sizes 5 … 55 px, smoothed 0.8 px.
- Each size is measured against **what that size finds in the grain** round the
  area (a 4 px ring outside it: median and MAD of the same top-hat), because a
  larger top-hat gathers more grain. Size 3 is not used: it measures single grains
  (clean grain reached z 6 there).
- The speck's size: the smallest size at which something stands out (z ≥ 7) and
  whose response at that point already holds 80 % of what a size two steps up finds
  there. Larger sizes go on gathering the picture round the speck (a speck on a
  bright out-of-focus blob).
- Seeds z > 7 inside the area (clean grain: p99 5.7, max 6.6 over 120 areas; his
  specks: 12–27, two faint IR finds 5.4 and 6.5); grown over what joins them above
  max(2, 0.25 × the peak) inside the area and max(3, 0.35 × the peak) up to 4 × the
  size outside it (a hair longer than the stroke over it). A run-on bigger than the
  area itself is picture joined to the speck and is dropped. Widened by 0.2 × the
  size (its soft edge).
- Polarity: IR and AI finds and "prints white" marks are brighter, "prints dark"
  marks darker, a brush stroke either; the unexpected one wins only 1.5 × stronger.

**What is replaced:**
- a finder's spot (`ir`, `auto`, `mark`): the speck only; nothing standing out →
  the whole area;
- a painted spot (`brush`): what was painted, plus the speck where it runs on
  (as with any inpainting brush; "Fit each click to the speck" sizes a click to a
  soft speck's halo, and tests/test_dust_fit.py's measured case still holds);
- any spot ≤ 4.5 px wide at the size rendered (the preview): the area, untraced
  (tracing at that size took the picture round the speck too).

### 2. The fill
- **A hole wider than 5 px** (inscribed radius > 2.5): PatchMatch completion
  (Barnes et al. 2009; Wexler et al. 2007), 7 × 7 patches, coarse to fine (halving
  while the hole is thicker than 3 px and the box at least 4 patches wide). Each
  level: propagation from the 4 neighbours both ways + random search with halving
  radius, then voting (patches weighted exp(−d / 2σ), σ = the 75th percentile
  distance). The coarsest level starts from a diffusion fill. In the patch distance
  a hole pixel's current guess counts 0.25 against 1 for the picture (a guess pulls
  matches towards itself: a hole half a patch wide drifted pink on his spot 4).
  On the finest level's last iteration every hole pixel is **copied** from the
  centre of its own patch's match, never averaged, so its grain is real grain; the
  pixels copied are the buffer's own 16-bit codes (headroom above white kept).
- **Seam**: along the hole's edge, the picture just outside is compared with what
  lies just outside each copied pixel's source (both smoothed 1.5 px); the
  differences, smoothed along the edge, are carried into the hole by diffusion and
  added (the healing brush's idea). Synthetic gradient + edge, disc r 10: error
  0.0175 → 0.0114 (noise floor 0.0100).
- **A hole ≤ 5 px wide** (a speck at the preview's size, a thin hair): diffusion
  (Telea, per channel at 16 bits) plus the grain of the same shape moved just clear
  of the hole, in the direction where the picture looks most like the fill.
- Sources: never in any hole, never outside the crop, from a box padded by
  clip(2 × the hole's short side + 28, 28, 160) px. Seeded from the hole's place, so
  the same render heals the same way.

### 3. In the app
- `ccr_processor.DUST_ENGINE` = `"patch"` (default) or `"clone"` (the earlier
  engine, kept; `FREECCR_DUST_ENGINE=clone`). `apply_dust_removal` → `_patch_heal`
  (crop → keep mask, windowed base → de-window for the distances).
- `CCRImage._patch_heal_cached`: the heal remembered by the buffer's content
  (blake2b of a sparse sample), the spots, the crop and the windowing; a hit puts
  the changed pixels back. 4 renders per image (preview, zoom, export …). A slider
  move re-renders the same base, so the heal costs nothing after the first time.
- The Dust panel hides **Feather** (nothing is faded: the fill meets the picture)
  and **Show heal sources** with its hint (no one place a heal copies from); the old
  engine's plan cache is set empty so the source overlays draw nothing.
- Paint Over applies a Dust / Marks slider move still waiting on its timer first
  (it re-flagged the finds just painted over).

## Measured

His frame, 12 IR finds (sheets in the PR / the conversation):

| size | time | visible discs or blocks |
|---|---|---|
| preview 1080 | 0.16 s | none at fit size |
| half 4140 | 1.2 s | old: 5 of 12; new: 0 (a 1 px tail of spot 4's hair, past the IR's stroke) |
| full 8280 | 3.8 s | 0 |

Synthetic bench (scratch: 8 lab scans at 1080, 320 dust specks and hairs on lines,
edges, flat and texture; error against the clean picture inside each spot's area):

| | mean error | wrong structure | fine detail vs clean |
|---|---|---|---|
| old touching rule | 36.0 | 51 % | 1.10 |
| ring-match (unmerged) | 21.2 | 33 % | 1.02 |
| **patch fill** | **17.4** | **27.7 %** | **0.97** |

## Not done
- The IR finder's spot geometry is unchanged; the heal now traces the speck inside
  it, which is what matters for the picture.
- A user-picked source (dragging the source ring) has no meaning for a fill made
  of many patches; with the patch fill those controls are hidden.

## Tests (tests/test_dust_patch_fill.py)
No-op and untouched pixels outside the holes; a hair across a step edge leaves the
edge (smoothed error < 9 levels, mean < 1.5); grain inside a filled speck 0.7–1.3 ×
its surroundings and the speck gone (< 4 levels); a found spot replaces < 25 % of a
dab 6 × the speck, a painted one > 90 %; nothing standing out → whole area; nothing
sampled outside the crop; a windowed base heals in its own codes above white;
deterministic; mono; a preview-size hair diffused with grain; a hair followed 15 px
past its stroke; the cache; the clone engine still selectable; the panel. The old
engine's tests pin `DUST_ENGINE = "clone"` (autouse fixture).
