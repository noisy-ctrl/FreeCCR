# Sharp preview at the fitted view

## Problem

At the fitted (unzoomed) view the canvas shows the 1080-px working preview.
Two things made it look blocky and soft:

1. The preview's `QGraphicsPixmapItem` used Qt's default
   `Qt.FastTransformation`, which overrides the view's SmoothPixmapTransform
   hint — an enlarged preview was drawn nearest-neighbour (repeated pixels).
2. On a high-DPI (Retina) screen a fitted window is ~2–3× more DEVICE pixels
   than the preview has, but the existing hi-res detail render only triggers
   when zoomed in or cropped, and it measures magnification in logical pixels.

## Changes

1. The pixmap item draws with `Qt.SmoothTransformation`.
2. **Sharp preview on high-resolution screens** (Settings → General → Zoom,
   default on, `view/sharp_fit`): at the fitted view, when the preview is
   magnified on the DEVICE (view scale × devicePixelRatio > 1.15) and the
   source has more pixels than the preview, request a detail render sized to
   the screen (`long side = zoom% × source long side × DPR`), bypassing the
   legacy 4,140/4,500-px floor that zoom tiles keep (a fit-only render needs
   ~2,600 px on a typical Retina window, not 4,500). Zoom requests also count
   device pixels, so 50 % on a 2× screen is one source pixel per device pixel.
   The 1080 preview stays the live editing surface; the sharp render follows a
   moment after edits settle, through the existing hi-res machinery (same
   colour-matched replay, cache and validation).

Turning the option off restores the previous behaviour exactly (except for
the smooth scaling, which is always on).

## Cost

A re-adjust of a ~2,600-px tile after each settled edit, on the existing
worker thread. The option is there for slow machines.

## Tests

Pixmap item transformation mode; `_zoomed_in_enough` at fit for DPR 1 vs 2,
with and without the option, and only when the source has more detail;
target size at fit (DPR-scaled, no 4,500 floor) vs zoomed (legacy floor kept).
