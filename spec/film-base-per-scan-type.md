# Film bases per scan type, B&W light, and Measure always asks

The user, 2026-11-18, in Capture with **Black & white** chosen: "for B+W
capture, the last used C-41/RGB measurement loads. Does it make sense to have
the menu change a bit? Does it make sense to do a measurement for the film
base of B+W?" Then: "With the option to change white light to a single colour
would be good too", and two bugs: "when I click Measure, there's a tiny measure
box that appears (even without me clicking anywhere)", and "although I selected
the film type in the popup when clicking Capture, it's clearly defaulted to
35mm for the crop."

## 1. The film base list follows the scan type

- A film base records the scan type it was measured in: `scan` =
  `trichrome` | `colour` | `bw` (`base_exposure.entry(..., scan=, light=)`,
  kept by `_clean_base`, carried by Measure Again / Edit / Rename).
  Older entries have none: three colours read as `trichrome`; one exposure
  (`w`) fits both `colour` and `bw` (`be.scan_of`, `be.fits(base, type)`).
- The **Film base** list shows only the bases that fit the current scan type
  (item data = the base's index in the whole list). Each scan type remembers
  its own pick by name (`live/film_base_pick`, JSON {type: name}); switching
  type brings back that pick, else the first base that fits, else "No film
  base for <type> yet" (disabled). `_film_base()` is None when the selected
  base doesn't fit, so Edit / Rename / Delete / Measure Again / Shoot at these
  exposures / the black point never act on a hidden base.
- A new Measure records the current scan type (and, in B&W, the light).

## 2. B&W light

- In B&W a **B&W light** row under the scan type: White / Red / Green / Blue
  (`live/bw_light`, default White). Hidden for the other types.
- Choosing it lights that LED alone at full when the Scanlight is open and
  nothing is shooting or measuring. Measure in B&W starts from that LED alone
  at full (colour · white light keeps the light as it is), and the film base
  records `light`. Picking a B&W film base sets the row to its light.
- Why: one narrow colour can't show colour fringing, so edges and grain are a
  little crisper; green has the most photosites. Processing is unchanged: B&W
  frames are made grey from the three decoded channels (Rec.601). Measured
  on the user's GFX 50S II under the green LED, red and blue photosites see
  13 % / 29 %; mixed at Rec.601 weights their extra noise is ~8 % of the grey's
  variance, so taking green alone is not worth a processing change.

## 3. Measure always asks where

The automatic pick (`find_rebate` on the live picture, high confidence) is
removed: on the user's 120 B&W frame it took "the top border", which was
picture, and drew a small patch nobody had asked for. Measure now reads the
box or click made for it, else a usable earlier Film Base click (unchanged
rules), else asks: "Click the film base…". `spec/film-base-exposure.md` §
"Where the base is read" step 2 is withdrawn.

## 4. The roll's format sets the frame guide

The Start Hot Folder Tether window's **Format** (`capture_session["format"]`,
the same `film_formats` keys as Capture's Format list) now sets the frame
guide's format when the session is set (`MainWindow._set_capture_session` →
`LiveCaptureMonitor.apply_roll_format`) and when Capture opens with a session.
The guide keeps its area and takes the format's shape
(`_set_guide_format`); a hidden guide is reshaped without being shown.
"Not set" leaves the guide alone. The guide is what crops each capture
(`spec/guide-follow-and-crop.md`), so a 6×4.5 roll now crops 6×4.5.

## Tests

- `fits` / `scan_of` for old and new entries; entry keeps `scan`/`light`
  through replace / edit / rename.
- Capture: in B&W only the fitting bases are listed; the pick is remembered
  per type; with none, the list says so and Edit is off; Measure in B&W lights
  the chosen LED and records scan + light; the B&W light row shows only in
  B&W.
- `_film_base_patch` never picks a border by itself.
- A session format reshapes the guide to that format; "Not set" leaves it.
