# A camera's gate edge (DRAFT, awaiting sign-off)

The user, 2026-11-20, with two references (a 6x6 full-border scan, a 35 mm
print with a worn black border): "the film gate isn't perfectly square. It
stays on the same camera and doesn't vary between frames. Pick one and stick
with it for the same format?" … "Maybe model off of my own negatives?" …
"That 35mm frame isn't a great template … It should be a little squarer."

## Measured

`core/film_gate_data.GATES` is written from his scans, never by hand. 35 mm:
his roll `_DSF3322`–`3335` (full-width scans, holes as the scale: 0.0095 mm a
half-size pixel). Per side, the steepest base-to-picture step across the
edge, sub-pixel, every pixel along it, a robust straight line taken off, the
residual inward (mm) at 230 even steps from 4 % to 96 % of the side. Frames
`_DSF3322` and `_DSF3327` (scanned turned) agree to 3–5 µm RMS on three
sides and 32 µm on the fourth once turned: a gate is fixed, as he said. Size
36.02 x 24.34 mm, corners rounded 0.26–0.70 mm. The long side's 0.45 mm
dents fall at the sprocket pitch: the holes seen past the edge, not the gate.

120 (6x4.5): his XP2 frames' left and right edges are the carrier's mask
(R0016 003 is an empty carrier), so only the top and bottom gate edges show;
not measured yet. Until a format is measured it gets a drawn gate
(`gate_outline`, seeded by the format, the same every time).

## Made a little squarer

`film_gate.squared`: a running median over 4.5 % of each side (takes the
hole-pitch dents out), the wander × 0.5 and softly capped at 0.06 mm
(`tanh`), the corners' rounding × 0.35 (0.09–0.25 mm). `outline_for(fmt, w,
h)` = the squarer measured gate, else the drawn one. The outline only ever
comes inward of the image area, so the picture is always under it.

## Where it would go (after sign-off)

The contact sheet strips' Gate (Square / **Your camera's** / Camera's own)
and the collage's Generated film borders.
