# A flat is for its own format

Status: BUILT 2026-11-20.

The user, 2026-11-20: "Flat fields might be best to be tied to specific
formats? The 120 film carrier is vertical for 120:6x4.5 but if I ever
scanned 6x7 for instance, it would be horizontal... Xpan format would
require a larger area of the light too." And: "All the while, flat fields
need to track, remember."

## 1. What changes

Flats already carried a format (the wizard's and Shoot Flat Fields' Format)
and the per-frame choice preferred it, but with no flat of the frame's
format it fell back to ANY flat of its light: a 6x4.5 flat (carrier stood
up) on a 6x7 frame (lying down). Now (`flat_field.profile_for`):

- **Format known**: a flat of that format (one whose opening fits the
  frame's preferred); else a format-less flat whose opening fits (or can't
  be checked); else **none** (the active profile, normally none). Never
  another format's flat.
- **Format unknown**: the flat whose carrier opening has the frame's shape
  (`opening_fits` is True); else a format-less one; else, when the library
  holds one format only and nothing says it doesn't fit, that one; else none.
- **A preset's flat** (spec/preset-flat-fields.md) is used only for frames
  of its format whose opening fits; otherwise the automatic choice
  (`pinned` → "format").

`opening_fits(flat, frame_opening)`: per axis where both openings have both
sides, the span ratio must be within the tracking limits (0.85–1.15), and
with both axes the two ratios within 8 % of each other (`ASPECT_TOL`): a
carrier stood up doesn't fit one lying down. True / False / None (can't
tell). The finder needs the mask's bands under ~25 % of the view; with the
opening filling the view's width (the user's 6x4.5 framing) only one axis
is seen, so the FORMAT is what keeps formats apart and the shape check is
the second guard.

The decode reads the frame's opening FIRST when any library flat has one
(`needs_opening`, `CCRImage._apply_field_correction`, then
`profile_for(img, opening=)`); the signature reads the opening kept on the
frame (`img.flat_opening`), so a frame stamped at decode agrees with it.
The IR flat is chosen with the IR shot's own opening.

## 2. Tracking (unchanged, restated)

Every flat is moved to the opening of the shot it corrects: a white or RGB
flat to the frame's (a trichrome merge's), the IR flat to the IR shot's own
(spec/ir-dust-and-flat-sets.md §6).

## 3. Light coverage (XPan)

`build_tagged_profile` measures the dimmest light inside the opening as a
share of its brightest (`coverage`, lowest channel); under 30 %
(`COVER_MIN`) it adds a warning: "The light falls to N % of its brightest
at the edge of the carrier's opening: is it big enough for this format?"
(Shoot Flat Fields shows the profile's warnings in its caption; the wizard
in its summary.) The gain map is capped at 4×, so light under 25 % stays
dark whatever the flat.

## 4. Shoot Flat Fields

Format list: the formats first, "Any format (no format of its own)" last;
the default is the roll's format, else the guide's, else the last one shot
(`live/flat_format`); "Any format" only when none is known. The window says
a flat is for its format only.

## 5. Tests

`tests/test_flat_format_match.py`: an opening fits only its own shape; a
flat is never borrowed by another format (format-less only when its opening
fits); an unknown format gets the flat of its opening's shape; the decode
reads the opening first and its signature agrees; a light too small for the
opening is warned; a preset pin of another shape is left for the automatic
choice. Updated: `test_flat_sets` (6x7 → no flat; IR 6x9 → none),
`test_preset_flat_fields` (pins are of the frame's format).
