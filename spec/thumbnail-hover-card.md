# The frame list's hover card: shutter speeds and RAW clipping

The user, 2026-11-23: "When hovering over the thumbnail however, could it display
shutter speeds and whether or not a channel is clipped in the raw … If the red
channel has been clipping though, that's a concern", and then: "a clip alert
should be derived from the actual frame/image portion. Worrying about sprocket
holes would degrade the actual image."

## What it shows
Hovering a frame in the list shows a card (a rich-text tooltip):
- the frame's full file name (the list shows the short label,
  spec/capture-short-names.md);
- a row per file: **Red / Green / Blue** for a 3-way merge (its sources in
  order), **Shot** for a single file, **IR** for a paired IR shot; each with its
  shutter speed and ISO from the file's EXIF (`1/6 s · ISO 100`, `15 s · ISO 1000`)
  and, for every RAW except the IR shot, its clipping: "not clipped", or
  **"clips: red 0.42 %"** in the theme's danger red (each CFA colour that clips,
  with the share of its photosites), "checking…" until read, "not a RAW";
- a line saying where clipping was read: inside the crop / inside Capture's frame
  guide / in the middle of the sensor;
- the ⚠ re-grade note when the frame has one (it was the old tooltip).

## Where clipping is read (`core/frame_card.picture_area`)
The picture only, never the sprocket holes or open light round the carrier:
1. the frame's crop (`crop_rect` + `crop_angle`, sensor fractions, as the RAW
   mosaic is unrotated: auto crop and the Crop panel work in the unrotated
   frame);
2. else Capture's frame guide mapped onto the frame (`img.capture_guide`);
3. else the sensor's middle 60 % (`frame_meter.CLIP_NO_GUIDE`, what Capture's own
   per-shot warning uses with no guide).
A sliced frame's crop is the slice's, not the sensor's: the middle is used.
The test is Capture's (`frame_meter.clipping_in_guide`, spec/capture-clip-warning.md):
a colour clips when its top value inside the area is ≥ half the range and ≥ 0.1 % of
its photosites there sit within one code of it, 3 % in from the area's sides.

## Reading (`core/frame_card.py`)
EXIF and clipping are read off the GUI thread by one daemon thread
(`ClipReader`, newest hover first, a file never queued twice), cached per file
(path, size, mtime) and area; the card shows "…" / "checking…" and is redrawn
when a file lands (`ThumbnailList._card_read` → `_on_card_read`) if it is still up.
Measured on his frame 004 (GFX 50S II, 51 MP): EXIF of four files 0.10 s, the
three colour shots' clipping 0.76 s; nothing clips inside the crop (red 1/6 s,
green and blue 1/5 s, ISO 100; IR 15 s ISO 1000).

## Tests (tests/test_thumbnail_hover_card.py)
Files and slots of a merge / single / IR; the area from a crop, a guide, a slice,
nothing; shutter text; the card's rows (exposure, not clipped, clips in red,
checking, not a RAW, IR without clipping, where it was read); the reader reads
each file once and calls back; the list shows the card on hover and redraws it
when a file lands.
