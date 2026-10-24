# Spec: Tethering (Watch-Folder Capture)

> **Renamed 2026-10-24: "Hot Folder Tether".** The File menu item is **Hot Folder Tether…** / **Stop Hot Folder Tether**, the banner reads "Hot folder tether: “folder” · N captured", and the messages say "Hot folder tether". It watches a folder that the camera's own tether app saves into; the name says so. Code identifiers (`tether_*`) are unchanged.


Status: REFINED v2
Owner: FreeCCR
Feature branch: `feature/tethering-watch-folder`

## 1. Summary

A **Tethering** mode (File menu) that **watches a user-chosen folder**. The user's
own camera-tethering software (Canon EOS Utility, Nikon NX Tether, Sony Imaging
Edge Remote, Fujifilm X Acquire, …) — or an SD-card auto-offload — writes each
capture into that folder. FreeCCR detects every new file, imports it as a
`CCRImage` (catalog-aware), **auto-converts it to a positive using the saved B/W
point**, appends it to the thumbnail sidebar, and shows it **large on the canvas**.

This delivers the spirit of "see the positive as you shoot" plus fast frame
triage, with **zero camera-SDK dependencies** and identical, trivial setup on
macOS and Windows.

### Why watch-folder instead of direct USB control

Decision is research-backed (see `experiments/`/PR discussion):

- Camera-scanning film is a **static copy-stand** workflow: focus, exposure and
  alignment are set **once per roll**, then the roll is shot in minutes. Live
  view is a one-time *setup* aid, not a per-shot need.
- The market-leading converter (Negative Lab Pro) has **no live preview**;
  import-then-convert is the norm. Even Adobe Lightroom **disables camera live
  view over USB tether**.
- People who want a live framing view run the **camera vendor's app feeding a
  watched/hot folder** — exactly this pattern (Lightroom "Auto Import", Capture
  One "Hot Folder"). It is the industry standard and identical across OSes.
- Direct in-app control (libgphoto2 / vendor SDKs) is high-friction
  (per-camera Zadig/WinUSB on Windows, SDK registration + redistribution limits,
  a per-vendor binding to maintain) for a benefit that this use case barely uses.

FreeCCR therefore competes on **conversion quality**, not on live-ness.

## 2. Goals / Non-goals

### Goals
- `File ▸ Tethering…` opens a folder picker, then enters a non-modal **watch mode**.
- Detect **new** supported image files appearing in the folder *after* start,
  robust to partial writes (a file mid-download is not picked up early).
- Import each new file as a `CCRImage` (with catalog restore), **appended** to the
  current batch.
- **Auto-convert** each capture to a positive with the saved global B/W point
  when one is available and Positive mode is off; otherwise import it unconverted.
- **Show each capture large** on the canvas and select it in the sidebar (triage).
- **Persist the global B/W point across sessions** (QSettings) and reuse it for
  tethering. This is a small app-wide improvement, not tethering-only.
- **Sample / refine the B/W point from a displayed capture** via the existing
  eyedropper buttons; subsequent captures use the refined point automatically.
- A slim **non-modal status banner** ("Watching <folder> — N captured") with a
  **Stop** control; the File-menu action toggles to "Stop Tethering".
- Clean teardown on Stop and on app close; **debounced** catalog saves.
- Respect **Positive mode** (skip B/W conversion when on — the capture is already
  a positive).
- **No new third-party dependency** — built on Qt + the existing pipeline.

### Non-goals
- **No direct USB camera control, live view, or remote shutter** (no
  gphoto2 / vendor SDK / PTP). Explicitly out of scope (researched as
  high-friction, low-value for this app).
- No true 30 fps live view; cadence is **per capture** (≈1–2 s after a file
  lands), not per video frame.
- No in-app camera settings (ISO/shutter/AF/WB) — the vendor app owns capture.
- No automatic **re-conversion of already-imported captures** when the B/W point
  later changes (the existing "Convert Current / Convert All (B/W Point)" buttons
  cover that manually; a future enhancement may automate it).
