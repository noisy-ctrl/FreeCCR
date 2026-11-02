"""Colour Organics' measured hue curve (spec/colour-organics.md,
revision 2026-11-02). Written by tools/fit_organics.py - do not edit
by hand; re-run the tool with more pairs instead.

TABLE[k] is the hue move in HSV degrees at hue 10*k, at the stored
amount 100 (shown 50), read by a periodic Catmull-Rom spline."""

# Fitted on 12 pairs from 2 lab(s), smoothness 1:
#   Colour Organics: the labs' hue minus FreeCCR's default render
#   (mid and bright tones, value >= 0.35), degrees
#
#   RMS by area of picture, and (in brackets) with every cell alike
#   in sample: 2.11 (4.84); no curve: 3.29 (6.31)
#   lab 060940 predicted from the others: 2.14 (3.10); no curve: 2.69 (4.57)
#   lab 148684250 predicted from the others: 3.34 (6.74); no curve: 4.30 (8.14)

HUE_STEP = 10.0
TABLE = (
    +1.797, +2.761, +3.330, +3.076, +3.464, +4.051,
    +3.188, +1.807, +0.746, +0.336, +0.535, +0.873,
    +1.027, +0.937, +0.721, +0.595, +0.634, +0.825,
    +1.058, +0.517, -1.312, -2.504, -2.096, -1.272,
    -0.579, -0.154, +0.042, +0.091, +0.067, +0.013,
    -0.048, -0.095, -0.093, +0.021, +0.337, +0.942,
)
