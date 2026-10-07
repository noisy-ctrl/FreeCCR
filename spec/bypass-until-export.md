# Bypass until export (Chroma NR and Details)

A **Bypass until export** checkbox in Chroma Noise Reduction and in Details
(sharpening). When ticked, that step is skipped in the preview, thumbnails and
zoom renders, so editing stays quick on slower machines, but it is always
applied in an export (`apply_adjustments(for_export=True)` at the four export
call sites in `ccr_processor`).

Stored as whole-image flags in the global adjustment dict, like `cineon_log`:
`chroma_nr_export_only`, `sharpen_export_only`.

- `GLOBAL_FLAG_KEYS` lists every such flag; `_attach_cineon` re-attaches all
  of them after a slider edit rebuilds the dict (otherwise they would vanish
  on the next slider move).
- Each flag rides its section's sync group (`noise`, `details`) and paste row
  (the row label says "bypass until export"); un-synced or un-ticked targets
  keep their own flag.
- Never stored in area layers. Area layers can add Chroma NR (the global flag
  governs it); sharpening is global only (spec/sharpening.md Non-Goals).
- In `apply_adjustments` the Details stage runs when
  `for_export or not sharpen_export_only`, alongside its existing `skip_dust`
  guard; `sharpen_scale` (the preview exaggeration) is unaffected.

Tests: `tests/test_bypass_until_export.py`.