- No `watchdog`/inotify dependency; detection is a Qt timer poll (§5.1).
- No video-file ingestion; only the existing supported still formats.

## 3. UX / Interaction

### 3.1 Entry
A new `File ▸ Tethering…` action, inserted **after `Open Folder`** and before the
Export separator (`main_window.create_menu`, ~`main_window.py:254`). On trigger:

1. **Persist the current batch first** (`ccr_backend.save_catalog()`), mirroring
   `open_files`/`open_folder`.
2. Folder picker: `QFileDialog.getExistingDirectory`, seeded from
   `_settings.value("files/last_tether_dir", "")`. Cancel → no-op.
3. `normalize_unicode_path` + `validate_unicode_path`; on invalid path, warn and
   abort (reuse the `open_folder` validation block).
4. Persist `files/last_tether_dir`.
5. **Enter watch mode** (§5). The current batch is **kept**; captures append to it.
6. **Seed the seen-set** with the folder's current contents — pre-existing files
   are **not** imported (only files that appear *after* start). (v2 open question
   §9: optional "import existing too").

### 3.2 Watch-mode state & banner
A slim banner strip across the **top of the image-preview column** (above the
canvas), shown only while watching:

```
●  Tethering — “Roll_2026-06-16”   ·   7 captured        [ Stop ]
```

- Left: a red dot + the watched folder's display name + capture count.
- A secondary line/tooltip when no B/W point is set:
  *"No B/W point set — captures import unconverted. Set Black & White points to
  auto-convert."*  When Positive mode is on: *"Positive mode — captures import as
  positives."*
- Right: a **Stop** button.
- Non-modal: the user can still select, adjust, crop, cull and export any
  captured (or previously loaded) image while watching.

The `File ▸ Tethering…` action's text toggles to **`Stop Tethering`** while active.

### 3.3 Capture flow (per new file)
Detect (poll, §5.1) → size-stability gate (§5.2) → off-thread decode + import
(§5.4) → optional auto-convert (§5.4) → append to backend + sidebar, **select +
show big** (§5.5) → transient hint *"Captured <name>"*. A file that fails to
decode is **skipped** with a hint; the watch loop continues.

### 3.4 B/W point sourcing (decision: persist + sample from a capture)
- The global B/W point (`ccr_backend.black_point_bgr/white_point_bgr`) is
  **persisted to QSettings** and **restored at startup**. Watch mode reuses it.
- **If set:** every capture auto-converts.
- **If unset:** captures import as unconverted negatives; the banner hints to set
  points.
- **Sample from a capture:** the existing **"Set White Point" / "Set Black Point"**
  buttons already operate on the displayed image and call
  `ccr_backend.set_white_point/set_black_point` (`sliders_panel.py:1327-1349`,
  `image_preview.py:402-410`). Since a capture is a normal displayed `CCRImage`,
  the user samples directly on it. After both points are set, **subsequent**
  captures auto-convert. (Already-imported captures are not retro-converted in
  v1 — §2 non-goals.)

> **Accuracy note (important).** Most vendor apps drop an **8-bit sRGB JPEG** (or a
> RAW) into the folder. The B/W-point math assumes the same value space as the
> sampled scan. When the capture is a RAW it decodes through the normal pipeline
> and converts faithfully. When it is a gamma-encoded JPEG, the live conversion is
> a **good framing/triage preview**, not a color-accurate result — sampling the
> B/W point *from a capture of the same format* (§3.4) makes the on-screen
> conversion self-consistent. This is acceptable: the canvas is a triage view; the
> archival conversion is whatever the user finalizes per image.

### 3.5 Stop
Stop via the banner button, the `Stop Tethering` menu item, or app close. Teardown
(§5.5): stop the poll timer, quit + join the worker thread, hide the banner, do a
final catalog save, restore the menu text.

