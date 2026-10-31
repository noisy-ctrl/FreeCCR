# Spec: Saved Film-Stock Slopes for Black-Point-Only Conversion

Status: IMPLEMENTED (v2)
Owner: FreeCCR
Feature branch: `feature/film-stock-slopes`
Builds on: `spec/optional-white-point-default-slope.md` (the default-slope mode)

## 1. Summary

Let the user **save the slope implied by a sampled B/W-point pair as a named
"film stock" preset**, and later select that preset to drive the
**black-point-only** conversion instead of the baked scalar
`DEFAULT_DENSITY_SLOPE`.

Rationale: the density slope `S_den[c] = 1 / log10(base[c] / dense[c])` is a
property of the **film stock** (per-dye-layer characteristic-curve gamma),
while the black point is a property of the **light source / session**. The
default-slope spec already measured that per-channel *density* ratios barely
move across light sources (B/G 1.16→1.20) where linear ones swing wildly
(2.55→1.22). So: sample a B/W pair once on a representative frame of a stock,
save its per-channel density slopes under a name ("Portra 400"), and on every
future roll of that stock only re-sample the black point — the saved slopes
supply the stock's contrast *and* per-channel colour character.

Behaviours:
1. **Slope combo = "Default"** (initial state) → black-point-only conversion is
   byte-identical to today (scalar `DEFAULT_DENSITY_SLOPE`).
2. **Slope combo = saved stock** → black-point-only conversion uses the stock's
   per-channel density slopes.
3. **White point sampled** → two-point conversion, exactly as today; the combo
   is inert (disabled) because the sampled pair overrides any preset.
4. **Save**: with both points sampled, a Save button computes the slopes from
   the current pair and stores them under a user-entered name.
5. **Delete**: a saved stock can be deleted; selection falls back to Default.

## 2. Goals / Non-goals

### Goals
- Save/select/delete named per-channel density-slope presets ("film stocks").
- Presets drive `_default_slope_invert` per channel; Default keeps the scalar.
- **Byte-identical** output when Default is selected (regression-safe), and in
  two-point mode always.
- The slopes used at convert time are **snapshotted into `conversion_inputs`**
  so zoom/hi-res replay, export, slice children, catalog restore, re-grade and
  the density-toggle reprocess all reproduce the conversion even if the preset
  is later renamed/deleted.
- Presets persist across sessions (QSettings). The **selection does not**: it
  resets to "Default" at app start, on every new batch load, and on
  tether session start — a stock chosen for one roll must be re-chosen for the
  next, never silently applied. (Originally the selection persisted too;
  reversed by user request — new images always convert with the default slope
  unless a stock is explicitly chosen for that roll.)
- Tethered captures convert with the selected stock (black-point-only mode).

### Non-goals
- No editing of a preset's numbers in the UI (re-sample + re-save to update).
- No change to two-point conversion (linear or density) — a sampled white point
  always wins over a preset.
- No preset import/export files, no bundled factory stock library.
- No per-image preset assignment — like the B/W points themselves, the selected
  stock is global convert-time state; per-image state is the snapshot.
- The auto-exposure (`exposure_base`) behaviour of black-point-only mode is
  unchanged (still computed from the converted preview).

## 3. Background / decisions made

- **Why per-channel is OK here when the default is a scalar.** The v4 default
  rejected per-channel slopes because a *universal* baked slope must not encode
  any one stock/light's colour. A preset is the opposite: the user explicitly
  wants *that stock's* colour character. Cross-light robustness is retained
  because slopes multiply `log10(base/img)`, which cancels per-channel light
  scaling; only the (cheap, per-session) black-point re-sample varies.
- **What is stored: slopes, not points.** The preset stores the derived
  `slopes_bgr` triple. Storing the raw B/W pair instead would tie the preset to
  the session's absolute scan values; the slopes are the invariant. The source
  pair is kept alongside as provenance metadata only (not used in math).
- **Density slopes only.** Black-point-only mode is always density-space (the
  `density_bwpoint` toggle applies to two-point mode only, as today). Linear
  slopes are light-source-specific and were already rejected in v4.
- **Snapshot by value.** `conversion_inputs` carries the slope triple itself,
  never the preset name. Deleting a preset can therefore never break replay of
  already-converted images. (Same principle as the existing `bw` anchors.)
- **Precedence.** Sampled white point > selected stock > default scalar. The UI
  makes this visible: with a white point set the combo is disabled and the mode
  label says "white point (two-point)".

## 4. UX / Interaction

### 4.1 Placement — Film B/W Point section (sliders panel)

A new row directly under the Set-point buttons, above the mode label:

```
Film B/W Point
[Set Black Point] [Set White Point] [✕]
Film Stock  [ Default            ▾ ] [＋] [🗑]
Anchor source: film stock "Portra 400" (black point only)
[Convert Current] [Convert All]
```

- **Combo** (`film_stock_combo`): first entry **"Default"**, then saved
  stocks sorted case-insensitively by name. Laid out like the Color Profile
  row (label column + combo) so it aligns with the section.
