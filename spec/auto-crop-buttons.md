# Auto Crop as its own action

Builds on `spec/auto-crop.md` and `spec/auto-crop-learning.md`.

## Goal

Auto crop was a tickbox that made Convert crop as well. Make it an action of
its own, usable before or after converting, and drop the tickbox.

## UX

- Under **Convert Current / Convert All**, a matching row: **Auto Crop** (the
  current frame) and **Auto Crop All**.
- **Auto Crop** finds the current frame on the raw scan (re-read at 1080 px
  when the frame is converted, ~0.4 s; the conversion is untouched), with
  what has been learned, and sets it as the crop when it is confident (high
  or medium), replacing any crop the frame had. One undo step. Otherwise the
  crop is left alone and the hint says why.
- **Auto Crop All** does every frame whose crop is still auto crop's own or
  that has none (frames you confirmed with Done or cropped by hand are left
  alone), with the roll check; it asks first (with the counts), shows a
  cancellable progress dialog, and ends with the summary
  ("Auto crop: 34 cropped (…); 2 left as they were (…)"). It replaces
  Re-crop all frames, which did the same on an already cropped roll.
- The Crop panel's **Auto** stays: a suggestion to adjust before Done.
  Since 2026-10-23 it is the SAME function as Auto Crop
  (`CCRBackend.auto_crop_suggestion`) with the same confidence gate: a
  low-confidence find no longer replaces the box (it had shown unsure,
  sometimes wrong boxes, which is why Auto Crop seemed to work better); the
  status says what it found and why it was left.
- Convert no longer crops. The `convert/auto_crop` setting is removed.

## Integration

- `CCRBackend.auto_crop_one(img)` -> (FrameCrop, applied);
  `auto_crop_candidates()`; `auto_crop_all(progress, should_cancel)`.
- The Convert paths are back to exactly what they were before auto crop (one
  pass; no detection), so a Convert is no slower than before.
- `SlidersPanel.auto_crop_btn` / `auto_crop_all_btn`.

## Tests

- Convert leaves the crop alone; Auto Crop on a converted frame crops it and
  leaves the conversion untouched.
- Auto Crop All leaves confirmed / hand-drawn crops; unchanged results push no
  undo; cancelling changes nothing.
- The panel has the two buttons and no tickbox; the hints for a crop and a
  refusal.

> Revised 2026-10-30 by `spec/convert-crop-and-clip.md`: Convert now also crops each frame it converts (a Settings switch, on by default), with the same detection and the same rules as Auto Crop All.
