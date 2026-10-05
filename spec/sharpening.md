# Sharpening, and "Bypass until export"

## Sharpening

Collapsible **Sharpening** section after Chroma Noise Reduction (separator
between). Sliders: **Amount** 0–100 (0 = off; 100 = 200 %), **Radius**
0–100 (default 25), **Threshold** 0–100 (default 0).

Luma unsharp mask: `detail = Y − GaussianBlur(Y, σ)`, the same delta added
to R, G and B, so colour differences are untouched (no colour fringes).
Threshold is a soft knee (smoothstep from thr/2 to 3·thr/2 on |detail|;
100 = 4 % of full scale) that leaves grain and noise-sized detail alone.
σ = long side × lerp(0.00005, 0.0006, radius/100), so preview, zoom and
export match; skipped below 0.3 px (nothing to sharpen at that scale, which
is why it only shows when zoomed in).

Pipeline: the very last step of `apply_adjustments` (output sharpening),
after area layers and the B&W collapse; area layers can add their own amount.
Skipped with `skip_dust` (sample patches). Own sync group **Sharpening**;
pastes as one row.

## Bypass until export

A checkbox in each of Chroma NR and Sharpening. When ticked, that step is
skipped in the preview, thumbnails and zoom renders, but always applied in an
export (`apply_adjustments(for_export=True)` at the four export call sites in
`ccr_processor`). Stored as whole-image flags `chroma_nr_export_only` /
`sharpen_export_only` in the global adjustment dict, like `cineon_log`:

- `GLOBAL_FLAG_KEYS` lists every such flag. `_attach_cineon` re-attaches all
  of them after a slider edit rebuilds the dict (otherwise they'd vanish on
  the next slider move).
- Each flag rides its section's sync group and paste row (the paste label says
  "bypass until export"); un-synced / un-ticked targets keep their own flag.
- Never stored in area layers; the global flag governs area renders too.

## Tests

`tests/test_sharpening.py`.
