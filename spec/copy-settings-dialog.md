# Selective Copy/Paste of Settings

> **Superseded** by `spec/paste-settings-dialog.md`: the dialog moved from copy
> to paste and now lists individual settings.

Give `Ctrl/Cmd+C` the same "pick what you mean" dialog that **Sync to All**
already has, so a copy/paste between two images can carry just the white
balance, just the crop, just the curves — instead of the whole slider set.

## Goals

- `Ctrl/Cmd+C` opens a modal dialog listing the same setting groups as
  **Sync to All** (`SYNC_GROUPS`), each with a checkbox, plus Select All /
  Deselect All. Accepting copies **only** the checked groups.
- `Ctrl/Cmd+V` applies only the copied groups to the current image; every
  setting the user did not copy is left exactly as it was on the target.
- The copy now covers the three whole-image groups Sync to All handles but the
  old copy/paste silently ignored: **Color Profile**, **Crop**, **Curves**.
- The group choice is remembered while the app is open (separately from the
  Sync to All choice), so a repeated copy is two keystrokes.

## Non-goals

- No OS-clipboard interchange or on-disk serialisation — the clipboard stays a
  private in-panel dict, lost on quit, as today.
- No multi-image paste; pasting to many images is what Sync to All is for.
- No new setting groups and no change to `SYNC_GROUPS` membership. Both
  features stay driven by that single list, so a future group appears in both.
- Area layers are still never *copied as layers* — see "Areas" below.

## UX / interaction

**Copy** (`Ctrl+C` / `Cmd+C`, unchanged shortcut):

1. No image selected → unchanged behaviour: hint "No image selected to copy
   from", no dialog.
2. Otherwise a modal **Copy Settings** dialog opens:
   - Prompt: *"Copy these settings:"*
   - One checkbox per `SYNC_GROUPS` entry, in the same order and with the same
     labels as Sync to All.
   - `☑ Select All` / `☐ Deselect All` row, separator, then `Cancel` /
     `Copy` (primary, default button).
   - Checkbox state seeds from the remembered selection; all-checked the first
     time.
3. Cancel → nothing is copied; a previously copied set stays on the clipboard.
4. Accept with nothing checked → hint "Nothing selected to copy." and the
   previous clipboard is left untouched.
5. Accept → hint `Copied N of M setting groups.` (`Copied all settings.` when
   every group is checked).

**Paste** (`Ctrl+V` / `Cmd+V`, unchanged shortcut):

- Nothing copied yet → hint "No settings to paste. Copy first with Cmd+C"
  (unchanged wording/behaviour).
- No image selected → "No image selected to paste to" (unchanged).
- Otherwise the copied groups are applied and the hint reads
  `Pasted N of M setting groups.` / `Pasted all settings.`

The dialog is the existing `SyncSettingsDialog` widget generalised — same
layout, spacing and theme, only the window title, prompt and accept-button text
differ. Users get one visual language for "choose what to apply".

## Data model

`SlidersPanel` state (all reset to `None` on construction):

| Attribute | Meaning |
| --- | --- |
| `_copy_group_selection` | Remembered `{gid: bool}` for the copy dialog. Distinct from `_sync_group_selection`. |
| `copied_selection` | The `{gid: bool}` actually used for the current clipboard entry. `None` = nothing copied. |
| `copied_adjustment` | Dict of the copied **adjustment keys only** (a subset of `ADJUSTMENT_KEYS`), plus `curves` when the curves group was copied and the source has non-identity curves, plus `cineon_log` when the channels group was copied and the source global layer has the flag. |
| `copied_profile` | `"color"` / `"bw"`, or `None` when the profile group was not copied. |
| `copied_crop` | `(crop_rect, crop_angle)`, or `None` when the crop group was not copied. |

`copied_adjustment` stays a plain adjustment dict, but is now **partial** —
that is the one behavioural contract change, and `paste_adjustment_settings` is
its only consumer.

## Copy: what is read from where

Mirrors the Sync to All source rules, except for the layer question:

- **Adjustment-key groups + curves** are read from the *live UI* — the sliders
  and the curve editor — i.e. the **active layer** (global, or the selected
  area). This preserves today's copy behaviour and keeps area→area copying
  useful.
- **Color Profile** and **Crop** are whole-image properties with no per-area
  meaning, so they are read from the `CCRImage` itself regardless of which
  layer is active.
- `cineon_log` rides the channels group exactly as in
  `_perform_sync_to_all`, and `_attach_cineon` already restricts it to the
  global layer.

## Paste: merge semantics

One undo step (`end_undo_burst()` then a single `push_undo_state()`), then:

1. **Adjustment keys.** Build a *complete* dict from the target's active-layer
   settings (missing keys filled via `_default_for`), then overwrite only the
   copied keys. This preserves the app-wide invariant that a non-empty
   adjustment dict carries every key — the same construction
   `_perform_sync_to_all` uses.