### 3.6 Errors / edge cases (UX-visible)
- Decode failure on a file → skip + hint, keep watching.
- Watched folder disappears (drive unplugged) → pause + hint; resume when it
  reappears.
- App close while watching → clean teardown before the catalog save in
  `closeEvent`.

## 4. Data model

### 4.1 Global B/W point persistence (new)
Mirror the existing global B/W point to QSettings, owned by the UI layer
(`MainWindow._settings`, consistent with `import/positive_mode` and
`import/input_icc_path`):

```
QSettings keys (JSON-encoded list[float] of length 3, B,G,R):
  convert/black_point_bgr   e.g. "[51234.0, 48010.5, 52001.2]"
  convert/white_point_bgr
```

- **Restore** at `MainWindow.__init__` (before any image loads, like positive
  mode): parse → `ccr_backend.set_black_point(...)/set_white_point(...)`.
- **Persist** whenever a point is sampled: hook `on_bwpoint_sampled`
  (`sliders_panel.py:1339`) to also write the QSettings keys (via the main
  window). Malformed/absent → treated as unset.

### 4.2 Watch-session state (transient — on `MainWindow`, not persisted)
- `_tether_folder: str` — watched directory.
- `_tether_seen: set[str]` — basenames already handled (seeded at start).
- `_tether_sizes: dict[str, int]` — per-file last-seen size, for the stability gate.
- `_tether_count: int` — captures imported this session.
- `_tether_timer: QTimer` — the poller.
- `_tether_thread: QThread`, `_tether_worker: TetherWatchWorker`.
- `_tether_active: bool`.

### 4.3 Captured images
Captures are **ordinary `CCRImage`s** persisted by the existing catalog exactly
like any imported file. When auto-converted, `conversion_inputs = {"mode": "bw",
"bw": ((B,G,R),(B,G,R)), "fine_rot": …}` is set (mirroring
`apply_bwpoint_to_all_images`, `ccr_backend.py:1358-1362`), so reopening the app
restores the conversion. The file physically lives in the user's chosen folder;
the catalog references it by path as usual.

### 4.4 QSettings (UI prefs)
- `files/last_tether_dir: str` — last watched folder.

## 5. Processing / detection

### 5.1 Detection — poll timer (GUI thread)
A `QTimer` on the GUI thread, interval **1000 ms**. Each tick:

1. `os.scandir(folder)` (Windows-Unicode-safe; already used in
   `ccr_backend.py:157`). On `FileNotFoundError`/`OSError` → folder unavailable:
   set a "paused" banner state, keep ticking.
2. Filter entries by the **supported extension set** (reuse the `open_files`
   filter list — `.dng .tif .tiff .arw .nef .cr2 .cr3 .raf .png .jpg .jpeg .rw2
   .3fr .fff` + the broader folder set), case-insensitive.
3. For each candidate not in `_tether_seen`:
   - record `size = entry.stat().st_size`.
   - **ready** iff `size > 0` **and** `size == _tether_sizes.get(name)` (i.e.
     unchanged since the previous tick — see §5.2). Update `_tether_sizes`.
   - When ready: add `name` to `_tether_seen` and **enqueue** the full path to the
     worker (queued signal, §5.4).

Polling at 1 s is far more reliable than `QFileSystemWatcher` for this cadence
(captures arrive seconds apart) and avoids its known "directory changed but which
file?" and missed-event issues. (`QFileSystemWatcher` as an *accelerator* is a
v2 open question, §9.)

### 5.2 Partial-write safety
The **size-stability gate** (§5.1): a file is dispatched only after its size is
unchanged across one poll interval, so a still-downloading capture is held until
complete. The worker additionally wraps the decode in try/except (truncated/locked
file → skip, leave it out of `_tether_seen` so a later tick can retry once stable).

