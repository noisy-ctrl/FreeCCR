# IR dust, revision 2: fewer false finds, whole fibres, a model that learns

The user, 2026-11-20, on his four Gold 200 120 frames (001 and 002 trichrome,
004 and 005 single white-light shots, each with an IR shot): "There's just
too many false positives and it's missing squiggly strands and more that look
apparent on the adjust IR scans. Would it not be possible to train a numpy
model or other?" Then, with two screenshots of frame 002 at Sensitivity 100
(409 specks flagged): "You can see it's missed black mark, white squigglies.
Although maybe the black mark is on the negative rather than dust."

## 1. What was measured

All numbers are on his four frames, inside the picture (blocks 260–3800 ×
140–2740 of the 4140 × 3104 block grid).

**The black mark and the purple smudges are not in the IR at all.** On the
negative they are lighter than the sky around them: the emulsion is thin or
stained there. Dust always prints white. IR can't see these marks, the same
as a dedicated scanner's IR cleaning. They need a hand heal or a
visible-light finder; they are not part of this revision.

**False finds counted without labels.** Dust only ever darkens the IR, while
noise and picture residue go both ways. So the same finder run on the IR
turned upside down (−ln IR) finds only false things, and how much it finds
estimates today's false finds:

| frame | 7σ (Sensitivity 50): found / upside-down | 4σ (Sensitivity 100) |
|---|---|---|
| 001 | 34 / 1 | 480 / 232 |
| 002 | 21 / 0 | 385 / 201 |
| 004 | 32 / 0 | 527 / 352 |
| 005 | 28 / 7 | 575 / 357 |

**The IR shot is very dark.** At 8 s and ISO 3200 it reached 6 % of the
sensor's range (median; the clear film 12 %). Photon transfer on the RAW
(variance vs level of same-colour neighbours in the sky) gives about 5.6
ADU per electron plus a read-noise floor of a third of the variance. That is
about 110 electrons per photosite, so the noise sits close to a thin fibre's
depth. A 30 s shot at the same ISO would make every speck ≈ 2.2× deeper
against the noise (photon noise ×√3.75, read noise unchanged). The
squiggles of frame 002 are 5.9–6.2σ deep today, under the default's 7σ.

