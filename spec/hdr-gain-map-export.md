# HDR export: JPEG with a gain map

Option 2 of the HDR discussion: keep the normal (SDR) picture exactly as it
is, and add the highlight range the pipeline already carries above white as a
gain map, so HDR screens show those highlights brighter. The canvas stays SDR
(true HDR on the canvas is the separate, larger option 3); a preview tint
shows where the glow will be.

## Goals

- A new export format, **JPEG + HDR gain map**. The base image is
  byte-for-byte the SDR JPEG the plain JPEG format writes (same colour space,
  same quality). HDR displays (macOS 15+/iOS 18+ Preview and Photos, Chrome,
  Android 14+, Instagram) brighten the highlights; everything else shows the
  normal picture.
- One setting: **HDR headroom**, 0.5 to 3 stops in half stops, default 2. How
  far above SDR white the brightest highlight may go.
- No new grading sliders. What glows follows the finished picture (revision
  2): its bright end, plus anything pushed past white. So every control that
  brightens the picture (Brightness, White Point, Contrast, Curves, Master
  Gain) grows the glow, and recovering highlights shrinks it.
- A toolbar toggle, **HDR glow**, that tints the areas that will glow,
  stronger where they glow more.
- Nothing else changes: the preview, thumbnails, histogram and every existing
  export format are untouched (pinned by tests).

## Non-goals

- HDR on the canvas (option 3), float/EXR export (option 1), PQ/HLG AVIF or
  HEIC. Each can follow.
- Per-frame HDR settings. The headroom is an export setting, like quality.
- Colour in the glow: the gain is the same on R, G and B, so a highlight gets
  brighter without changing hue. A clipped SDR highlight stays the colour it
  is in SDR.
- Data above white for bases without a working-space window.
  Reference-frame conversions and Positive mode render straight into display
  range, so only the bright-end term applies to them; black/white-point and
  no-anchor conversions (the windowed bases) also get the above-white term.

## UX

### Export dialog

The Format combo gains **JPEG + HDR gain map** after JPEG. With it selected:

