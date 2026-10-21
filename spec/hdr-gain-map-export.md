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
- No new grading sliders. What glows is what sits above white *before*
  highlight recovery, so the existing controls keep their meaning: Master Gain
  (exposure) decides how much is above white; White Point recovery and the
  auto-tone shoulder shape the SDR picture only.
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
- Headroom for bases without a working-space window. Reference-frame
  conversions and Positive mode render straight into display range, so there
  is nothing above white to show. Their HDR export is valid, but the gain map
  is flat and the file looks the same on an HDR screen. Black/white-point and
  no-anchor conversions (the windowed bases) are where the glow comes from.

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

When on, the canvas (preview and hi-res zoom) shows the picture with the
glowing pixels blended towards magenta (`#ff40c8`): no tint below 0.05 stops
of glow, then an opacity of 0.30 + 0.55 × glow/H. Magenta is rarely a real
highlight colour on film, so it reads as an overlay. The thumbnails, the
histogram and every export never carry the tint. The scopes and the colour
probe read the displayed picture, so they see the tint while it is on, as
with any display view; the tooltip need not say so.

H is the headroom last chosen in the export dialog.

## Processing

### What glows

Captured inside `_apply_working_space_recovery`, the windowed base's
de-window stage, after Channel Levels, the Cineon/Film Look decode, Channel
Balance, Master Gain (with Auto Gain) and White Balance, and **before** the
White Point recovery, the auto-tone shoulder and the window clamp. The
legacy Gain/Exposure stage (`exposure`, a scalar applied after White Point)
is folded in, since it is an exposure control too.

    over = max(R, G, B) × exposure_factor           (display units, white = 1)
    s    = log2(max(over, 1))                         stops above white
    glow = H · tanh(s / H)                            stops of HDR brightening

`tanh` makes the glow start as a straight continuation of the picture (slope
1 at white) and roll smoothly into the ceiling H instead of hard-clipping
there. Using the brightest channel means a saturated highlight glows as soon
as any channel crosses white, which is when the SDR picture starts to lose
it.

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
as the picture (`hdr_geometry`). `write_export_image` resizes it to the
output size with the picture (INTER_AREA) and builds the file. A capture with
no map (non-windowed base) gives a zero glow.

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
