# Marks learn from your corrections

The user, 2026-11-22 ("Marks finder learning … < yes please"). The colour
shots' marks (`spec/colour-shot-marks.md`) are found by a fixed rule; IR dust
already learns from the Eraser, Paint Over and hand strokes
(`spec/ir-dust-v2.md` §4). Marks' corrections have been stored since the
marks shipped (`marks-light` / `marks-dark` in `ir_dust_learn`), but nothing
read them. This makes them count.

## Goals and non-goals

- Until a kind of mark has 10 corrections the rule stands, byte for byte:
  nothing changes for anyone who hasn't corrected marks.
- After that, which marks are flagged follows a model fitted to his
  corrections, starting from (and pulled towards) the rule.
- A hand stroke over a mark the finder saw but didn't flag counts as a
  missed mark, as it does for IR dust.
- Forget What It Learned… forgets the marks too.
- Non-goal: no change to what a mark candidate is (the seeds, growth and
  features), or to the structural tests that aren't a matter of degree.

## The model (`core/marks_model.py`, defaults `core/marks_default.py`)

- **Features** (model units, from a mark's stored `mk_f`): log depth, log
  stands-out, ring step, log area, log elongation, IR support, colour.
- **The gate stays a rule** (`film_marks.gate`): ring step ≤ `STEP_MAX`, area
  ≤ `AREA_MAX`; prints-dark: span ≤ `MAX_LENGTH`, IR support ≤
  `LIGHT_IR_MAX`; prints-white with an IR shot: IR support ≥ `DARK_IR_MIN`.
  Those say what a mark is not; the model decides among the rest.
- **The default** per kind (light / dark) is a logistic fit to the rule's
  own verdicts at Sensitivity 50 on every gated candidate in his frames
  (`tools/fit_marks_model.py`, writing `core/marks_default.py`). It is the
  prior, never used alone (below 10 corrections the rule itself is used).
- **Sensitivity** keeps the rule's meaning: a mark is flagged when its
  logit z ≥ corner(s) − corner(50), corner(s) = the DEFAULT model's logit at
  the rule's thresholds for s (`thresholds(s)`: depth and stands-out), the
  other features at their means. So moving the slider shifts the line as
  far as the rule's thresholds move.
- **Learning**: `ir_dust_learn.model_for("marks-light"|"marks-dark")` =
  None below `MIN_EXPLICIT` (10) weight-1 corrections; else MAP logistic
  (`fit_map`, prior worth `MARK_PRIOR_N` 20 examples, not IR's 60: the
  rule's fit is far sharper than IR's defaults, about 5 logit per standard
  deviation of stands-out, so the same prior held it much harder) in the
  default's standardised units on the transformed features. Examples as before: the
  Eraser over a mark = not a mark (weight 1), Paint Over = a mark (0.3), and
  now a Brush stroke over an unflagged candidate = a missed mark (1,
  `film_marks.learn_from_stroke`, while the frame's mark map is in memory).

## Measured (his 9 frames: 5 Gold 200 trichrome / B&W with IR, 4 XP2)

8,036 candidates; gated 2,430 prints-dark and 1,508 prints-white. The
default against the rule, counts flagged (both = the same candidates):

| s | prints-dark rule / model / both | prints-white rule / model / both |
|---|---|---|
| 0 | 23 / 21 / 18 | 39 / 34 / 34 |
| 50 | 46 / 44 / 44 | 77 / 74 / 72 |
| 100 | 90 / 85 / 76 | 156 / 152 / 145 |

(the tool prints these and writes them into `marks_default.py`'s
docstring.)

How fast it learns (prior 20, measured on the fitted defaults): ten "not a
mark" corrections on a just-flagged prints-white mark (depth 11, stands out
8.5) take its logit from +0.42 to −2.0, while a strong one (40 / 30) stays
at +13.9. A prints-dark mark well under the rule's line (6.5 / 5, logit
−7.5) needs about twenty missed-mark strokes to be flagged (−0.36 at ten,
+0.45 at twenty), and a faint one (2.5 / 1.5) stays far out (−16.7).
With IR's prior of 60 the same twenty strokes left it at −0.96.

`fit_map` gained a line search (Newton's step halved while the objective
rises): from a model sure of the opposite, the full step overshot onto a
flat part and stalled, so twenty corrections moved it LESS than ten
(−7.45 vs −2.18). Where the old loop converged the result is the same.

## Integration

- `film_marks.passes(mk, s, has_ir)` = gate and (model z ≥ shift if a
  learned model exists for the kind, else the rule's thresholds).
- `ImagePreview` calls `film_marks.learn_from_stroke` beside IR's.
- The Dust panel's learned line counts marks' corrections too; Forget What
  It Learned… forgets the scan kind's IR examples and both marks kinds, then
  re-flags the frame's marks.
- `ir_dust_learn.fit_map` takes any number of features (it assumed 8).

## Tests (`tests/test_marks_learning.py`)

- Below 10 corrections `passes` is the rule exactly on random marks.
- The default is the rule at s = 50 on most candidates (a synthetic set).
- 10+ "not a mark" corrections on deep marks stop them being flagged; 10+
  missed marks get shallow ones flagged; the prior keeps far-away marks as
  the rule had them.
- A brush stroke over an unflagged candidate records one example of its
  kind; over a flagged one, none.
- Forget clears the marks kinds.
- `fit_map` with 7 features.
