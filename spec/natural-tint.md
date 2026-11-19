# Natural tint (B&W)

The user, 2026-11-19: "In B+W mode too, can the colour organics be replaced
by a natural tint slider. Image isn't completely monocrome when using the
slider. It adds back the natural tint of whatever film it is? But be off by
default."

## Goals / non-goals

- With Color Profile = B&W, a **Natural tint** slider (0–100, default 0 =
  completely monochrome, exactly as before) brings back the frame's own tint:
  the colour the same frame has in Color profile, as a tone curve (cooler
  shadows, warmer highlights, a base's cast, whatever the film shows), never
  the per-pixel colour (grain, dye clouds, a stray colour object).
- Not a toning tool with chosen colours (sepia, selenium): it only reads the
  frame. Not for monochrome decodes (those carry no colour).

## UX

- In B&W the Colour Organics row is replaced by **Natural tint** (the same
  place, under Vibrance); in Color the reverse. The label's tooltip explains
  it. Right of 0 adds the tint; 100 = the full tint the frame shows in Color.
- Its value is stored per frame (`adjustment_settings["natural_tint"]`), in
  the "Saturation & Lab Finish" sync / copy group, and survives switching the
  profile back and forth (Colour Organics keeps its own value too).

## Processing (`core/natural_tint.py`)

1. The colour render the B&W collapse is about to discard (`adjusted`, the
   frame with all its settings) is reduced to ≤ 256 px (INTER_AREA) inside
   the crop's bounding box (the render is of the whole frame), turned into
   OKLab.
2. **The tint curve**: the picture's lightness is split into 32 bins; in each
   bin with ≥ 20 samples the MEDIAN a and b (robust to a few coloured
   things). Empty bins are filled by linear interpolation between neighbours
   (held flat past the ends), then smoothed with a Gaussian of 1.5 bins.
   Bins near black and white fade the tint out (a smoothstep over L 0–0.08
   and 0.94–1), so pure black and white stay neutral.
3. Each grey pixel (the B&W collapse, Rec.601 as before) gets (a, b) =
   amount/100 × curve(L) at its own OKLab L, unchanged L, moved into range by
   `oklab.fit_move` (never clips). A pixel with zero tint is returned
   exactly; amount 0 never runs (the old B&W path byte for byte).

Why this is safe for grain: the tint is a smooth function of lightness only,
so luminance grain moves it by (curve slope × grain), tiny; colour grain is
never read per pixel.

## Integration

- `CCRImage.apply_adjustments`: both B&W collapses go through
  `_bw_collapse(adjusted, s)`; `natural_tint.apply(grey, colour, amount,
  area)`.
- `ccr_processor.renders_monochrome`: a B&W profile with Natural tint > 0 is
  NOT monochrome (the export writes RGB, so the file matches the preview);
  a monochrome decode still is.
- `SlidersPanel`: `natural_tint` created right after Colour Organics
  (ADJUSTMENT_KEYS zip), shown in its place by `_sync_bw_rows()` (on frame
  load and on profile change); "sat" sync group; label tooltip.

## Tests

- Amount 0 / profile Color: unchanged paths (byte for byte).
- A warm-highlights / cool-shadows ramp in colour comes back with that tint
  (warmer at the top, cooler at the bottom), grey at 0, stronger at 100 than
  50, L unchanged within 1e-3, never clipped.
- A small saturated red object barely moves the curve (median).
- Grain on a grey ramp: chroma noise of the result ≪ the colour input's.
- `renders_monochrome` false with the tint on; the export writes 3 channels.
- Panel: in B&W the Natural tint row shows and Colour Organics hides, and
  back in Color; the value is stored and in the sat group.