2. **Curves.** Copied → set `curves` from the clipboard, or drop the key when
   the source had identity curves. Not copied → carry the target's own
   `curves` across the slider-only rebuild.
3. **`cineon_log`.** Copied (channels group) → follow the clipboard. Not
   copied → preserve the target's existing flag. Always dropped when an area
   layer is the paste target (the display transform is whole-image only) —
   existing behaviour.
4. **Color Profile.** Copied → assign `img.color_profile` and re-sync the combo
   box (the combo is not driven by the adjustment dict).
5. **Crop.** Copied → assign `img.crop_rect` and `img.crop_angle`. A crop
   change also moves the region the histogram samples, so it must be followed
   by a reprocess — which step 6 always does.
6. Write through `set_active_settings_by_index(..., reprocess=True)`, refresh
   the sidebar thumbnail and the preview.

The sliders and curve editor are updated from the merged result, not from the
clipboard, so keys that were not copied keep showing the target's values.

### Resolved edge cases

- **Nothing actually changes** (paste onto the image it was copied from). The
  paste still runs and still pushes one undo state. Unlike Sync to All — which
  skips unchanged images to avoid N dead undo entries across a batch — a single
  explicit paste is cheap, and a no-op undo entry is less surprising than a
  keystroke that appears to do nothing.
- **Areas.** The clipboard has no notion of *which* layer it came from: copied
  slider values paste into whatever layer is active at paste time. Area
  geometry and the area list are never copied.
- **`crop_rect` is `None`.** A copied "no crop" is a real value — pasting it
  clears the target's crop rather than being treated as "nothing to copy".
- **Crop mode open.** `ImagePreview.update_preview` reads `crop_rect` live, so
  the refresh at the end of paste picks up a pasted crop with no extra work,
  the same as Sync to All.
- **Unconverted target.** Not gated, matching today's paste: the settings land
  in the dict and take effect once the image is converted.
- **Group removed from a future `SYNC_GROUPS`.** `copied_selection` is read
  with `.get(gid, False)`, so a stale remembered selection can never resurrect
  an unknown group.

## Integration points

| Location | Change |
| --- | --- |
| `SYNC_GROUPS` (`src/widgets/sliders_panel.py`) | Unchanged — now shared by both dialogs. |
| `SyncSettingsDialog` | Generalised: `title` / `prompt` / `action_label` constructor args, defaulting to today's Sync to All strings. No call-site change. |
| `SlidersPanel.__init__` | New `_copy_group_selection`, `copied_selection`, `copied_profile`, `copied_crop`. |
| `copy_adjustment_settings` | Show the dialog; store the partial clipboard described above. |
| `paste_adjustment_settings` | Group-aware merge instead of whole-dict replace. |
| `user_guide/get_started.md` | Mention that `Ctrl/Cmd+C` now asks what to copy. |

Nothing outside `sliders_panel.py` reads `copied_adjustment`, so the partial
dict cannot leak into the render/export paths.

## Test plan

New `tests/test_copy_settings_dialog.py`, driving `SlidersPanel` with the
existing offscreen-Qt fixtures and a stubbed dialog (monkeypatched `exec_` /
`selection`) so no modal is shown:

1. **Dialog reuse** — the copy dialog exposes the same group ids as
   `SYNC_GROUPS`, in order, and `selection()` reflects the checkboxes;
   `_set_all(False)` clears every box.
2. **Cancelled copy keeps the old clipboard** — copy WB, cancel a second copy,
   clipboard still holds the first entry.
3. **Empty selection copies nothing** — clipboard untouched, hint set.
4. **Partial copy stores only the selected keys** — copying only `wb` yields a
   clipboard with `temperature`/`tint` and no tone keys.
5. **Paste merges** — target with distinct tone + WB values, paste a WB-only
   clipboard: WB takes the source values, every tone key keeps the target's.
6. **Paste keeps target curves when curves not copied**, and replaces/removes
   them when it is.
7. **Profile group** — pasting a `bw` profile flips `img.color_profile` and the
   combo row; not copying it leaves the target's profile alone.
8. **Crop group** — pasting crop assigns `crop_rect`/`crop_angle` and triggers
   a reprocess (target histogram matches the source's cropped histogram, as in
   `TestSyncToAllCropHistogram`).
9. **`cineon_log`** — preserved on the target when the channels group is not
   copied; followed when it is; never written into an area layer's dict.
10. **Adjustment dict completeness** — after any paste the target's active
    settings contain every `ADJUSTMENT_KEYS` entry.

Existing suites that must keep passing unchanged:
`tests/test_sub_saturation_crop_undo.py` (`TestSyncGroups`),
`tests/test_cineon_log.py`, `tests/test_histogram_crop.py`,
`tests/test_curves.py`, `tests/test_color_profile.py`, `tests/test_area_editing.py`.
