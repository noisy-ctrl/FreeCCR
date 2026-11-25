"""Lab Tone curve (spec/lab-tone.md).

WRITTEN by tools/fit_lab_tone.py, never edit by hand: OKLab L of FreeCCR's
render (default render with each frame's six Channel Levels numbers fitted to
the lab scan and FreeCCR's own conversion-time Auto tone, then Organics v2
50 / Organic Saturation 50 / Clean Lights 50)
mapped to the labs' L by matching each frame's lightness quantiles over its
registered area (0.5 .. 99.5 %), the frames' mappings median-combined per lab
and the three labs (00003875, 060940, 14868425; 50 Portra 400 pairs)
averaged, lightly smoothed, sampled at KNOTS."""

KNOTS = (0.00, 0.08, 0.16, 0.24, 0.32, 0.40, 0.48, 0.56, 0.64, 0.72, 0.80, 0.88, 0.94, 1.00)
VALUES = (0.113, 0.148, 0.182, 0.234, 0.297, 0.372, 0.455, 0.552, 0.647, 0.732, 0.815, 0.885, 0.932, 0.979)
