# Histogram clipping overlay

Hovering the histogram's clipping corners shows on the picture what is
clipping: the bottom-left corner shows the pixels at pure black, the
bottom-right the pixels at pure white, while the pointer is there.

## Goals / non-goals

- Hover the bottom-left / bottom-right corner of the histogram → the picture
  shows its clipped pixels; move away → gone. Works whether or not a corner
  wedge is lit (with nothing clipping the picture simply shows nothing, which
  is itself the answer).
- Same definition of clipping as the histogram: an 8-bit channel at 0 (black)
  or 255 (white), on the pixels actually displayed (the histogram counts the
  displayed preview's crop; outside crop mode the canvas shows that crop).
- Display only (a layer over the picture, like False colour): nothing that
  reads the picture sees it, nothing is saved.
- Non-goals: a click-to-lock toggle; per-channel colour coding of the
  overlay; a threshold other than the exact extreme.

## UX

- The two hot corners are squares of 2 × the wedge size (22 px) at the plot's
  bottom corners. Hovering one outlines its wedge (so the corner reads as a
  control even when nothing clips) and sets the tooltip, e.g.
  "Black clipping: 0.8 % of the picture (shown in blue)".
- Black clipping is shown in **blue**, white clipping in **red** (the usual
  convention). Solid where all three channels clip; half-strength where only
  one or two do (a colour clipping in one channel).

## Data / integration

- `HistogramWidget.clip_hover = Signal(str)`: "black", "white" or "" (left /
  hidden / data cleared). Mouse tracking on; `leaveEvent` emits "".
- `SlidersPanel` forwards it to `ImagePreview.set_clip_overlay(kind)`.
- `ImagePreview`: its own layer (`_clip_item`, a child of the picture item at
  z 2.5, above every other layer), computed from the displayed pixmap
  (`core.clip_overlay.clip_rgba(rgb8, kind)`), cached on (kind, cacheKey),
  refreshed wherever the other layers follow a new pixmap, reset with them on
  `scene.clear()`. Up to 4096 px on the long side; a bigger (zoomed) pixmap is
  reduced by an integer max-pool, so an isolated clipped pixel still shows.

## Tests (`tests/test_histogram_clip_overlay.py`)

- `clip_rgba`: all channels at 255 → solid red, one channel → half, none →
  transparent; black likewise in blue; max-pool keeps an isolated pixel.
- Widget: hovering each corner emits the right kind, the middle and leaving
  emit "", the hovered corner is remembered for painting.
- Canvas: `set_clip_overlay("white")` shows a layer red where the pixmap is
  white, `None`/"" hides it; the panel forwards the widget's signal.
