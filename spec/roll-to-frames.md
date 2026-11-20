# The film menu's Film and Format can go to the open frames

Status: BUILT 2026-11-20.

The user, 2026-11-20, asked whether a converted frame with no film and
format gets its flat field once he picks them in the film menu. It didn't:
the toolbar's film-canister menu sets the ROLL (`MainWindow.capture_session`:
the next captures' names, log, preset and the window title), never the open
frames, and a frame's flat comes from its own film (`img.film_preset`) and
format (`img.film_format`). His answers: "1. Ideal but maybe be able to say
selected or all frames open. 2. Sounds right" (2 = the Crop panel's format).

## 1. The menu asks

Picking a Film or a Format in the canister menu still sets the roll first
(`set_roll_film` / `set_roll_format`, unchanged). With frames open,
`MainWindow.pick_roll_film` / `pick_roll_format` (what the menu calls now)
then asks (`_ask_roll_to_frames(what, n_selected, n_open)`, the seam):

> Give the open frames this film too?
> [Selected Frame / Selected Frames (N)] [All Open Frames (N)] [Just the Roll]

Just the Roll (also Esc) is the old behaviour. Selected / All write the
frames' own value, replacing what they had (None and Not set clear it):
`img.film_preset` for the film, `img.film_format` for the format. Only the
film is marked: the preset's slider settings are not pasted (Apply Preset
does that, as before).

## 2. The flat follows at once

`CCRBackend.mark_frames(indices, **attrs)` sets the attributes and returns
the decoded frames whose flat field changes with them (their grading
signature for that frame, `active_profile_signature(img)`, before vs after);
`MainWindow.mark_frames` re-grades those (`ThumbnailList.
replace_with_current_profile`: re-decoded, conversion replayed, crop and
settings kept), refreshes the ⚠ flags, saves the catalogue and says how many
were re-processed. Apply Preset / Write Preset (`SlidersPanel._mark_preset`)
use the same backend call.

## 3. The Crop panel's format

Done with a film format picked (`ImagePreview` crop confirm) sets
`img.film_format` through `mark_frames`; when that changes the frame's flat,
the frame is re-graded after the crop is applied and the hint says so
("… re-processed with the 6x7 flat field"). Before, the frame kept its old
flat with no ⚠ (the flags were only refreshed on load and profile changes).

## 4. Tests

`tests/test_roll_to_frames.py`: `mark_frames` lists only decoded frames
whose flat changes; the menu's pick asks only with frames open, Selected /
All / Just the Roll write the right frames (None / Not set clear), and the
re-grade gets exactly the frames whose flat changed; the Crop panel's
format re-grades a frame whose flat changes and not one whose doesn't.