**The picture's residue is local crosstalk, not misregistration or blur.**
Blurring the visible shot to match the IR before dividing it out made the
residue at edges WORSE at every blur (004: 0.0528 → 0.0603). A per-tile fit
of a sub-block shift found shifts under 0.01 block. A per-tile crosstalk
k (192-block tiles, pulled towards the frame's own k) removed most of it:

| frame | upside-down finds at 4.5σ, global k → local k | the line stage at 7: found / upside-down, global → local |
|---|---|---|
| 001 | 39 → 26 | 73/12 → 69/0 |
| 002 | 30 → 30 | 29/1 → 35/1 |
| 004 | 87 → 31 | 38/36 → 35/3 |
| 005 | 103 → 45 | 49/45 → 28/7 |

**Real dust darkens the frame's own shots too; noise doesn't.** On 185
candidates between 4.3σ and 7.5σ (48 per frame, spread over the colour
shots' darkening), each looked at by hand with the positive stretched to the
tile's own range, the colour shots' darkening and the IR side by side: 59 are
dust, 126 noise or picture. The median IR/visible depth ratio of clearly
found dust is 0.34–0.51: the IR dip is about a third of the visible one, so
a weak IR speck has a strong visible one. The best single feature is the
**shape agreement** (the normalised correlation of the IR spot with the
colour shots' darkening, best of ±1 block shifts): ≥ 0.4 on 77 % of dust,
21 % of noise.

A logistic model on eight features (§3.3), trained on three frames and tested
on the fourth, in turn (through the module's own code, `tools/fit_ir_dust_model.py`):

| held-out frame | dust found | false |
|---|---|---|
| 001 | 15 / 19 | 0 / 27 |
| 002 | 13 / 15 | 0 / 33 |
| 004 | 12 / 16 | 3 / 30 |
| 005 | 6 / 9 | 4 / 36 |
| all | **46 / 59** | **7 / 126** |

On the same band today flags all 185 at Sensitivity 100 and almost none at
50. Over whole frames (inside the picture), flagged / found on the
upside-down IR, as shipped:

| frame | rev. 2 at 0 | rev. 2 at 50 | rev. 2 at 100 | today 50 (7σ) | today 100 (4σ) |
|---|---|---|---|---|---|
| 001 | 77 / 2 | 110 / 6 | 171 / 51 | 34 / 1 | 480 / 232 |
| 002 | 54 / 1 | 66 / 3 | 114 / 47 | 21 / 0 | 385 / 201 |
| 004 | 55 / 5 | 115 / 25 | 210 / 73 | 32 / 0 | 527 / 352 |
| 005 | 38 / 0 | 82 / 6 | 193 / 47 | 28 / 7 | 575 / 357 |

Counts are candidates (a fibre's joined pieces count once each). 004's 25
at 50 are mostly the picture's top edge against the carrier, which a crop
leaves out. The squiggles of his screenshot (frame 002): nothing at today's
50, fragments and scattered false bits at 100; revision 2 at 50 covers the
curled one and most of the long one (its faint middle is missing: the IR
barely shows it). Map time 4.1–4.5 s a trichrome frame, 2.5–3.1 s a single
shot (it was ≈ 4 s).

**Fibres the IR hardly sees.** A bright chevron fibre on 002 darkens the
colour shots by 0.18 (log) but the IR by only 0.02 (ratio 0.11; dust is
usually 0.3–0.5): a translucent fibre scatters visible light more than
infrared. Only its end is found. A visible-led stage was tried (the colour
shots' sharp darkening as the footprint, the IR's dip added up over exactly
that footprint as the test): on 002 it added 2 finds the model hadn't made,
on 005 it added 18, all of them picture (lettering, ropes, edges: the IR's
residue under large picture details isn't noise). Dropped; these fibres need
a visible-light finder.

**The user's XP2 pair** (R0011, an IR shot at 0.17 % of its range): 59 flagged
at 50, 93 % of them darker than 0.1 (log, 4 × that residual's noise) in white
light. Its upside-down count (38) is NOT a false-find count there: that IR
shot has bright outliers (hot pixels at that exposure), which only the
upside-down side sees; revision 1 had 31 upside-down vs 13 found at 7σ.

**IR-only dots.** Compact IR dips at 5–6.3σ with nothing in the colour shots
(11 on 005) are not fixed sensor defects (none recur at the same place in
another frame's IR, chance 0.05 %) and not single bad photosites (the dip
covers 4 × 4 photosites). Whatever they are, nothing shows in the picture, so
there is nothing to heal: the model leaves them. The IR depth and line
features stop at 8 and 12 (the labelled band reached 7.5): beyond that a dip
with nothing in the colour shots gets no extra credit (on the Gold frames 82
of 84 dips deeper than 8σ have the colour shots' support).

**The repository the user linked** (Dust-Removal-For-Film-Negative-Scans) is
a notebook of classic processing (difference of Gaussians, a threshold and a
median filter on greyscale), no trained model and no licence. It is the
visible half of what this revision measures; its own listed weakness
(picture detail of similar size and brightness gets cleaned) is what the IR
answers. Nothing is taken from it.

## 2. Goals and non-goals

Goals:
- Far fewer false finds at the same or better coverage (§1's tables).
- Fibres found whole, as strokes that follow them, curls and loops included.
- A model that starts from the defaults fitted on his frames and learns from
  his corrections, per kind of scan, stored in his app data.
- Say when the IR shot is too dark to find faint dust, with the fix.

Non-goals:
- Marks that aren't in the IR (the black mark, the smudges) and fibres it
  hardly sees (§1): a visible-light finder is a later step.
- Changing Capture's IR exposure by itself: the hint says what to set.
- Any change to how a spot is healed.

## 3. The finder (`core/ir_dust.py`)

### 3.1 Local crosstalk

After the frame's own k (unchanged), each visible shot's k is fitted again
per 192 × 192-block tile on the high-passed logs (every 2nd block, the 1 % at
each end dropped), ridge-regularised towards the frame's k (λ 50, so a flat
tile says nothing), the tile coefficients smoothed (Gaussian 0.7 tile) and
interpolated bilinearly to every block: `ln IR −= Σ_c k_c(x, y) ln V_c`. No
shift terms (measured none).

### 3.2 Maps and candidates

- **z**: as today (closing background, local noise), on the locally
  de-pictured IR.
- **line**: z clipped to [−12, 4] (one deep speck mustn't make a line),
  averaged along 9-block segments at 12 turns, the deepest turn kept,
  normalised by its own median and MAD over the picture.
- **vz**: each visible shot's own residual by the same closing and local
  noise, averaged over the shots (one map; dust darkens them all).
- **Candidates**: seeds z < −4 or line < −6 inside the crop, grown into
  z < −2 or line < −3; components over 4000 blocks are picture.

### 3.3 Features and the model

Per candidate (component mask m):

| feature | definition |
|---|---|
| IR depth | −min z over m, at most 8 |
| line | −min line over m, at most 12 |
| visible dip | −mean vz over m minus −mean vz over the ring 3..8 blocks out |
| same shape | max over ±1 block shifts of the correlation of min(z, 0) and min(vz, 0) on a square of half-size max(7, half the box + 4) |
| IR/visible | the IR's log depth / the visible log depth over m, in 0..1.5, 0 when the visible dip < 0.5 |
| picture edge | ln(90th percentile of the colour shots' gradient in the ring / its median over the picture) |
| log area | ln of m's blocks |
| visible deepest | −min vz over m |

`p = 1 / (1 + exp(−(b + Σ w_i (x_i − μ_i) / s_i)))`. The defaults (μ, s, w, b)
are fitted by `tools/fit_ir_dust_model.py` on the 185 hand-checked candidates
(positions and labels in `tools/ir_dust_labels.json`; the RAWs stay on the
user's computer) through the module's own feature code, so the constants
match what ships.

**Sensitivity** 0..100 (default 50) is the probability needed:
`logit(p_needed) = 2.2 · (1 − Sensitivity/50)`, so 0 → 0.90, 50 → 0.50,
100 → 0.10.

### 3.4 Fibres whole

- **Joining**: an accepted candidate takes with it every line-like candidate
  (line ≥ 5 or length/width ≥ 3) with p ≥ 0.2 whose mask comes within 4
  blocks of it, and so on (a fibre's weaker pieces come with its strong
  ones).
- **Strokes along the fibre**: a component that may be long (spread
  length/width > 2, or ≥ 40 blocks) is thinned (Zhang–Suen); it is long when
  its skeleton is over 3.5 × its width (area / skeleton length), which a
  curl or a loop is even though its pixels' spread calls it round. The
  skeleton is split into simple paths at junctions (a diagonal step counts
  only where no straight one joins the two, so a staircase isn't a
  junction); each path (a loop walked round from any point) becomes one
  stroke spot, points every ≈ r blocks, r = width / 2 × 1.35 + 1. Today's
  slice-across-the-main-axis averaging put a curled fibre's points inside the
  curl, and the spread measure healed a loop as one disc over its inside
  (synthetic loop: the strokes cover 100 % of it and 0 % of its inside).

### 3.6 Bridges (2026-11-20, the user: "I see it not removing the full length")

His wavy line on 002 came out as its top and its bottom: the IR barely shows
its middle (13 blocks), where the colour shots still darken by z −3 to −4.
So after flagging, two line-like flagged pieces are joined by a stroke
(`bridges`, spots tagged `"ir_bridge"`, no features, nothing learned from
them) when:
- their nearest skeleton points are 2–24 blocks apart (`BRIDGE_GAP`);
- each runs on towards the other (the way out of the piece at that point
  within 60° of the way to the other; a dab agrees with anything);
- the cheapest path between them through the colour shots' darkening
  (`dark_path`, step cost × clip(1 + vz/4, 0.2, 2)) is at most 1.6 × the gap
  + 2 long, its mean vz below −2.5 and 80 % of it below −1.5;
- and it is a thin line: 70 % of its points are darker than both sides,
  max(3, r) blocks out across it, by 1.5 (`_ridge_share`). Without this,
  9 of 21 bridges ran along 004's frame edge against the carrier (dark on
  one side only); with it 2 remain there (between edge pieces a crop leaves
  out), and the rest are all along real fibres (checked by eye: 4 on 001,
  5 on 002, 0 on 005).
The stroke's radius is the smaller of the two pieces'. Measured cost:
≈ 0.1 s a frame.

### 3.5 The dark IR hint

`DustMap.ir_level` = the IR shot's 99.5th percentile over the white level.
Under 0.20 the Dust panel's IR line adds: "The IR shot is dark (N % of the
sensor's range): a longer IR exposure, such as 30 s, would show fainter
dust."

## 4. Learning from his corrections (`core/ir_dust_learn.py`)

- **Examples**, each (features, label, weight):
  - **Erase Spots** over flagged or healed IR spots: label 0, weight 1.
  - **Heal Them**: every flagged spot healed is label 1, weight 0.3 (he could
    have erased it, but may not have looked).
  - **A hand stroke** (kind "brush") over a candidate that wasn't flagged:
    label 1, weight 1 (one he had to find himself).
- **Per kind of scan**: "merged" (trichrome) and "single" (one visible shot)
  learn apart, because the colour shots' features differ (three shots
  averaged vs one grainy one).
- **Stored** in `<app data>/FreeCCR/ir_dust_learned.json` (APPDATA or
  ~/.config; `FREECCR_IR_DUST_LEARNED` overrides; `tests/conftest.py` points it
  at a temp file), at most 3000 examples per kind, oldest out.
- **Fit**: MAP logistic regression with a Gaussian prior centred on the
  defaults (precision equal to 60 examples), Newton's method, refitted when an
  example is added and only once there are ≥ 10 explicit (weight 1)
  corrections; otherwise the defaults.
- **Seen**: the Dust panel's IR line ends "Learned from N of your corrections
  on this kind of scan." The IR Dust heading's right-click has **Forget What
  It Learned…** (asks first), which empties that kind's examples.

## 5. Integration

- `DustMap` keeps the candidates (box, mask, features, line-likeness), not
  only z, so a new Sensitivity, a correction or a refit re-flags without
  re-reading anything. `spots_from(dm, sensitivity, keep, model=None)` keeps
  its signature (the model defaults to the learned one for the map's kind of
  scan); `mask_from` draws the flagged candidates' masks.
- **A spot carries its candidate's features** (`"ir_f"`, eight numbers to 3
  decimals, and `"ir_c"`, the candidate's number in that find), additive keys
  in the spot dict, so the catalogue keeps them (spots are deep-copied as
  they are) and a correction made after a restart still teaches. Strokes of
  one fibre share `ir_c`; one erase or heal counts each candidate once.
- `erase` / `heal` record examples through `ir_dust_learn` (the kind of scan
  from the frame: `merge_sources` → merged); the hand-stroke hook is the
  canvas's dust stroke end (`ImagePreview`), only while the frame has a dust
  map in memory.
- `DustMap.kind` = "merged" or "single" (how many visible shots), so a
  rethreshold uses the right learned model.
- Speed: measured per frame (it was ≈ 4 s); the line stage is 12 small
  filters, the colour shots' residual one closing per shot.

## 6. Tests (`tests/test_ir_dust_v2.py`)

- Local crosstalk: a synthetic frame whose crosstalk ramps 0.1 → 0.3 across
  it has fewer upside-down finds with the local fit than with the frame's k,
  and its specks are all still found.
- A speck in both shots is flagged at the default; an IR-only dip of the same
  depth isn't; a very deep IR-only one is.
- A faint curled fibre (a loop drawn at 5σ) is flagged as strokes covering
  ≥ 95 % of its pixels; its pieces are joined.
- Sensitivity: 0 ⊆ 50 ⊆ 100 finds.
- Learning: ten erasures of noise finds lower similar candidates below the
  threshold; Forget restores the defaults; the store goes to the test path and
  caps at 3000; Heal Them adds weight-0.3 positives.
- The dark IR hint appears under 20 % and not above.
- Bridges: a line the IR loses for 16 blocks in the middle is covered end to
  end (the pieces alone cover < 80 % of the gap) and the bridge carries no
  features; with no darkening in the colour shots there is no bridge; a
  path along a thin dark line is a ridge (≥ 0.9), along the dark side of an
  edge not (< 0.2).
- `tests/test_ir_dust.py` still passes (its synthetic scenes and the user's
  XP2 pair when present).

## Revision 2026-11-22: Sensitivity means the same whatever the IR exposure

**The fault.** The user's IR shots went from 8 s ISO 3200 to 30 s ISO 1000
(the panel's "dark IR" hint). The same light level (8.7–12.9 % of the range
against 9.1 %), but 1.85× less noise against the signal (photosite noise 48.9
against 78.6 levels at the same mean). The features are in noise units, so
the picture's leftovers after the crosstalk division (not noise-limited)
scored twice as deep, and the model fitted on 8 s shots flagged them: on one
frame of plain sky, Find + Heal Them healed 755 spots. The upside-down count
(§1) showed it: at the default Sensitivity the 8 s frames run 0.16–0.32
upside-down finds per real find, the 30 s frames 0.62–1.28. Adding noise back
to an 8 s level on the same 30 s frame (frame 004) halved both counts (258 /
161 → 130 / 72), so it is the cleaner shot, not the frames.

**The fix: a cleaner shot is read at the noise the model was fitted at.**
Real dust and the picture's leftovers are both fixed log dips; a cleaner
shot only divides them by a smaller noise, so every z grows by the same
factor and the model, fitted at 8 s noise (median local noise 0.0150–0.0162
log), takes far more of them. `dust_map` now scales z by
`noise / NOISE_REF` when the shot's median noise is below `NOISE_REF` =
0.015 (`ir_dust.NOISE_REF`), before the line map and the candidates; a shot
at or above it is read exactly as before (8 s shots: factor 1, unchanged).

Measured on the 30 s sky frame (R0023 001, noise 0.0071): candidates 1307 →
378, flagged at the default 233 → 100, and all 15 of the deepest IR
candidates still flagged; its upside-down count at the default (65) is now
within the 8 s frames' (39–90). A per-frame threshold set from the
upside-down run was tried first and dropped: the false-find share on this
frame stays near 0.55 over a wide range of thresholds, so the threshold
jumped between 0.21 and 0.996 as the Sensitivity moved (45 → 367 finds).

Tests (`tests/test_ir_dust_v2.py`): a scene and its twice-as-clean twin: the
clean twin has no more candidates than the noisy one (27 without the
reference against 20), flags at most one more, and flags every planted speck
the noisy twin flags; a shot at the reference noise gives exactly the same z
with and without it. The older "dust in both shots" test pins `NOISE_REF` to
0, as it checks the model at its scene's own (cleaner) noise.

## Revision 2026-11-22 b: a found speck is healed whole

The user's sky frame (R0023 005, Find + Heal at the defaults) left a cyan
hook beside a healed dot. The IR saw the speck's top only (part of it is
translucent in infrared); the colour shots showed it all. The dab was sized
from the IR's piece, so the rest stayed, and the heal's source could even
come from it.

**Change** (`with_visible`, `_spots_of(..., vz=)`, `spots_from` passes
`DustMap.vz`): before a find is made into a DAB, it grows over the
connected region the colour shots show darker than −`VIS_REACH_Z` (3) z
that touches it, if that region is larger than the find and no more than
`VIS_REACH_MAX` (3) times its size across (9× its area: a find in front
of a dark part of the picture is left alone). The grown find is then made
into spots afresh, so a hook becomes strokes along its skeleton. Finds
already made into strokes (fibres, loops) and the bridges are untouched:
growing those changed what the IR followed and broke the loop and bridge
tests. Marks (`film_marks`) call `_spots_of` without `vz` and are
unchanged. Nothing changes what is flagged, only how much a flagged find
covers.

**Measured** on that frame: of 118 finds flagged at 50, 27 grew (median
2.1× the area, largest 4.7×); the hook and the white blob's speck are
fully healed at a 2000 px and a 6000 px render. Test:
`test_a_speck_the_ir_sees_only_part_of_is_healed_whole` (fails with the
growth turned off) and `test_with_visible_leaves_a_find_alone_when_nothing_more_shows`.
