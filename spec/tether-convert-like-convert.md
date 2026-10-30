# Tethered captures convert like Convert Current

Status: built 2026-10-30. Revises `spec/camera-tethering.md` §5.4 (the worker's
conversion) and §9.1.3 (the inherited look).

## Problem
"When tethered, auto converts come out weird where clicking convert fixes it."

The tether worker converted each capture with its own short path: the roll's
sampled black point, the sampled white point (or default slope plus film-stock
slopes), nothing else. Convert Current had since grown a fuller pipeline:

- the frame's own film base (auto black point, with the roll's median as the
  fallback);
- the frame's own white point (auto white point, guarded by the roll's
  highlights) when none was sampled;
- the picture area, recorded so zoom and export measure the same region;
- auto tone;
- the after-conversion auto white balance.

So a capture and the same frame after pressing Convert came out different, and
pressing Convert "fixed" it.

A second, smaller fault: the look inherited from the previous frame (crop,
flips, rotation, adjustments, areas) was applied on the GUI thread AFTER the
worker had converted. That meant (a) the measurements were made without the
crop that marks the picture, and (b) the previous frame's auto Temperature/Tint
was copied over the new frame's own.

## Goals
- A tethered capture converts exactly as Convert Current would convert it,
  with the same recorded `conversion_inputs`.
- The inherited look (including Flip H, which the user relies on when scanning
  negatives back to front) is on the capture before it converts.
- A Temperature/Tint written by auto white balance belongs to its frame and is
  not inherited; one the user set by hand is a look and is inherited.

## Non-goals
- No change to when a capture converts: still only with Positive mode off and a
  roll black point set (the lead-frame flow, `spec/tether-black-point.md`).
- No change to Convert Current's results.
- No unanchored (no-black-point) convert from the worker: that path asks first
  in the GUI (`spec/no-anchor-convert.md`), so the worker leaves such a frame
  unconverted.

## Processing
Convert Current's body is split into two GUI-free backend steps, used by both:

- `CCRBackend.plan_frame_conversion(img)` → dict `black`, `white`, `slopes`,
  `rect`, `auto_res`, `auto_source`, `awp_rec`. It reloads a converted frame,
  runs the auto black point (and the roll fallback, `recorded_roll_black`), the
  per-frame white point (`frame_highlights` → `resolve_white_point`, roll
  reference `roll_highlight_reference`), picks the slopes, and the picture rect.
- `CCRBackend.apply_frame_conversion(img, plan)` → `ccr_normalize_with_bwpoint`,
  `conversion_inputs` (`mode: "bw"`, `bw`, `fine_rot`, `density`, `slopes`,
  plus `auto_bp`, `auto_wp`, `picture` when present), `measure_auto_tone`,
  `maybe_auto_awb`.

The panel's `_on_convert_current_bwpoint` keeps its own confirmations, hints and
flashes around these two calls. The worker's `_convert_like_current(img)` plans,
returns unconverted when `plan["black"]` is None, applies, and builds the
preview. On an exception the capture still imports, unconverted.

The roll helpers iterate a copy of `ccr_backend.images`, since the worker now
calls them off the GUI thread while captures are being appended.

## The inherited look
`tether_watcher.look_template(src)` / `apply_look_template(img, t)` (moved from
MainWindow) carry adjustments, areas, rotation, fine rotation, both flips, crop,
crop angle and colour profile, as deep copies. `look_template` drops a
Temperature/Tint pair equal to `src.awb_auto` (still untouched since AWB wrote
it).

The GUI hands the template to the worker at dispatch, from the frame on screen
(`MainWindow._hand_template`, called by `_dispatch_to_worker`, `_emit_triplet`
and the one-off worker in `_load_triplet_now`). The worker snapshots
`self.template` when a job starts and passes it to `_finish`, which applies it
to each unconverted image before converting and marks
`img._tether_template_applied`. `_on_capture` then skips its own template, and
re-renders only when the capture arrived with a look but unconverted.

Timing: the template is the look on screen when the file was handed over, not
when it finishes. A change made while a capture is decoding reaches the next
capture.

## Tests
`tests/test_tether_convert_like_convert.py`:
- a capture converted by the worker equals the same frame converted by Convert
  Current (`conversion_inputs` keys and values, pixels);
- the panel's Convert Current goes through plan then apply;
- the template (flip, crop) is on the frame when it is planned; adjustments are
  copied, the AWB pair isn't, a hand-set temperature is; each capture has its
  own copy;
- no roll black point: the capture imports unconverted;
- the main window hands the look to the worker and doesn't re-apply it to a
  capture that already has it.

`tests/test_tether_black_point.py`'s worker test patches `_convert_like_current`.