### 5.3 RAW + JPEG pairs
When a camera shoots RAW+JPEG it writes two files per shot. v1 imports **every**
ready supported file (documented). v2 refinement (§9): when both a RAW and a JPEG
of the **same basename** become ready in a session, prefer the RAW and skip the
JPEG sibling.

### 5.4 Processing — worker thread (serial queue)
A long-lived `TetherWatchWorker(QObject)` moved onto a `QThread` **with an event
loop** (so queued slots run off the GUI thread). The poller emits `fileReady(path)`
connected (`Qt.QueuedConnection`) to `worker.process(path)`. `process`:

1. `imgs = create_images_for_path(path, catalog_data=load_catalog())`
   (catalog-aware single-file load, the same primitive
   `load_images_from_files` uses per file — `ccr_backend.py:78`, `catalog.py:307`).
   *(Catalog is read once and cached on the worker; re-read only if stale.)*
2. For each produced `CCRImage img` (normally one; a previously-sliced file could
   yield several):
   - If **Positive mode off** *and* a B/W point is set:
     `processed = ccr_normalize_with_bwpoint(img, black, white)`;
     `img.resized_raw = processed`; `img.converted = True`;
     `img.conversion_inputs = {...bw...}`; `img.update_thumbnail_and_preview()`.
     (Exactly mirrors `apply_bwpoint_to_all_images`, which already runs in a
     worker thread and builds the preview QPixmap there — established pattern.)
   - Else import as-is (Positive mode bakes the positive at decode; or unconverted
     negative when no B/W point).
   - Emit `captured(img)`.
3. On exception → emit `captureError(path, str(e))`.

**Threading note.** Unlike the one-shot `ImageLoaderWorker`
(`main_window.py:83`, which blocks in `run()` and emits `finished`), this worker is
**long-lived**: `thread.start()` runs its event loop, the worker is `moveToThread`'d,
and work arrives via the queued `fileReady → process` connection. Files are
processed **serially** on that one thread, so multiple rapid captures queue and the
GUI stays responsive.

### 5.5 GUI integration — `captured` slot (GUI thread)
1. Append `img` to `ccr_backend.images` and `ccr_backend.file_paths` **on the GUI
   thread** (the lists are lock-free and assume the GUI thread is the writer —
   `ccr_backend.py:34-35`).
2. `thumbnail_list.append_image_item(idx, select=True)` (new helper, §6) — adds one
   `QListWidgetItem` with `Qt.UserRole = idx` and selects it. Selecting fires
   `on_current_item_changed` → `image_preview.update_preview(idx)` +
   `sliders_panel.set_current_idx(idx)` → the capture shows **big** on the canvas.
3. `_tether_count += 1`; update the banner; `set_temporary_hint("Captured <name>")`.
4. **Debounced catalog save** (coalesce to ~once / 3 s via a `QTimer`; also save on
   Stop/close) — avoids disk thrash on bursts (hazard from backend research).

`captureError` slot → `set_temporary_hint("Skipped <name>: <msg>")`.

### 5.6 Conversion math
Unchanged. `ccr_normalize_with_bwpoint` / `apply_bwpoint_normalization`
(`ccr_processor.py:1001`, `:1541`) already perform the per-channel linear
anchor map (`(v - white)/(black - white)·65535`, clip) + neutral inversion, with
the post-invert "look" disabled. Reused verbatim.

### 5.7 Positive mode
`CCRImage` reads `ccr_backend.positive_mode` at decode time, so a capture imported
while Positive mode is on is already a positive. In that case **skip** the B/W
conversion entirely. The flag is read **per capture** (snapshot at `process`
start) to avoid mid-decode inconsistency.

## 6. Integration points

