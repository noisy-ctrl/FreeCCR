# Slider speed: render-then-show, off the GUI thread

## Problem (measured)

Profiling a 20-tick drag of the Contrast slider on a converted trichrome frame
(1080 × 809 preview, Cineon on, default sliders), with the real panel and
preview widgets:

| | |
|---|---|
| Per tick | ~310 ms, all on the GUI thread |
| Renders | 21 for 20 ticks |
| Display after the drag | the render from the **previous** tick |

Each tick ran `on_slider_changed` → `ImagePreview.update_preview(idx)`, which
**reads the cached preview first** and only then calls
`SlidersPanel.set_current_idx(idx)`, whose last line re-renders synchronously
(`ccr_backend.apply_adjustment_by_index`). So:

1. **The picture is always one tick behind.** The render a tick produces is
   only shown by the *next* tick. The 150 ms debounced "heavy" render after the
   drag re-renders the final state but never displays it, so the last value is
   never shown until something else refreshes the canvas. A single click on a
   slider track, an arrow key or a Printer Lights press changes nothing on
   screen at all. This is the "renderer gets stuck" report.
2. **The GUI thread is blocked for the whole render**, so the slider handle,
   value label and histogram stutter at the render rate.
3. Discrete edits that render and then call `update_preview` (Cineon toggle,
   reset, settle) render **twice**.

Render cost breakdown (container, 1080 px): OpenCL kernel on the pocl CPU
device ~190 ms (the numpy path is 96 ms for the whole preview here, and
bit-identical), Cineon decode ~37 ms, sharpening ~27 ms, Auto Gain
measurement ~12–20 ms, dtype conversions ~16 ms. A half-resolution render
of the same base costs ~68 ms. The Mac's GPU will differ; see *Measuring on
your machine*.

## Goals

- Every slider / curve / feather change ends with the exact final state on
  screen, rendered once.
- The GUI thread never renders during a drag: the handle and labels stay fluid
  and the picture updates as fast as renders complete (**latest wins** —
  intermediate values that arrive while a render is running are skipped, never
  queued).
- On slow machines, a reduced-resolution **draft** keeps a drag interactive;
  the full render lands as soon as the control goes idle.
- **No change to the rendered colour or tone.** The full render is the same
  `apply_adjustments` call with the same inputs; drafts differ only spatially
  (sharpening / chroma NR at a lower resolution), and only until the drag stops.

## Non-goals

- Rewriting the pipeline for the GPU, changing any stage, or changing export.
- The zoom/sharp-fit hi-res tile (it already renders off-thread after idle).
- Batch paths (load, convert-all, paste-to-many).

## Design

### 1. Pure render + apply split (`CCRImage`)

`update_thumbnail_and_preview()` becomes
`_apply_preview_pixels(render_preview_pixels(...))`:

- `render_preview_pixels(settings, areas, bases…, draft_long=None)` computes
  the 8-bit thumbnail, the 8-bit preview and the histogram counts **without
  touching the image**. Everything mutable it reads is passed in (snapshot),
  exactly like `HiResDetailWorker`.
- `_apply_preview_pixels(result)` stores them (pixmaps on the GUI thread,
  numpy stash off it, unchanged) and bumps `_render_epoch`.

The default call is bit-identical to today's.

A per-image `RLock` (`_render_lock`, created lazily in a module-level
`WeakKeyDictionary`, so `CCRImage` stays copyable) serialises renders of one
image: the live worker holds it while rendering; a synchronous render on the
GUI thread waits for the in-flight live render (at most one render) instead of
racing it on shared caches (dust plan, look cache).

### 2. `LiveRenderer` (`src/widgets/live_render.py`)

- `request(idx, draft=False)`: if a job is running, mark *pending* and return.
  Otherwise snapshot (on the GUI thread) the image's settings (deep copy),
  area layers (deep copy), base offsets, base curve, colour profile, window
  flag, crop, sprocket flags, the current `_render_epoch`, and start a
  `QThread` that calls `render_preview_pixels`.
- On completion (GUI thread, queued signal): drop the result if the image has
  left the list or its `_render_epoch` moved (a synchronous render landed
  meanwhile — it is at least as new). Otherwise apply the pixels and, if that
  image is on the canvas, refresh the canvas **without re-rendering**
  (`SlidersPanel.live_refresh` guard, below). Then, if pending, start the next
  job with the *then-current* settings.
- Drafts: never write the thumbnail (the sidebar keeps the last full render).
- `shutdown()` waits out the in-flight job (MainWindow.closeEvent), like
  `ImagePreview.shutdown_workers`.
- `last_full_ms`: duration of the last full render — the draft decision input.

### 3. Panel wiring

