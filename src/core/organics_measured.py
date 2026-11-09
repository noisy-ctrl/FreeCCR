"""Colour Organics' measured hue curve (spec/colour-organics.md,
revision 2026-11-09). Written by tools/fit_organics.py - do not edit
by hand; re-run the tool with more pairs instead.

TABLE[k] is the hue move in HSV degrees at hue 10*k, at the stored
amount 100 (shown 50), read by a periodic Catmull-Rom spline."""

# Fitted on 35 pairs from 4 lab(s), smoothness 1, each lab weighted the same:
#   Colour Organics: the labs' hue minus FreeCCR's default render
#   (mid and bright tones, value >= 0.35), degrees
#
#   RMS by area of picture, and (in brackets) with every cell alike
#   in sample: 2.14 (3.91); no curve: 3.08 (5.02)
#   060940 predicted from the others: 1.77 (3.20); no curve: 2.92 (4.51)
#   148684250 predicted from the others: 2.73 (4.93); no curve: 3.57 (6.00)
#   00003875 predicted from the others: 2.54 (4.00); no curve: 2.56 (4.31)
#   slope round the circle: 0.84 .. 1.09 at the measured amount

HUE_STEP = 10.0
TABLE = (
    +2.540, +2.414, +2.109, +1.939, +2.577, +3.326,
    +3.028, +1.858, +0.765, +0.543, +0.885, +1.311,
    +1.485, +1.369, +1.175, +1.094, +1.163, +1.262,
    +1.151, +0.428, -1.044, -2.170, -2.396, -1.986,
    -1.157, -0.402, +0.148, +0.474, +0.641, +0.670,
    +0.635, +0.647, +0.714, +0.979, +1.552, +2.214,
)