| Location | Change |
|---|---|
| `main_window.create_menu` (~`:254`) | Add `File ▸ Tethering…` action after `Open Folder`; keep a handle to toggle its text. |
| `main_window` (new) | `enter_tethering()` (save catalog, folder picker, validate, persist dir, seed seen-set, start poller+worker, show banner, toggle menu text). |
| `main_window` (new) | `stop_tethering()` (stop timer, quit+join worker, hide banner, final save, restore menu text). `_cleanup_tether()` nulls handles (mirror `_cleanup_loader`). |
| `main_window` (new) | `_poll_tether_folder()` (the §5.1 tick). |
| `main_window` (new) | `_on_capture(img)` / `_on_capture_error(path,msg)` (the §5.5 slots). |
| `main_window.__init__` | Restore persisted B/W point into `ccr_backend` (before image loads); create the (hidden) banner; init `_tether_*` state. |
| `main_window.closeEvent` (`:196`) | `stop_tethering()` **before** `ccr_backend.save_catalog()`. |
| `sliders_panel.on_bwpoint_sampled` (`:1339`) | Also persist the sampled B/W point to QSettings (via the main window). |
| `thumbnail_list` (new) | `append_image_item(idx, select=True)` — append one item without a full `load_thumbnails()` rebuild. |
| **new** `src/core/tether_watcher.py` | `TetherWatchWorker(QObject)` (+ a small extension-filter helper). Pure Qt + existing pipeline; no third-party deps. |
| `catalog.py` | **No change** — captures persist exactly like normal imported files. |

## 7. Files touched / added

- **add** `src/core/tether_watcher.py` — `TetherWatchWorker` (long-lived QThread
  worker; `process(path)`, `captured`/`captureError` signals).
- **add** `src/widgets/tether_banner.py` *(or inline in `main_window`)* — the slim
  watch-status banner with a Stop button.
- **edit** `src/ui/main_window.py` — menu action + enter/stop/poll/slots, B/W
  restore + banner, `closeEvent` teardown.
- **edit** `src/widgets/thumbnail_list.py` — `append_image_item`.
- **edit** `src/widgets/image_preview.py` — `set_tether_banner` /
  `show_tether_banner` / `hide_tether_banner` helpers (insert the banner into the
  existing internal `QVBoxLayout` at index 0; no reparenting — see §9.1.5).
- **edit** `src/widgets/sliders_panel.py` — persist B/W point on sample; banner
  hint text.
- **add** `tests/test_tether_watcher.py` — detection/stability/dedupe, processing
  (convert vs unconverted vs positive-mode), B/W persistence round-trip, append.

## 8. Test plan

### Unit (headless; no camera, minimal Qt)
- **Scanner / stability:** a file is dispatched only after its size is stable
  across two ticks; a growing file is held; pre-existing files (seeded set) are
  ignored; unsupported extensions ignored.
- **RAW+JPEG dedupe** (if v2 §5.3 lands): same-basename RAW preferred over JPEG.
- **Processing:** a sample negative → converted `CCRImage` with correct
  `conversion_inputs` when a B/W point is set; unconverted when unset;
  Positive-mode path skips conversion.
- **B/W persistence:** set points → QSettings round-trip → restore yields equal
  tuples; malformed/missing → unset (`None`).
- **Backend append:** `_on_capture` appends exactly one image; `images` and
  `file_paths` stay in sync; index/`UserRole` mapping correct.

### Manual
- `File ▸ Tethering…`, pick a folder; copy a supported file in → it appears
  converted, **big on the canvas**, selected in the sidebar within ~1–2 s.
- Drop several files quickly → all import serially; UI stays responsive.
- No B/W point → imports unconverted + hint; sample on a capture → next drop
  auto-converts.
- Positive mode on → captures import as positives (no B/W conversion).
- Stop → banner hides, watching stops. Reopen the app → captures + conversions
  restored from catalog; B/W point restored.
- Close the app while watching → clean exit, catalog saved, no dangling thread.
- Unplug / rename the watched folder → graceful pause + hint; restore on return.

## 9. Refinement (v2) — resolved decisions & added detail