- **Save button** (`＋`, glyph width, tooltip "Save the current B/W-point pair
  as a named film stock"): enabled **only when both points are sampled**
  (that's the only time slopes can be computed). Clicking prompts for a name
  (`QInputDialog.getText`); empty/whitespace names rejected; an existing name
  asks to overwrite. On success the new stock is **selected** in the combo, so
  clearing the white point (✕) immediately activates it — the natural
  "calibrate a stock from the lead frame, then run the roll" flow.
- **Delete button** (`🗑`, danger-styled glyph like the ✕): enabled only when a
  saved stock is selected. Confirms, deletes, selection falls back to Default.
  Already-converted images are unaffected (snapshot by value).
- **Disabled states**: with a white point sampled, combo + delete are disabled
  (tooltip: "White point set — two-point conversion in use; clear it (✕) to
  use a film-stock slope"). Save is disabled unless both points are sampled
  (tooltip says why).

### 4.2 Mode label

`_update_bwp_mode_label` gains a third case (prefix renamed "Slope source" →
"Anchor source" by user request):
- no black point → hidden (unchanged)
- white point set → "Anchor source: white point (two-point)"
- black only, Default → "Anchor source: default slope (black point only)"
- black only, stock selected → `Anchor source: film stock "<name>" (black point only)`

### 4.3 Hints

The "Black Point sampled!" hint mentions the stock when one is selected
("…click <b>Convert</b> to use film stock \"Portra 400\"…"). The save action
confirms with a hint ("Film stock \"Portra 400\" saved.").

### 4.4 Startup / new batch

Presets are restored from QSettings when the panel is built; the combo always
starts on **Default** (the legacy `convert/film_stock_selected` key is
removed on startup). When a new batch is loaded (Open Files / Open Folder /
tether session start), `MainWindow` calls
`sliders_panel.reset_film_stock_combo()` and the backend loader clears its own
copy in `load_images_from_files` — mirroring the per-roll B/W-point reset.

## 5. Data model

### 5.1 Preset store — `src/core/film_stocks.py` (new)

Pure, Qt-free helpers (unit-testable) plus thin QSettings I/O in the panel:

```python
FilmStock = {           # plain dict, JSON-safe
  "name":       str,    # unique, case-insensitive, stripped
  "slopes_bgr": [b, g, r],   # per-channel density slopes (floats > 0)
  "black_bgr":  [b, g, r] | None,   # provenance only
  "white_bgr":  [b, g, r] | None,   # provenance only
  "created":    str,    # ISO date, informational
}
```

- `encode_film_stocks(stocks) -> str` / `decode_film_stocks(s) -> list` —
  JSON round-trip, defensively validating every record (bad/partial records
  dropped, slopes coerced to float, non-positive slopes rejected).
- `upsert_film_stock(stocks, stock) -> list` — replace by case-insensitive
  name or append; returns a new sorted list.
- `remove_film_stock(stocks, name) -> list`.
- `find_film_stock(stocks, name) -> dict | None` (case-insensitive).

QSettings keys (`FreeCCR/FreeCCR`, next to the existing `convert/*` keys):
- `convert/film_stocks` — the JSON string.
- `convert/film_stock_selected` — LEGACY (selection persisted originally);
  no longer written, and removed on panel startup so old installs also start
  on Default.

### 5.2 Backend global state — `CCRBackend`

- `self.film_stock_slopes: tuple[float,float,float] | None = None` — the
  ACTIVE slopes for black-point-only conversion (None = default scalar).
- `self.film_stock_name: str | None = None` — for the mode label / hints only.
- `set_film_stock(name, slopes_bgr)` / `clear_film_stock()`.

Like `black_point_bgr`, this is global convert-time state owned by the panel.

### 5.3 Conversion snapshot — `conversion_inputs`

`mode: "bw"` records gain an optional key:
- `"slopes": (b, g, r) | None` — the per-channel slopes actually used.
  Present-and-None (or absent — legacy catalogs) means the default scalar.
  Only meaningful when `bw[1]` (white) is None; writers set it to None in
  two-point mode.

Catalog `_ci_to_json`/`_ci_from_json` convert the triple list↔tuple like the
other tuple keys. Legacy records without `"slopes"` replay as today.

## 6. Processing / math

### 6.1 Slope computation — `compute_density_slopes` (ccr_processor.py, new)

```
compute_density_slopes(black_point_bgr, white_point_bgr) -> (b, g, r) | None
per channel c:
  base  = black[c]; dense = white[c]
  invalid if base <= 0 or dense <= 0 or base <= dense
  D[c]  = log10(base / dense);  invalid if D[c] <= 1e-6
  S[c]  = 1 / D[c]
returns None if ANY channel is invalid (an unusable pair is rejected whole)
```

This is `log_bwpoint_slopes`' DENSITY vector, promoted from a diagnostic
print to a return value (the diagnostic stays as-is).

### 6.2 Inversion — `_default_slope_invert(img_f, black, slopes_bgr=None)`

Per channel `c`: `slope_c = slopes_bgr[c]` if provided else
`DEFAULT_DENSITY_SLOPE` (identical code path, the scalar becomes a per-channel
value). Everything else — density floor, clamp-at-0, working-space windowed
encode / legacy clip, `DEFAULT_DENSITY_GAMMA` — is unchanged. With
`slopes_bgr=None` the function is byte-identical to today.

Endpoint check: a pixel at the stock's calibration dense value has
`d[c] = D[c]` per channel, so `out = S[c]·D[c] = 1.0` → white, exactly like
the two-point density mode at its dense sample. Light-source invariance is
per-channel unchanged: `log10(k·base / k·img) = log10(base/img)`.

### 6.3 Threading it through (signature additions, all defaulting to None)

- `ccr_normalize_with_bwpoint(..., slopes_bgr=None)` — forwarded to
  `_default_slope_invert` when `white_point_bgr is None`; ignored otherwise.
- `apply_bwpoint_normalization(img, black, white=None, density=False,
  slopes_bgr=None)` — same routing (zoom / slice / restore replay).

## 7. Integration points

Writers (record `"slopes"` at convert time; all pass
`slopes = ccr_backend.film_stock_slopes if white is None else None`):
- `sliders_panel._on_convert_current_bwpoint`
- `ccr_backend.apply_bwpoint_to_all_images` (Convert All)
- `tether_watcher.CaptureWorker` — snapshot slopes with black/white in
  `process()` (same mid-decode-flip protection), used in `_convert`.

Replayers (read `ci.get("slopes")` and forward):
- `ccr_backend._reconvert_in_place` (camera-profile re-grade)
- `ccr_backend.export_image_by_index` — snapshot branch AND the legacy
  un-snapshotted branch (which uses the live `self.film_stock_slopes`)
- `ccr_backend` slice: children replay with the parent's slopes and inherit
  `"slopes"` in `child_ci`; un-slice parent restore likewise
- `ccr_backend.reprocess_all_for_density_bwpoint_change` — `dict(ci)` copy
  already carries `"slopes"`; the black-only replay must forward it
- `ccr_image` hi-res zoom replay
- `catalog._replay_conversion` (session restore)

UI / persistence:
- `sliders_panel.py` — combo row + save/delete buttons, enable/disable logic,
  mode label, hints, QSettings load/store, backend sync.
- `main_window.py` — nothing new at startup beyond what the panel does itself
  (the panel owns the combo; `_restore_bwpoint` stays as-is).
- `settings_dialog.py` — untouched (this is working state, not a preference).

## 8. Test plan (`tests/test_film_stocks.py`)

Math (`compute_density_slopes`):
- Known pair → expected per-channel `1/log10(base/dense)` values.
- Degenerate pairs (base<=dense, zero/negative channel, D≈0) → None.

Inversion (`_default_slope_invert` / `apply_bwpoint_normalization`):
- `slopes_bgr=None` → byte-identical to current output (regression, both
  working-space and legacy paths).
- With slopes from a pair: the pair's dense value maps to white per channel;
  base maps to black; monotone; per-channel light-scaling invariance
  (`k·base, k·img` → identical output) still holds.
- Consistency: preview path (`ccr_normalize_with_bwpoint`) equals replay path
  (`apply_bwpoint_normalization`) with the same slopes.

Store (`film_stocks.py`):
- encode/decode round-trip incl. unicode names; corrupt/partial JSON → [].
- upsert new / overwrite same name (case-insensitive) / sort order; remove;
  find case-insensitive.

Snapshot / replay:
- `conversion_inputs` with `"slopes"` survives `_ci_to_json`/`_ci_from_json`
  (tuple restored); legacy record without the key replays with default scalar.
- Slice child `child_ci` inherits `"slopes"`.
- Tether `_convert` records the snapshot slopes.

UI (smoke, offscreen like existing panel tests where present):
- Save disabled without both points; combo/delete disabled with a white point;
  delete of the selected stock falls back to Default; mode-label text cases.

Test files are run in small groups (full-suite run hangs — pre-existing).

## 9. Open questions — resolution

1. **Scalar vs per-channel preset** — RESOLVED: per-channel. The preset is
   opt-in, per-stock colour character is the point, density space keeps the
   cross-light robustness.
2. **Disable vs hide the combo in two-point mode** — RESOLVED: disable (with
   tooltip). Hiding would shift the layout and obscure the save workflow,
   which happens exactly while both points are set.
3. **Store points or slopes** — RESOLVED: slopes (invariant), points kept as
   provenance only.
4. **Where presets live** — RESOLVED: QSettings JSON next to the persisted
   B/W points; the catalog stays per-session image state.
5. **Auto-select after save** — RESOLVED: yes; clearing the white point then
   activates the stock immediately.

## Revision: the greyed ＋ says why (2026-10-31)
The user found ＋ greyed out and couldn't add a stock. ＋ still needs a pair
(a White Point sampled by hand on the frame, plus the black point that frame
converted with, or a roll-wide pair from code); its tooltip now names the
missing piece: "Needs a White Point sampled on this frame…" / "Needs this
frame's black point…" / "Not in Positive mode…", and the usual text once it
can save (`SlidersPanel._film_stock_save_tip`).
