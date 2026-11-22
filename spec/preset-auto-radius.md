# A film preset can measure each frame's sharpening radius

The user, 2026-11-22 ("Auto sharpening radius per frame in film presets …
< Yes please"). Details → Radius has an **Auto** button
(`spec/auto-sharpen-radius.md`) that sizes the radius to the frame's own
grain. Its spec left "remembering a radius per film stock" for later. The
grain's size in pixels depends on the film, its push, the format and the
scan, so a fixed radius in a preset is right for one setup only; measuring
each frame is what Auto already does, so a preset can ask for it.

## Goals and non-goals

- A film preset has **Auto radius** (off by default): its frames get Auto
  Radius measured on each of them when the preset's settings reach them.
- It runs in the background, one frame at a time, never blocking the
  window, and says what it did.
- A radius the user has set by hand since the frame was queued is never
  overwritten.
- Non-goals: Amount and Masking stay the preset's (or the frame's); no
  change to how Auto measures.

## Data (`core/film_presets.py`)

- Preset key `auto_radius` (bool; `clean` keeps it, `make(auto_radius=)`,
  New Version copies it). `set_auto_radius(presets, pid, on)`,
  `auto_radius_of(p)`. `summary` adds "Auto radius".
- A preset with Auto radius and nothing else can still be applied (like a
  preset of flats only).

## When it runs

- **Apply Preset to This / the N Selected Frames** (`apply_preset_to`):
  after the paste and the marking, the frames are queued.
- **A tethered capture** in a roll with that preset (`MainWindow._on_capture`
  sets `img.film_preset`): queued once it is in the list.
- Not on Write Preset (the frame written from keeps its radius: it is the
  one the preset was made from), not when the film menu only marks frames
  (no settings are pasted there).

## The queue (`SlidersPanel.queue_auto_radius(images)`)

- Each image is queued with its radius at that moment; an image already
  queued isn't added twice. One `AutoRadiusWorker` at a time
  (`_radius_queue_worker`, separate from the Auto button's); with
  `_auto_radius_threaded` False the measurements run inline (tests).
- On each result: when the image is still loaded, the measurement gave a
  radius, and the frame's radius is still the one it was queued with: one
  undo snapshot, `sharpen_radius` set; the frame on screen gets its slider
  moved and is rendered; another frame is re-rendered and its thumbnail
  refreshed. Otherwise nothing changes for it.
- A hint while it works ("Auto Radius: measuring 2 of 5…") and at the end
  ("Auto Radius set on 4 frames (radius 31–38); 1 kept its own."). The
  catalogue is saved at the end.

## UI

- Settings → Film Presets: a check box under Film Look, "Measure each
  frame's sharpening radius (Auto)", with a tooltip saying when it runs.
  Saved as it is ticked, like the rest of the page.

## Tests (`tests/test_preset_auto_radius.py`)

- The key survives clean / normalise / New Version; `summary` names it;
  `set_auto_radius` round-trips.
- Apply Preset with Auto radius queues the frames and sets each one's
  radius from a stubbed measurement (one undo step each); a frame whose
  radius changed by hand meanwhile keeps it; a failed measurement changes
  nothing.
- Without Auto radius nothing is queued.
- A tethered capture of such a preset is queued.
- The page's box reads and writes the preset.
