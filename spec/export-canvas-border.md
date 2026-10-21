# Export on a coloured canvas (border)

Place each exported frame, centred, on a canvas of a chosen size and colour,
the way a scan is often shared: for example a 3:2 frame 1946 px across on a
2000 × 2000 white square, leaving a 27 px border at the sides.

## Goals

- An optional **Border** section in the Export window: canvas width × height
  (px), the frame's size as its **long edge** (px), and a background colour.
- The frame keeps its ratio and is centred. If it would not fit the canvas at
  that size, it is shrunk to fit (it is never cropped).
- Works with TIFF, JPEG and **JPEG + HDR gain map**. In an HDR file the
  border stays at normal (SDR) white or whatever colour was chosen, and only
  the frame's highlights glow.
- The colour is chosen as sRGB and converted with the picture, so a ProPhoto
  export shows the same border colour.

## Non-goals

- Presets (the user's choice: custom numbers only).
- Off-centre placement, captions, rounded corners, shadows.
- The trichrome linear-merge TIFF (untagged camera data; the section is
  disabled for that format).

## UX

A **Border** group below the format/size form:

- **Place on a canvas** (tick box). The other controls are enabled only when
  it is ticked, and while it is ticked the Resize combo is disabled: the
  canvas sets the output size.
- **Canvas:** two spin boxes, width × height, 16–20000 px. Default 2000 × 2000.
- **Image long edge:** spin box, 16–20000 px, default 1946. Tooltip: "The
  frame's longer side in pixels; the other side follows the frame's ratio.
  A frame that would not fit the canvas is shrunk to fit."
- **Colour:** a swatch button showing the colour and its hex code; clicking
  opens the colour picker. Default white (#ffffff).
- A muted line under it: "Each frame is centred on the canvas."

Persisted: `export/canvas_on` (bool), `export/canvas_w`, `export/canvas_h`,
`export/canvas_long_edge` (int), `export/canvas_colour` ("#rrggbb").

The size estimate uses the canvas size for every file when the box is ticked.

## Processing

`core/export_canvas.py`:

- `Canvas(width, height, long_edge, colour)` (frozen dataclass; colour is an
  8-bit sRGB triple).
- `placed_size(w, h, canvas)`: scale so `max(w, h) == long_edge`, then, if
  either side exceeds the canvas, scale down to fit. Sides are rounded and
  at least 1 px.
- `compose(rgb_u16, canvas, over=None)`: resize the frame to the placed size
  (INTER_AREA when shrinking, INTER_LANCZOS4 when enlarging), fill a canvas
  with the colour (× 257 into 16 bits), paste the frame at
  `((W − w) // 2, (H − h) // 2)`. The optional HDR map (`over`, the
  pre-recovery brightest channel) is resized the same way (INTER_AREA) and
  padded with 0, so the border never glows. Returns `(canvas_rgb, canvas_over)`.

`write_export_image(..., canvas=None)`: with a canvas, `compose` replaces the
`max_long_side` resize. Everything after (colour space, ICC, JPEG/TIFF/HDR
writing) is unchanged, so the border is converted with the picture.

**Monochrome frames** keep their single-channel TIFF/JPEG only when the
border is neutral (R = G = B); a coloured border needs three channels.

`canvas` is threaded exactly like `hdr`: `ExportPlan.canvas` →
`ExportItemsWorker` → `export_items(canvas=)` → `export_image_by_index(canvas=)`
→ the four pipeline functions (`canvas=`) → `write_export_image(canvas=)`.
The linear-merge path ignores it.

## Test plan (`tests/test_export_canvas.py`)

1. `placed_size`: landscape and portrait take the long edge; too big shrinks
   to fit; never below 1 px.
2. `compose`: output size, colour at the corners and edges, frame centred
   (odd remainders go to the right/bottom), map padded with 0, input untouched.
3. A converted black-point frame exported as TIFF, JPEG and HDR JPEG on a
   canvas: output size, border colour, frame position; the HDR gain map is
   canvas-sized and zero on the border (decoded with libultrahdr).
4. ProPhoto: the border matches the colour-space conversion of that colour.
5. Monochrome: white border stays single-channel; a coloured border writes RGB.
6. Without a canvas, exports are unchanged.
7. Export window: controls, enabled states, Resize disabled while ticked,
   disabled for linear merge, settings round-trip, `plan.canvas`, the worker
   passing it on.

## Refinement notes

- The frame is placed AFTER crop and orientation (it is the finished frame),
  which is also where the HDR map has been brought into the same geometry.
- Upscaling is allowed (a small crop on a big canvas) but uses Lanczos; the
  usual case is a downscale from a full-resolution scan.
- Rounding: `w = round(src_w * s)`, `h = round(src_h * s)`; centring uses
  floor division, so any odd pixel of border lands on the right/bottom.