- the Quality slider shows (it is the SDR base's quality);
- an **HDR headroom** row appears: a spin box, 0.5–3.0 in 0.5 steps, with the
  suffix " stops" and the tooltip "How far above normal white the brightest
  highlights may go on an HDR screen. 1–2 stops looks natural; 3 is
  dramatic.";
- the size estimate uses the JPEG estimate (the gain map adds a few percent);
- files get the `.jpg` extension;
- the ProPhoto banding hint applies exactly as for JPEG.

Remembered in QSettings: `export/format = "hdr_jpeg"`,
`export/hdr_headroom` (float, default 2.0).

### HDR glow toggle

A checkable toolbar action, **☀ HDR**, after the area buttons. Tooltip:
"Tint the highlights that will glow on an HDR screen in a 'JPEG + HDR gain
map' export. Stronger tint = more glow, up to the export's HDR headroom.
Preview only." Off at startup, not saved.

When on, the canvas draws a layer over the glowing pixels: nothing below
0.05 stops of glow, then a strength of 0.30 + 0.55 × glow/H. It was a magenta
(`#ff40c8`) tint at that opacity; since False colour (spec/false-colour.md),
whose scale uses pink, it is a halftone of black-and-white dots whose density
follows the strength, always (the user's choice), so both views can be on at
once.

Revision 2: the layer is its **own graphics item**, a child of the picture
item (so it inherits orientation, fine rotation and the hi-res prescale) and
cropped exactly like the displayed preview. It is never part of the
picture's pixels, so the scopes, the colour probe, the histogram, the
thumbnails and every export only ever see the real picture. (The first
version baked the tint into the preview and hi-res pixels, and it showed up
in the waveform.) The opacity map comes out of the preview render at preview
resolution (`render_preview_pixels` → `hdr_alpha`, stored as
`_preview_hdr_alpha`) and is scaled to whatever pixmap is displayed; the
hi-res tile is untouched by the toggle. Turning the tint on re-renders the
preview once (the map needs the render's above-white capture); turning it off
only hides the layer. With the tint on, a frame whose stored preview has no
map for the current headroom is re-rendered when you switch to it.

H is the headroom last chosen in the export dialog.

## Processing

### What glows (revision 2)

The first version measured only what sat above white *before* White Point,
so a recovered sky would still glow. In use that read as broken: raising
White Point or Brightness never grew the glow, because neither could reach
that measurement. The glow now follows the finished picture.

Two terms, from the finished SDR picture `sdr` (display values, the pixels
the export writes, before any border) and `over`, the brightest channel
captured inside `_apply_working_space_recovery` **after** every gain (Channel
Levels, the Cineon/Film Look decode, Channel Balance, Master Gain with Auto
Gain, White Balance, White Point and the legacy Gain/Exposure) but **before**
the auto-tone shoulder and the window clamp:

    y    = Rec.709 luma of sdr                        (0..1)
    r    = clamp((y − 0.80) / 0.20, 0, 1)             the bright end, knee 0.80
    s    = r² · 0.5 · H  +  r · log2(max(over, 1))    stops
    glow = H · tanh(s / H)

- The **bright end** ramps in from 80 % display luma (squared, so it starts
  gently) to half the headroom at white: 1 stop at the default H = 2.
  Brightness, Contrast, Curves and White Point all move pixels across it.
- **Past white** adds log2 of how far, weighted by the same ramp so a dark
  but saturated colour whose single channel ran past white does not glow.
  White Point up pushes more past white; recovery pulls it back.
- `tanh` rolls the total smoothly into the ceiling H (slope 1 at 0).
- Bases without a window have no `over`: only the bright-end term applies.
- The auto-tone shoulder is skipped by `over` on purpose: it exists to soften
  exactly what was pushed past white, and the HDR rendition is where that
  detail can be shown.

`GLOW_KNEE` (0.80) and `GLOW_EXPAND` (0.5) live in `core/hdr_gainmap.py`.

The capture is a thread-local context (`ccr_processor.hdr_capture()`), so no
render signature changes. Only the first windowed recovery in a context is
recorded: area layers grade the already-clamped global result and never run
the windowed stage. Outside a capture context the stage does nothing extra.

### The file

`core/hdr_gainmap.py` writes an "Ultra HDR" JPEG with both metadata
flavours, the layout libultrahdr 1.4 produces:

- **Primary** (the SDR picture): SOI, APP0 JFIF, APP1 XMP (`hdrgm:Version`
  and a GContainer directory giving the gain map's length), APP2 ISO 21496-1
  version block, APP2 MPF index (two images; the gain map's offset counted
  from the MPF TIFF header), APP2 ICC profile(s), image data.
- **Gain map**, appended after the primary's EOI: a single-channel 8-bit
  JPEG (quality 90, full resolution, so highlight edges stay sharp), with APP1
  XMP (`hdrgm` values) and APP2 ISO 21496-1 metadata.
- Values: gain min 0, gain max H, gamma 1, offsets 0, HDR capacity 0 to H,
  base rendition SDR, gain in the base colour space. A sample v means
  2^(H·v/255) on a display with at least H stops of headroom (scaled down on
  displays with less, by the viewer).

The SDR base is the same `to_8bit(apply_export_colorspace(...))` pixels the
JPEG path writes, encoded at the chosen quality.

**Monochrome frames** (B&W profile or a mono decode) are written as
three-channel primaries in this format: gain-map readers expect a colour
base. The channels are equal, so the picture is unchanged. The plain JPEG and
TIFF keep their single-channel output.

### Geometry and size

The four export tails (reference, black/white point, positive, ref-params)
open a capture around `apply_adjustments(..., for_export=True)` when HDR is
requested, then pass the captured map through the same crop and orientation
as the picture (`hdr_geometry`). `write_export_image` resizes the picture to
the output size, works out the glow on the FRAME (`hdr_gainmap.glow_map`,
which resizes the captured map to it), and only then places both on a border
canvas if one is set (the glow padded with none), so a white border never
glows. A capture with no map (non-windowed base) leaves the bright-end term
alone.

## Integration points

| Where | Change |
| --- | --- |
| `core/hdr_gainmap.py` | new: headroom constants, `glow_stops`, `gainmap_levels`, metadata builders, `encode_gainmap_jpeg` |
| `core/ccr_processor.py` | `hdr_capture()` / `HdrCapture`, the capture in `_apply_working_space_recovery`, `hdr_geometry`, `hdr=` on the four export functions, `hdr_headroom`/`hdr_over` on `write_export_image`, `apply_hdr_tint` |
| `core/ccr_backend.py` | `hdr_headroom` through `export_items` → `export_image_by_index` → pipelines; `hdr_tint_headroom` (None = tint off) |
| `core/ccr_image.py` | `render_preview_pixels`: capture + tint the preview pixels when the tint is on; snapshot carries `hdr_tint` |
| `widgets/export_dialog.py` | format entry, headroom row, settings, plan field, extension, estimate |
| `widgets/image_preview.py` | the toggle; `ExportItemsWorker` passes the headroom; the hi-res worker captures and tints; the hi-res signature includes the tint |

## Test plan (`tests/test_hdr_gain_map.py`)

1. `glow_stops`: 0 at or below white; slope 1 just above white; monotonic;
   below H; `log2` for small s.
2. `gainmap_levels` and `clamp_headroom` bounds.
3. The file decodes with libultrahdr (imagecodecs) and the HDR/SDR ratio
   matches 2^glow within 1.5 %, with ISO-only, XMP-only and both metadata
   sets; the SDR base decodes to the same pixels as a plain JPEG of the same
   picture.
4. Structure: the MPF offset lands on the gain map's SOI; the XMP length
   equals the gain map's length; ISO metadata round-trips.
5. The capture records the pre-recovery maximum; White Point and auto tone do
   not change it; the SDR output with a capture open is identical to without.
6. An end-to-end black-point export with HDR on: the SDR base equals the plain
   JPEG export's pixels; the gain map is non-zero only where the base was
   above white; crop and rotation keep the map aligned with the picture.
7. Export dialog: the format entry, headroom row visibility, settings
   round-trip, plan field and `.jpg` extension.
8. The tint: off leaves the preview pixels identical; on tints only glowing
   pixels; thumbnails and histogram are untouched.

## Refinement notes

- The gain map is single-channel. A three-channel map could restore colour
  inside clipped highlights, but it would also shift the colour of every
  highlight relative to the SDR picture, which is the opposite of "the same
  picture, brighter".
- Offsets are 0 (as libultrahdr 1.4 writes), so the decode is exactly
  SDR × 2^gain with no dark-pixel bias; the gain is 1 below white anyway.
- Full-resolution gain map: maps are mostly flat zero, so the size cost is
  small, and a half-size map would bloom around small specular highlights.
- The headroom setting lives with the export, but the tint reads it too, so
  the dialog writes it to `ccr_backend.hdr_tint_headroom` whenever the tint is
  on, and the toggle reads the saved value when switched on.
