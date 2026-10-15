# White point: the brightest near-grey area, and a choice per frame

Follows `spec/auto-white-point.md`.

## 1. Problem

"The white point picking the brightest point sometimes contributes to a great
image, sometimes makes the starting point bad."

The auto white point makes the frame's brightest area neutral. That is right
when the brightest area is meant to be white (sunlit stone, a white wall, an
overcast sky) and wrong when it isn't (blue sky, sunlit foliage, skin,
blossom, a lamp). The roll guard protects warm/cool but removes green/magenta
up to 0.15, so a frame whose brightest area is foliage or flowers still goes
wrong. And when it does go wrong there is no quick way to see why or to
choose otherwise for that frame: a hand-sampled White Point applies to the
whole roll (scaled per frame), which is how one frame's sample put a magenta
cast on another.

## 2. Goals / non-goals

1. Pick the brightest **near-grey** area, the way a person sampling by hand
   picks the white stone and not the sky.
2. Show the area that was used, and let each frame choose its white point:
   this frame's highlight, the roll's average, off, or one sampled by hand on
   that frame. A hand-sampled White Point applies to the frame it was sampled
   on only.

Non-goals: changing the black point, Auto Gain or Auto tone; tethered
capture (it keeps converting with the session's black point and the default
slope).

## 3. Picking the brightest near-grey area (`core/auto_white_point.py`)

On the unconverted preview, inside the picture area, with the frame's black:

- Density `D` per pixel, averaged into blocks of about 1/60 of the picture's
  short side (≈ 10 px at 1080), so each candidate is an *area*, like a
  marquee, and grain is averaged out.
- Candidates: blocks in the brightest 10 % (by mean density over the
  channels), the top 0.2 % excluded (glints), and only even blocks (the
  spread of density inside the block in the lower half of the candidates —
  an edge between a bright and a dark object is a mix of two colours).
- Each candidate's colour is its ratio `D / mean(D)` compared with the
  roll's neutral (`reference`), as temperature and tint, and its distance is
  `hypot(temp, tint)`.
- The most neutral quarter of the candidates (at least 8 blocks) whose
  distance is within 0.10 of the roll's neutral is the frame's near-grey area.
  Its median density ratio is the white point's colour; the white point's
  *level* stays the brightest area's (unchanged from before, so tone and
  Auto Gain behave as they did).
- No such area (every bright area is coloured relative to the roll: a
  sunset, or a frame with its own cast) → the previous rule: the brightest
  area with the roll guard (warm/cool ≤ 0.06, green/magenta ≤ 0.15). That
  keeps the cases the guard was built for.
- No roll (fewer than 3 other frames with an estimate) → the previous rule
  against equal channels.

The roll's neutral is the median colour of the other frames' estimates, as
before; a frame converted with a near-grey area contributes that area's
colour rather than its brightest area's. (A two-pass "refined" neutral was
tried and dropped: on a roll where most frames' brightest areas are sky it
picks sky-like areas as neutral all over again, so it cannot undo the bias it
was meant to.)

The blocks used are kept as up to 8 outline rectangles (connected blocks
merged), normalised to the raw preview.

## 4. The choice per frame (UI)

A row under the anchor line in Film B/W Point:

    White Point  [Frame highlight ▾]  [Show]

| Choice | What Convert uses |
|---|---|
| Frame highlight | §3 (the default when Settings → Auto white point is on) |
| Roll average | the roll's neutral colour at the frame's own level |
| Off | no white point: the film stock or the default slope (the default when the setting is off) |
| Sampled by hand | the White Point sampled on this frame (listed only once there is one) |

- Changing it on a converted frame re-converts that frame straight away.
- It is remembered per frame (`img.wp_choice`, catalog) and Convert All
  honours it; a frame never set follows the Settings default.
- **Show** outlines, for a few seconds, the area the white point came from
  (the near-grey blocks, or the hand-sampled rectangle). Convert Current
  flashes it on its own, after the black point strip.
- A film stock still wins over "Frame highlight" and "Roll average"
  (as before: auto only replaces the default slope); "Sampled by hand" wins
  over a film stock, because it is this frame's own measurement.

### Set White Point

- Samples onto **the current frame only** (`img.wp_sample`, its rectangle in
  `img.wp_sample_rect`), selects "Sampled by hand" and converts the frame
  straight away. It no longer sets a roll-wide white point.
- The roll-wide white point is retired: it is no longer restored at start
  (the old `convert/white_point_bgr` setting is removed), so a sample from an
  old session can't override every frame. The backend still accepts one from
  code, and then behaves as before.
- ✕ clears the black point and this frame's hand sample (the frame goes back
  to the default choice).
- Save film stock (needs both points) uses this frame's hand sample with the
  black point the frame was converted with.

## 5. Data

- `img.wp_choice`: None (follow the setting), "frame", "roll", "off",
  "sampled"; `img.wp_sample`, `img.wp_sample_rect`. Saved in the catalog.
- `conversion_inputs["auto_wp"]` gains `choice`, `neutral` (the near-grey
  ratio, or None) and `areas` (outline rects); `dmax` is still the brightest
  area (other frames' roll estimates read it alongside `neutral`).
- The white point used still goes into `["bw"]`; replay is unchanged.

## 6. Tests

- A frame whose brightest area is green foliage and which has a grey wall a
  little darker: the near-grey pick is the wall (the old rule made the
  foliage white); the outline rectangles cover the wall.
- A sunset frame (every bright area warm against the roll): falls back to the
  guarded brightest area.
- No roll: the previous behaviour.
- The roll's neutral prefers a frame's recorded near-grey colour to its
  brightest area.
- Choices: roll / off / sampled each give the expected white (or none); a
  film stock beats frame/roll but not sampled; Convert All honours per-frame
  choices; the catalog round-trips them.
- UI: the combo follows the current frame; changing it on a converted frame
  re-converts; "Sampled by hand" appears after sampling; Set White Point
  leaves the backend's roll-wide white point unset and converts the frame;
  the persisted roll-wide white point is dropped at start; ✕ clears the
  frame's sample.