### 9.1 Resolved open questions
1. **Append, don't replace.** Entering tethering **keeps** the current batch and
   appends captures to it (least destructive; lets a user keep a loaded roll
   visible). `ccr_backend.save_catalog()` is still called first, mirroring the
   open flows, so prior edits are persisted before the session grows.
2. **Pre-existing files: prompt once.** On entry, if the folder already contains
   ≥1 supported file, show a `QMessageBox.question`: *"This folder already
   contains N supported image(s). Import them too?"* **Yes** → enqueue them
   through the normal capture path (oldest-mtime first); **No** → seed the
   seen-set with them so only *new* files import. Either way the seen-set ends up
   containing every current file, so nothing is imported twice.
3. **Pure 1 s poll** in v1 (no `QFileSystemWatcher`). At capture cadence (seconds
   apart) a 1 s `os.scandir` poll is reliable and simple; the watcher's
   "something changed / which file?" semantics and missed-event quirks aren't
   worth it. Listed as a future *accelerator* only.
4. **RAW+JPEG dedupe (light, in v1).** Track handled **basenames**. RAW
   extensions (`.dng .arw .nef .cr2 .cr3 .raf .rw2 .3fr .fff .orf .pef .srw …`)
   are preferred over JPEG/PNG. Rule: when a file becomes ready, if a sibling of
   the **same basename** has already been handled, skip the newcomer **unless**
   the newcomer is RAW and the handled one was a non-RAW that has *not yet been
   imported in this tick* — in practice we resolve **within a tick** (same
   basename, both ready: import the RAW, drop the JPEG) and document the
   cross-tick case (JPEG ready first, RAW a tick later → both import) as a known
   v1 limitation. No retroactive removal of an already-imported sibling in v1.
5. **Banner = slim top strip, inserted INSIDE `ImagePreview`'s own layout.**
   ⚠️ **Do not wrap `image_preview` in a new container.** `ImagePreview` and its
   inner `GraphicsImageView` reach the main window via `self.parent().parent()`
   and `pw.parent().parent()` (`pw = self.parent_widget`, i.e. the `ImagePreview`)
   in ~15 places (WB/eyedropper sampling, B/W sampling, histogram, thumbnail
   refresh, hints — `image_preview.py:320, 410, 459, 972, 1002, 1012, 1231, 1241,
   1704, 1965, 1980, 2494, 2538, 2836, 3046…`). The chain works because
   `ImagePreview.parent()` is `central_widget` and `.parent()` is `MainWindow`.
   Inserting an extra container level would make `parent().parent()` resolve to
   `central_widget` and **break all of them**.
   Instead, the banner is added to `ImagePreview`'s **existing internal**
   `QVBoxLayout` (`self.layout`, currently `[toolbar, view, rotation_slider]`,
   `image_preview.py:587, 728`) at **index 0** via `self.layout.insertWidget(0,
   banner)`. The banner becomes a child of `ImagePreview` (so `ImagePreview`'s own
   parent is unchanged and every `parent().parent()` chain stays valid), and it
   renders as a strip above the toolbar. Implemented as a tiny
   `ImagePreview.set_tether_banner(widget)` / `show_tether_banner(folder, count,
   note)` / `hide_tether_banner()` helper so `MainWindow` doesn't reach into
   `image_preview.layout` directly. `TetherBanner` is a `QWidget` (red-dot label +
   status `QLabel` + **Stop** `QPushButton`, `stopRequested` signal),
   `setVisible(False)` by default.
6. **Debounced catalog save = 3000 ms**, single-shot, restarted on each capture;
   plus an **unconditional** save on Stop and in `closeEvent`. (One `QTimer`,
   `setSingleShot(True)`.)
