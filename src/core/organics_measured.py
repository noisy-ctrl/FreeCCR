"""Colour Organics' measured hue curve (spec/colour-organics.md,
revision 2026-11-09). Written by tools/fit_organics.py - do not edit
by hand; re-run the tool with more pairs instead.

TABLE[k] is the hue move in HSV degrees at hue 10*k, at the stored
amount 100 (shown 50), read by a periodic Catmull-Rom spline."""

# Fitted on 33 pairs from 3 lab(s), smoothness 1, each lab weighted the same:
#   Colour Organics: the labs' hue minus FreeCCR's default render
#   (mid and bright tones, value >= 0.35), degrees
#
#   RMS by area of picture, and (in brackets) with every cell alike
#   in sample: 2.10 (3.87); no curve: 3.05 (5.00)
#   060940 predicted from the others: 1.77 (3.20); no curve: 2.92 (4.51)
#   148684250 predicted from the others: 2.73 (4.92); no curve: 3.57 (6.00)
#   00003875 predicted from the others: 2.54 (4.01); no curve: 2.56 (4.31)

HUE_STEP = 10.0
TABLE = (
    +2.556, +2.428, +2.115, +1.939, +2.578, +3.331,
    +3.033, +1.860, +0.764, +0.544, +0.889, +1.317,
    +1.493, +1.375, +1.181, +1.098, +1.167, +1.266,
    +1.154, +0.430, -1.045, -2.169, -2.395, -1.996,
    -1.211, -0.553, -0.159, +0.008, +0.031, -0.008,
    -0.035, +0.023, +0.253, +0.733, +1.462, +2.206,
)
