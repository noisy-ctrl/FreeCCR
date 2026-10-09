# Preview detail: Fast / Balanced / Full

## Goal

One setting for how much detail the editing preview renders, with a real
middle option. It replaces the two Zoom checkboxes ("Load full resolution at
100% zoom", "Sharp preview on high-resolution screens"), which between them
only allowed the two ends.

The cost of one edit's render grows with the pixel count (container, numpy
path, same frame and settings):

| Long side | MP | Per edit |
|---|---|---|
| 1080 (editing preview) | 0.9 | 0.07 s |
| 2000 | 3.0 | ~0.45 s |
| 2560 (screen-sized, Retina) | 4.9 | 0.8 s |
| 4128 (half-size RAW decode) | 12.8 | 2.1 s |
| 6000 | 27 | 5.0 s |
| 8256 (full GFX50S II) | 51 | 12.1 s |

On a Retina screen 50% zoom already needs every source pixel (one source pixel
per *device* pixel), so "Full" re-renders 51 MP after every settled edit as
soon as the user zooms in at all.

## Non-goals

- Changing the 1080 editing preview, the export, or any colour. This is display
  resolution only: nothing is re-converted and catalogs are untouched.

## Levels

| Level | Fitted view (high-DPI) | Zoomed |
|---|---|---|
| **Full** (default, today's behaviour) | screen-sized render | up to the file's own resolution |
| **Balanced** | screen-sized render, capped at **2000 px** | capped at **6000 px** (~half the pixels of full) |
| **Fast** | none: the 1080 preview, smoothly scaled | half-size decode (today's "full-res zoom off") |

Balanced's zoom cap is above the half-size decode, so it still needs one full
decode when zooming in (cached per image), but each settled edit then renders
27 MP instead of 51.

## Data / integration

- `ccr_backend.preview_detail` ∈ {"full", "balanced", "fast"}; `ccr_backend.
  set_preview_detail(level)` sets it AND the two existing flags it subsumes
  (`sharp_fit_preview`, `full_res_zoom`), so every existing code path keeps
  reading those. `ccr_backend.preview_caps()` → `(fit_cap, zoom_cap)`, `None`
  meaning uncapped.
- `ImagePreview._hires_target_long_side` clamps `want` to the cap for the
  fitted-view render and for zoom tiles. `hires_decode_request` is unchanged:
  its floor (today's half-size resolution) still applies, so no level is ever
  softer when zoomed than "Fast".
- QSettings `view/preview_detail`. Migration when absent: both old flags on →
  Full, both off → Fast, mixed → Balanced. The old keys are still written so a
  downgrade keeps working.
- Settings → General → Zoom: a "Preview detail" combo with one line per level.
  Applying calls `MainWindow.on_preview_detail_changed(level)`, which releases
  the current detail tile and re-requests at the new resolution (no
  reprocess).

## Tests

- Level → flags/caps mapping; unknown level falls back to Full.
- `_hires_target_long_side` respects each cap at the fitted view and when
  zoomed, and Fast matches the old both-off behaviour exactly.
- Migration from the old keys; the Settings combo seeds from and applies to the
  backend.
