# The background detail render gives way to you

## 1. Problem

Scrolling the thumbnail list stutters, worst just after clicking a frame.

Clicking a frame starts the detail render (`HiResDetailWorker`) for the sharp
fitted view on a high-DPI screen (`spec/sharp-fit-preview.md`): for a
trichrome frame that is three RAW re-decodes, the conversion replayed, and the
adjustments rendered at ~2,450 px. Measured on the three trichrome frames
sent (Retina simulated): 5–9 s per click, and the render for a frame you have
already left runs to the end anyway, so clicking through a roll stacks them
up. The adjustment pass runs through OpenCL, which on a Mac is the GPU that
also draws the screen; when the result lands, the GUI thread converts and
swaps in a 2,448 x 1,835 image (and the scopes re-capture it). In the
sandbox (CPU OpenCL, no real display) the GUI thread itself stalled only
20–47 ms, so the stutter on the Mac is the render competing for the machine
while you scroll rather than the GUI thread being blocked by Python.

## 2. Goals / non-goals

- While you are scrolling the list or moving between frames, the detail
  render does not start, pauses at its next safe point, and does not swap its
  result in.
- A render for a frame you have left stops at its next safe point.
- Nothing changes when you are not interacting: the sharp view still arrives,
  just after a short quiet moment.
- Zooming in still renders straight away (you asked for detail); it pauses
  while you scroll, like the fitted one.

Non-goals: making the render itself faster; cancelling a single RAW decode or
the OpenCL pass mid-way (neither can be interrupted from Python); the
preview cache (separate).

## 3. Design

`core/ui_activity.py` (Qt-free): `touch()` stamps "the user just did
something", `busy(quiet=0.4)` is True within 0.4 s of it, `idle_for()`.
Touched by: the thumbnail list's scroll bar moving, and the current frame
changing.

- **Delayed start.** `ImagePreview._maybe_request_hires` defers a request
  that would start a fresh decode for the *fitted* view (not zoom) until the
  UI has been quiet for `HIRES_QUIET_S` = 0.6 s, re-checking on a single-shot
  timer; the retry re-validates everything, so a frame you moved past never
  starts.
- **Safe points.** The worker binds `load_progress` (which the decode already
  calls after reading the file, after decoding it, and after each RAW of a
  merge) to a checkpoint, and checks again before the adjustment pass. At a
  checkpoint it raises `load_progress.Interrupted` if interruption was
  requested (the worker ends quietly), and sleeps in 40 ms steps while the UI
  is busy. `load_progress.step` re-raises `Interrupted` (it swallows anything
  else, as before, so a progress callback still can't break a load).
  `Interrupted` is a `BaseException`: the RAW decode wraps itself in
  `except Exception` (logging a failed read and setting `last_read_error`),
  and a deliberate stop must pass straight through that.
- **Frames left behind.** When the preview switches to another image, every
  in-flight worker for a different image is asked to stop.
- **Quiet landing.** `_on_hires_ready` holds a result while the UI is busy
  and applies it (re-validated) once it is quiet.
- The worker runs at `QThread.LowPriority`.

## 4. Tests

- `ui_activity`: busy right after `touch`, quiet after the window.
- `load_progress.step` re-raises `Interrupted`, still swallows other errors.
- The worker's checkpoint waits while busy, returns once quiet, and raises
  when interrupted; a worker interrupted mid-decode emits nothing.
- A fitted-view request is deferred while busy and starts once quiet; a
  zoomed request starts at once.
- Switching image interrupts workers for other images, not the current one's.
- A result arriving while busy is applied only after the UI goes quiet.
- The thumbnail list's scroll bar and selection touch the clock.