7. **Worker = one long-lived event-loop `QThread`.** `self._tether_thread =
   QThread(); self._tether_worker = TetherWatchWorker(); worker.moveToThread(thread);
   thread.start()` (no `started→run` blocking connection — the thread runs its
   **event loop**). Work is delivered by a **queued** connection
   (`self._fileReady.connect(worker.process, Qt.QueuedConnection)` or
   `QMetaObject.invokeMethod(worker, "process", Qt.QueuedConnection, path)`), so
   `process` executes on the worker thread; files queue and run **serially**.
   `captured`/`captureError` are emitted back and delivered to GUI slots on the
   GUI thread (auto/queued). Shutdown: `worker.cancel()` (sets a flag the
   in-flight `process` can check between images), `thread.quit()`,
   `thread.wait(3000)`, then null the handles in `_cleanup_tether` — exactly the
   `_stop_loader_if_running`/`_cleanup_loader` shape (`main_window.py:437-454`).
8. **Stop affordances = banner button + menu toggle** in v1. The File-menu action
   flips between `Tethering…` and `Stop Tethering`; the banner carries a **Stop**
   button. No `Esc`/global-key binding in v1 (avoids the existing SPACE/arrow
   focus interactions in the canvas).

### 9.2 Tightened integration detail
- **QPixmap-off-GUI-thread is the established pattern here.**
  `CCRImage.__init__` → `update_thumbnail_and_preview` builds `QPixmap`
  (`ccr_image.py:605`, `:522`), and the existing **batch loader runs that on a
  worker `QThread`** (`ImageLoaderWorker` → `load_images_from_files` →
  `create_images_for_path` → `CCRImage(...)`), as does `BWPointConvertWorker`.
  `TetherWatchWorker.process` building the preview/conversion off-thread is
  therefore **consistent with existing code**, not a new hazard. The one rule kept
  strict: **list mutation** (`ccr_backend.images/.file_paths.append`) and **all
  thumbnail-widget / canvas calls** happen only in the GUI-thread `captured` slot
  (§5.5), never in the worker.
- **Persisting the B/W point from the panel.** `on_bwpoint_sampled`
  (`sliders_panel.py:1339`) reaches the main window via `self.parent().parent()`
  (the same access `_on_convert_current_bwpoint` uses, `:1377`); call a new
  `MainWindow.persist_bwpoint()` (guarded by `hasattr`) that JSON-encodes
  `ccr_backend.black_point_bgr/white_point_bgr` into the §4.1 QSettings keys.
  Restoration at `MainWindow.__init__` decodes them back via
  `ccr_backend.set_black_point/set_white_point` before any image loads.
- **Selecting the new capture.** `thumbnail_list.append_image_item(idx,
  select=True)` adds one `QListWidgetItem` (`text=display_name or basename`,
  `Qt.UserRole=idx`, transformed icon via `apply_frontend_transformations`) and,
  when `select`, `thumbnail_list.setCurrentRow(idx)` — which fires
  `on_current_item_changed` → `image_preview.update_preview(idx)` +
  `sliders_panel.set_current_idx(idx)`. No full `load_thumbnails()` rebuild per
  capture. Guard against the `_rebuilding` re-entrancy flag the list already uses.
- **Folder-unavailable handling.** A poll tick whose `os.scandir` raises
  `OSError` sets a banner "paused — folder unavailable" state and keeps ticking;
  the next successful tick clears it. No worker involvement.
- **Catalog read in the worker.** `process` loads the catalog once and caches it
  on the worker, re-reading only if its mtime changed, so per-capture imports
  don't re-parse the JSON each time (mirrors the "read catalog once per batch"
  optimization in `load_images_from_files`, `ccr_backend.py:63-69`).

### 9.4 Post-review hardening (applied during implementation)
An adversarial review of the implementation surfaced these fixes, now in code:
- **Seen-set pruning.** `FolderScanner.feed` forgets files that have left the
  folder (`seen &= present`), so a **delete-then-recreate of the same filename**
  (retaking a bad shot) re-imports instead of being blocked forever.
