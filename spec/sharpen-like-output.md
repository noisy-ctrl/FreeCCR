# Sharpening shows what the output gets

The user, 2026-11-09: "it's fairly vital that it shows a closer reflection of
the output." Supersedes the "preview simulates the full-res look" radius
scaling in `spec/sharpening.md`.

## The problem

The Radius slider is in full-resolution pixels (25 = 1 px), but each render
used it differently:

| Render | Buffer | Radius used |
|---|---|---|
| 1080 preview | ~1080 px | radius × full/1080, capped at 8 px (exaggerated) |
| Fitted detail render / zoom tiles | 2000–8000 px | radius in the buffer's own pixels |
| Export, downsized, no crop | the output size (decoded small) | radius in the output's pixels |
| Export, full size or cropped | full resolution | exact |

So Radius 100 (4 px) on an 8256 px frame was 8 px on the 1080 preview, about
15 times what a full-size export gets; the detail render that replaces the
preview showed a different amount again; and an uncropped 2000 px export was
sharpened about four times harder than the same export with a crop.

## The rule

Every render sharpens with the radius it would have **at full resolution,
expressed in its own pixels**: `r = radius_px × buffer_long / full_long`, with
`buffer/full` capped at 1. No lower clamp, no exaggeration. `apply_adjustments`
works this out itself from `original_full_size` (which is already the slice's
size for a sliced frame); `sharpen_scale` is now that ratio and its default
`None` means "work it out". It is safe to derive now: every buffer that reaches
the sharpening is the whole (sliced) frame at some resolution; crops are cut
after the adjustments. A buffer whose size is unknown is taken as full size.

Consequences:

* The fitted view shows roughly what an export at the size you are viewing
  shows; 100 % zoom shows the exact pixels; the 1080 preview and the detail
  render agree.
* Small radii visibly do little at the fitted view, which is true of the
  output too.
* Downsized and cropped exports now agree.
* No cost: the same buffers, with a smaller blur.

## How close it is (measured)

Sharpening a small buffer with the scaled radius against sharpening at full
size then shrinking (INTER_AREA), grain in flat mid-tones on a real Portra 400
frame, 8-bit levels, half-size decode standing in for full size:

| Setting (buffer px at 4140) | Size | Sharpen then shrink | Shrink then sharpen | None |
|---|---|---|---|---|
| A100, R100 | 2000 | 6.92 | 6.89 | 3.18 |
| A100, R50 | 2000 | 5.35 | 4.60 | 3.18 |
| A50, R100 | 2000 | 4.96 | 4.95 | 3.18 |

Large radii match; a radius under about one buffer pixel shows somewhat less
than the output gets (the grain it would lift is finer than the buffer). Upsampling
2× before sharpening was tried and was worse (the upsample softens).

## Tests

* `apply_sharpening(scale=s)` uses `radius × s`, no lower clamp; scale 1 is
  unchanged.
* `apply_adjustments` derives the scale from the buffer against
  `original_full_size`: the same frame rendered at 1080 and at full size,
  then the full one shrunk, give halos of the same extent.
* The preview no longer passes its own scale.
