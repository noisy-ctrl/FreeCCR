# Full-resolution colour in high-quality JPEG exports

## Problem

Export JPEGs were encoded with OpenCV's defaults, which store colour at half
resolution in both directions (4:2:0 chroma subsampling) at every quality,
98 included. Luma detail is untouched, but small saturated details (a red
sign, coloured text, a bright flower edge against foliage) bleed slightly into
their neighbours. JPEG XL, which was being considered, would not have this.

## Change

- At **quality 90 and above**, export JPEGs store colour at full resolution
  (4:4:4). Below 90 the default (4:2:0) stays, since those exports are about
  size.
- One helper, `core/jpeg_params.jpeg_params(quality)`, gives the encoder
  settings to every export JPEG: the plain JPEG (`write_export_image`), the
  SDR picture inside "JPEG + HDR gain map" (`encode_gainmap_jpeg`, so its base
  stays identical to the plain JPEG), and the size estimate
  (`export_estimator.jpeg_bytes_per_pixel`, so the estimate matches).
- The gain map itself (single channel) and monochrome JPEGs (one component)
  are unaffected.
- If an old OpenCV lacks `IMWRITE_JPEG_SAMPLING_FACTOR`, the default is used.

Cost: files some 10-20 % larger at quality 90+. 4:4:4 is baseline JPEG and
every viewer reads it. (Instagram re-encodes uploads, so this is about the
files you keep and share directly.)

## Tests (`tests/test_jpeg_chroma_444.py`)

1. `jpeg_params`: 89 → quality only; 90 and 98 → quality + 4:4:4.
2. A plain JPEG export at 92 has 1×1 sampling on all three components; at 80
   the luma is 2×2.
3. The HDR JPEG's picture at 92 is 4:4:4 and still identical to the plain JPEG.
4. A monochrome export at 95 still writes one component.