- `on_slider_changed`, `_on_curve_changed`, `_on_feather_changed`: store the
  settings (unchanged), then `self._live_request()` instead of
  `update_preview` + "heavy" debounce. The undo burst is unchanged.
- `_live_request` asks for a **draft** only when all hold: this tick is part of
  a burst (previous request < 300 ms ago), the last full render took
  > `DRAFT_THRESHOLD_MS` (70 ms), and the frame is draft-safe (no dust spots —
  the preview-scale heal caches its source plan and a different resolution
  would overwrite it). A single click / key press therefore always gets a full
  render.
- The existing 150 ms debounce timer now means *settle*: if the last applied
  result is a draft, or older than the latest request, request a full render;
  then refresh the sidebar thumbnail.
- `set_current_idx` gains a guard: during `live_refresh` (canvas refresh after
  a live result) it neither re-renders nor clears the pending/debounce state.
  All other callers keep today's behaviour.

### 4. Draft render

- Base downscaled with `INTER_AREA` to half the preview's long side (cached per
  base object), rendered with `auto_gain_override` = the Auto Gain measured on
  the full base (so exposure matches the full render exactly), and
  `sharpen_scale` = full / draft long side (same halo in frame terms).
- Result upscaled (`INTER_LINEAR`) back to the preview's exact size, so every
  overlay, crop and reference-frame mapping is untouched. Histogram from the
  draft.

### 5. Auto Gain cache

`compute_auto_gain_offset` depends only on the base pixels and the window
flag (and on crosstalk when it applies). The result is cached per image
against a weak reference to the base array + window flag + crosstalk values;
no cache when dust spots exist (the heal makes a fresh array every render).
Same value, ~12–20 ms less per render.

### 6. Discrete edits render once

The panel's own render-then-show sites (`_settle_preview`, the Cineon and
"Bypass until export" toggles, Reset) wrap their `update_preview` in the same
guard, so the duplicate render inside `set_current_idx` goes. Sites outside
the panel are untouched.

### 7. OpenCL device: opt-in overrides only

The default device choice is **unchanged** (`platforms[0].devices[0]`) — a
different device can round differently, and some older Mac GPU OpenCL drivers
are unreliable, so switching silently is not worth it without data. Instead:

- start-up prints every OpenCL device found, and which one is used;
- `FREECCR_OPENCL=0` forces the numpy path (bit-identical here);
- `FREECCR_OPENCL_DEVICE=gpu|cpu|<n>` picks a device by type or index.

`tools/bench_render.py` measures each option on the user's machine and
reports the largest difference from the numpy render, so a change of default
can be made from data.

### Measuring on your machine

`FREECCR_RENDER_TIMING=1` prints one line per live render:
`Live render: 84 ms (full, 1080x809)`. `tools/bench_render.py <raw files>`
times one preview render through OpenCL and numpy and the main stages, so a
slow machine can be diagnosed from a paste of its output.

## Test plan

- `render_preview_pixels` + `_apply_preview_pixels` equals the old
  `update_thumbnail_and_preview` byte-for-byte (preview, thumbnail, histogram)
  on a converted image with areas, curves and Cineon.
- Auto Gain cache returns exactly `compute_auto_gain_offset`, and misses when
  the base, window flag or crosstalk change.
- Slider tick → after the job completes the canvas pixmap equals a fresh
  synchronous render of the current settings (the old one-tick lag fails
  this).
- One render per burst step; ticks during a running job coalesce (N ticks →
  far fewer than N renders, last one = final settings).
- A synchronous render during an in-flight job wins (epoch): the late live
  result is discarded.
- Draft: only during bursts above the threshold, never with dust spots; settle
  produces a full render whose pixels equal a synchronous render.
- `set_current_idx` with the guard does not render and does not stop the
  debounce timer; without it, unchanged.
- Device selection: default unchanged; `gpu`/`cpu`/index overrides pick the
  right device (fake platform objects); `FREECCR_OPENCL=0` disables OpenCL.
- Existing suite passes.

## Refinement notes

- No drafts until one full render has been timed (`last_full_ms` is None at
  start), so the first move on a frame is always full.
- A result that is older than a pending request is still applied (it is newer
  than what is on screen); the pending job then starts with the settings of
  *that* moment, not of the tick that queued it.
- The guard is a counter, not a flag, so a nested refresh cannot clear it
  early.
- The live worker reads `resized_raw`, `sprocket_alpha`, `dust_spots`,
  `conversion_inputs` and `converted` live. Each of those only changes through
  operations that also render synchronously, which bumps the epoch and voids
  the late result.
- Area layers are draft-safe (their masks are normalised, and the hi-res
  worker already renders them at other resolutions); dust spots are not.
