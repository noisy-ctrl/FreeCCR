# Scan type as picture tiles in the Start window

Status: built 2026-11-07 (the user's request: "instead of the Scanning:
dropdown, could we have picture icons for RGB colour negative, white light
colour negative, B+W?"; his answers: the Start window only, picture + short
name).

## Goals
- In the Start Hot Folder Tether window, the **Scanning** dropdown becomes
  three tiles side by side, each a small picture with a short name under it,
  so the choice reads at a glance.
- Nothing else changes: the window's answer (`options()["scan_type"]` =
  `"trichrome"` / `"colour"` / `"bw"`) and everything that acts on it.

## Non-goals
- The tether banner's and Capture's scan-type lists stay lists (the user
  chose the Start window only).
- No image files: the pictures are drawn in code, so they are sharp at any
  screen scale and follow the window's look (dark, or light from Capture).

## The tiles (`widgets/scan_type_tiles.py`)
Order as the user listed them, names from `scan_type.TILE_NAMES`:

| tile | type | picture |
|---|---|---|
| **RGB** | `trichrome` | an orange-based colour negative over a light panel in three bands, red, green and blue |
| **White light** | `colour` | the same negative over one white panel |
| **B&W** | `bw` | a grey negative over a white panel |

Each negative is drawn the way a negative looks: the sky (bright in the
scene) dense, the hills thin, the sun densest. The light panel glows up
behind the film in its colour. The sprocket holes are white on every tile
(the user asked, 2026-11-07; they were red, green and blue on the RGB tile).
The tooltip is the full label (`scan_type.LABELS`) with a line on what it
means.

- `ScanTypeTiles(QWidget)`: `currentData()`, `setCurrentData(t)` (unknown →
  the default, as `scan_type.clean`), signal `changed(str)` (only on a real
  change), `tiles` (type → tile). One tile is always picked.
- `ScanTile(QAbstractButton)`, checkable, painted by hand: picture
  96 × 60 px, name below. Picked: raised background and a 2 px outline
  (the text colour while the row has focus, muted otherwise); hover: a
  stronger border; name in the text colour when picked, muted otherwise.
- Keyboard: the ROW is the one tab stop (as a group of radio buttons);
  Left/Up and Right/Down pick the previous/next tile (no wrap). Clicking a
  tile gives the row focus.
- Accessibility: each tile's accessible name is its short name, its
  description the full label; the row's name is "Scanning".
- Colours come from `theme` tokens through a `colour` function. The dialog's
  `restyle` hook (already how Capture's `light_dialog` recolours a window's
  later stylesheets, see `FilmBaseEditDialog`) becomes a property on
  `TetherStartDialog` that also repaints the tiles, so opened from Capture
  the tiles are light like the rest. Hover uses BORDER_STRONG, not
  SURFACE_HOVER (#3c3c3c is also BORDER, and the light map treats it as a
  border).

## The window
`TetherStartDialog.scan_tiles` replaces `scan_combo`; `options()` reads
`scan_tiles.currentData()`. No other code used `scan_combo` of this window
(Capture's own `scan_combo` is a different widget and is unchanged).

## Tests (`tests/test_scan_type_tiles.py`, `tests/test_tether_start_window.py`)
- Order and names; opens on the given type; an unknown type opens on the
  default.
- A click picks that tile and only that one, `changed` fires once; clicking
  the picked tile again keeps it.
- Arrow keys step through the tiles and stop at the ends; the row takes Tab
  focus and the tiles don't.
- The pictures differ as they should: rendered at 2×, the RGB tile has red,
  green and blue light; White light has white light and the orange base and
  no saturated green or blue; B&W has no saturated colour at all.
- Light look: with `restyle = theme.light_qss_text` the tile background is
  the light surface colour.
- The window returns the picked tile's type.