- **Duplicate-name coalescing.** A listing that reports the same name twice (some
  network/symlink filesystems) is coalesced before the stability gate.
- **No lost capture on Stop.** `_on_capture` always appends the image (and
  persists immediately if the session already stopped) — a capture that was
  in-flight when the user hit Stop is kept, not dropped.
- **Catalog mtime-cache** on the worker (§9.2) is implemented, not per-call reload.
- **`positive_mode` is snapshotted** alongside the B/W point at `process()` start.
- **Folder-unavailable banner clears** on reconnect (a `_tether_unavailable` flag).
- **Timer lifecycle:** `_tether_save_timer` is nulled on Stop; no `deleteLater`
  on the worker after the thread's loop has exited (refs dropped, GC releases it).
- **`append_image_item` returns bool** + logs on an invalid index; a skipped add
  (rebuild in flight) self-heals on the next full `load_thumbnails`.

### 9.5 Follow-up fixes (post-merge feedback)
- **Banner theme.** The status strip now matches FreeCCR's default light theme:
  a soft amber active-session bar (`#fff3cd`), a red recording dot, and a
  white-on-red **Stop** button (was a dark `#2b2b2b` strip with an
  invisible-on-dark default button).
- **Immediate B/W status.** Sampling a Black/White point now refreshes the
  banner note at once (`MainWindow.refresh_tether_banner`, called from
  `persist_bwpoint`), so the "captures import unconverted" warning clears the
  moment both points are set — no waiting for the next capture/poll.
- **Inherited look on capture.** Each new capture inherits the editable look —
  `adjustment_settings`, coarse/fine rotation, flips, crop, color profile, and
  area layers — of the image active when it arrived (`_tether_template` /
  `_apply_tether_template`, snapshotted in `_on_capture` before the new image is
  selected). A rotate/brightness/etc. set on one frame thus auto-applies to the
  next imported frame, which a fixed copy-stand rig wants. The template chains
  forward (each capture becomes the source for the next) and is a deep,
  isolated copy. Skipped when the active image's look is all-default (no wasted
  re-render).

### 9.6 Per-session B/W-point reset + prompt (film-lead workflow)
Every tethering session **clears the global B/W point** on entry and re-samples
it from the roll's own **film lead** — different rolls/stocks have different base
colour and density, so a previous roll's point must not carry over. This
supersedes the original "persist + reuse" choice **for tethering**; the persisted
point (QSettings) still benefits the normal, non-tethering convert workflow and
is restored at startup, but `enter_tethering` resets the in-memory point to
`None` so each session starts uncalibrated.

- `enter_tethering` sets `ccr_backend.black_point_bgr/white_point_bgr = None`.
  Captures import **unconverted** until the user samples (the banner note says
  so).
- After the **first genuinely-new capture** of the session (the film lead),
  FreeCCR prompts once (`_tether_prompted`, reset per session) to set the roll's
  B/W point from it. Files imported via the "import existing?" prompt are
  excluded (`_tether_import_existing`); only a poll-detected frame counts as the
  lead.
- The dialog always offers **Set Black Point** / **Skip** (no "Keep Existing" —
  the point was just cleared). Choosing Set Black Point re-selects the lead frame
  (in case a later capture stole the selection while the dialog was open) and
  enters Black-point sampling; the existing flow then guides the user to the
  White point. Once both are set, `persist_bwpoint` → `refresh_tether_banner`
  clears the note and subsequent captures auto-convert.
- The modal opens via `QTimer.singleShot(0, …)` so it never blocks the capture
  slot (and never hangs a headless test, which doesn't spin the loop).

### 9.3 Out-of-scope confirmations (unchanged from §2)
Direct USB camera control / live view / remote shutter, 30 fps video, in-app
camera settings, retroactive re-conversion on B/W-point change, and a
`watchdog`/`QFileSystemWatcher` dependency all remain **non-goals** for this
change.
