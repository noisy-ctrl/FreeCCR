# Auto black point, always on

Follows `spec/auto-black-point.md`, which added per-frame film-base
measurement behind an "Auto black point (per frame)" tickbox.

## 1. Goal

Measuring each frame's base works on every roll it has been used on, and
it already falls back when it can't, so the tickbox is a switch nobody needs to
turn off. Remove it: Convert always measures each frame.

Non-goals: changing detection, or the fallback order.

## 2. UX

- The tickbox is gone from the Film B/W Point section, and its QSettings key
  `convert/auto_black_point` is removed on start.
- The mode line always reads "Anchor source: auto black point per frame, …"
  followed by the slope source (default slope, film stock — name, or a
  sampled white point scaled per frame). When a Black Point has been sampled
  its tooltip adds that it is the fallback for frames with no measurable
  border, since that is now the only thing a sampled black point does (not in
  the line itself: it elides from the right).
- Set Black Point stays: it is the fallback for a frame whose border can't be
  measured (the order is unchanged: this frame's border → the sampled black
  point → the roll's consensus).

## 3. A frame with nothing to anchor to

Before, with the tickbox on, such a frame was refused (Convert Current) or
left out (Convert All). With the tickbox off it got the fixed-constant density
invert (`spec/no-anchor-convert.md`) after a warning. Now that auto is the
only mode, both buttons keep that conversion reachable:

- **Convert Current**: the frame is measured first. Only if there is no
  border and no sampled black point does it show the "Convert without a black
  point?" warning, now leading with the reason ("No film base found on this
  frame: no clear border found (…)"). Convert Anyway runs the fixed density
  invert; Cancel leaves the frame and says to set a Black Point by hand. The
  Settings switch that turns the warning off still applies.
- **Convert All**: no up-front warning (which frames lack a border isn't known
  until they are measured). A frame with no border, no sampled black point and
  no roll consensus is converted unanchored (`bw = (None, None)`,
  `auto_bp.source = "none"`) and counted in the summary: "N had no film base
  to measure (fixed density invert)". The roll consensus exists whenever any
  frame on the roll measured a base, so in practice this only happens when no
  frame shows a border at all — which then converts consistently, all
  unanchored.

### 3.1 Convert Current gets a roll fallback too

Convert All falls back to the roll's consensus base; Convert Current had
none. It now uses the median base of the other frames that were converted
with their own measured border (`recorded_roll_black`, at least 2), so a
frame with no border converts against the roll before asking about an
unanchored conversion.

### 3.2 A frame scanned at a different exposure keeps its own base

`roll_check` flags a frame whose base differs from the roll's in every
channel the same way, as light round the film. A frame scanned brighter or
darker than the others looks the same, and forcing the roll's base on it put
its picture below black (a night frame scanned at twice the exposure: 84 % of
its picture under black, flat). The film base is the clearest thing on the
film, so a base the picture is clearer than cannot be this frame's base. With
the raws passed in, a frame keeps its own base when more than 1 % of its
middle is clearer than the roll's base but not more than 1 % clearer than its
own (`clearer_share`, 3 % grain margin); light round the film reads brighter
than the picture allows, so it is still flagged.

## 4. Data / integration

- `ccr_backend.auto_black_point` defaults to `True` and is set `True` by the
  panel. It stays an attribute so scripts and tests can still run the
  single-anchor loop.
- The batch summary gains `unanchored`.
- No change to `conversion_inputs` replay: `(None, None)` is already the
  no-anchor recipe.

## 5. Tests

- The panel has no tickbox; the backend flag is on; the old setting is gone;
  the label names auto and, with a sampled black point, its tooltip names the
  fallback. The film-stock label tests expect the auto wording.
- Convert All converts a frame with nothing to anchor to unanchored (no film
  stock slopes either) and counts it.
- The confirmation leads with the reason.
- The single-anchor label tests run with the flag off from code.
