# Marks the IR can't see, found in the colour shots

The user, 2026-11-20, after IR dust revision 2 (`spec/ir-dust-v2.md`): "A
finder that works from the colour shots, for marks the IR can't see".
Revision 2 measured what the IR misses on his four Gold 200 frames: his black
mark and the purple smudges (not in the IR at all: lighter on the negative,
so in the film, not on it), fibres the IR barely shows (a bright chevron on
002: colour shots 0.18 log, IR 0.02) and specks too small for the noisy IR.

Draft signed off 2026-11-20 (draft_marks_001/002/004/005.jpg, the marks this
finder flags beyond the IR's): "With the IR dust" (outlined in a second
colour, its own Sensitivity, Heal Them heals both), "Yes, on every frame"
(also frames with no IR shot). On 005's white-printing marks: "22 is
probably text. Same for 24, 26, 27. 23 + 25 may also be part of the scene
but I can't tell"; 005's black specks on the deck and 004's white specks:
not sure.

## 1. What a mark is

Two kinds, both read on the RAW's 2 × 2 blocks (camera-native, linear) as the
residual against the local background in log, divided by the local noise
(grain) — `signed_residual`: like IR dust's z, but the background is the
mean of an open-close and a close-open (neither bright nor dark specks move
it). IR dust's upper envelope, blurred, put a dark ring round every bright
mark, which hid the mark itself behind the halo rule (measured on a
synthetic mark: found only as its "dark" ring):

- **prints dark** (`"light"` on the negative): emulsion damage, a pinhole, a
  lost dye. Read as the deepest of the shots (a mark may have lost one dye).
- **prints white** (`"dark"` on the negative): dust and fibres the IR
  doesn't show. Read as the mean of the shots (dust is grey). Where the IR
  finder has flagged a speck (dilated 3 blocks) none is looked for.

A mark **stands out** from where it lies; the picture doesn't: the steepest
edge on the mark is far steeper than anything in the ring 3..8 blocks round
it (the taking lens softens the picture; a mark on the film is as sharp as
the scan), and the ring is even (a picture's edge is dark on one side).

## 2. The finder (`core/film_marks.py`)

- Seeds deeper than 7σ, grown into 3σ; components ≤ 1500 blocks, whole
  ring inside the frame.
- **The IR's own**: a prints-white mark with ≥ 30 % of it within 3 blocks of
  IR-flagged dust is left to the IR finder (its fibres came out twice).
- **Halos**: a dust speck's bright ring on the negative isn't a mark: no
  prints-dark mark is looked for within 4 blocks of IR-flagged dust or a
  prints-white mark deeper than 7σ (on 002 two of six prints-dark finds were
  halos before this).
- **Features** (`FEATURES`): depth (−min z), stands out (steepest gradient on
  the mark dilated 1 / 90th percentile of the ring's), ring step (the
  ring's two halves across the mark's long axis, |median difference| as a
  share of the mark's own contrast: an edge's two sides differ by about as
  much as the "mark" along it stands out; by the grain's noise it failed on
  any gentle gradient), log area, elongation, IR support (the IR's z added up over
  the mark, / √(area × 12.6), + = darker in the IR; 0 with no IR shot),
  colour (std of the three shots' mean z / (|their mean| + 1); 0 on one
  shot).
- **The test** (a rule, `flag`; not yet a fitted model):
  - depth ≥ D(s), stands out ≥ S(s), ring step ≤ 0.5, area ≤ 800 blocks, with
    Sensitivity s: D = 11 → 8 → 6 and S = 9 → 7 → 5.5 at 0 → 50 → 100
    (linear either side of 50);
  - prints dark: at most 60 blocks long (a longer dark line is the
    picture's: a wire, a window frame) and not dark in the IR (IR support
    ≤ 2.5: that is dust's halo);
  - prints white, with an IR shot: IR support ≥ 1.8. Measured: his fibres
    the IR finder missed 2.0–16.3; 005's text 22/24/26 (his call) −0.7 to
    1.6. With no IR shot this test isn't there, so lettering can be flagged
    (Erase it).
- **Spots**: kind `"mark"`, made like IR dust's (`ir_dust._spots_of`: a dab,
  or strokes along the skeleton), carrying `"mk_f"` (the features) and
  `"mk_k"` (light / dark). The heal treats `"mark"` like `"auto"`.

Measured at Sensitivity 50, inside the picture, beyond the IR's finds, as
shipped: 001 4 prints-dark (brown and black dots on the sky) + 3
prints-white (fibres); 002 5 + 6 (his brown mark, three brown dashes and a
purple one; the chevron's arm, the "5"-shaped fibre, specks); 004 6 + 11
(four vertical black dashes, a lamp; white specks on dark walls); 005 23 + 1
(the draft's 6 before the IR test, which took out the lettering he named).
2–3 s a frame.

## 3. In the Dust panel

The IR Dust section becomes **Dust and Marks**:
- **Find** (was Find Again) looks with the IR shot when there is one, and in
  the colour shots always (frames from RAW files; a TIFF or JPEG frame has
  no RAW shots to read and says so).
- **Sensitivity** stays the IR's; **Marks** under it is the colour shots'
  (0..100, default 50, `img.mark_sens`).
- Flagged marks are pending like IR dust (`img.ir_dust_pending`, kind
  `"mark"`), outlined in **blue** (IR dust stays amber); the status line
  counts both ("The IR shot shows N specks (amber) and the colour shots M
  marks (blue), not healed yet."); **Heal Them** heals both, replacing
  earlier IR and mark spots; Before and Erase Spots work on both; the red
  dot on the Dust removal icon counts both.
- A Marks change re-flags from the map in memory, like Sensitivity.

## 4. Learning

Not in this version. Erasing or healing a mark records an example (its
features, kind `marks-light` / `marks-dark`) in the same store as IR dust's
(`ir_dust_learn`), so a fitted model can learn from his corrections later;
nothing reads them yet.

## 5. Tests (`tests/test_film_marks.py`)

- A sharp dark spot on a smooth negative prints dark and is found; the same
  spot blurred to the picture's softness isn't; a step edge isn't (ring
  step).
- A dust speck's halo doesn't make a prints-dark mark.
- Prints white with an IR shot: a mark the IR doesn't darken at all isn't
  flagged, one it darkens a little (IR support ≥ 1.8) is.
- Sensitivity nests (0 ⊆ 50 ⊆ 100).
- Spots are kind "mark" with features; heal replaces earlier IR and mark
  spots and keeps hand ones; erase removes marks and records an example.
- `find_all` on a frame with no IR shot finds marks only; with one, both.
- The panel: Marks slider re-flags; the status counts both; the overlay
  draws marks blue.
