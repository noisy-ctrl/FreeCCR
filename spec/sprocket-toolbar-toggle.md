# Sprocket holes white: a toolbar button

Status: built 2026-10-30 (the user's request, after the clear-film whitening
turned out to be what whitened deep shadows on tethered captures converted
with a lead frame's black point).

## What
A checkable icon button on the canvas toolbar, right after the rotate / flip
icons (the user's choice, 2026-10-30; it began after Un-convert), for
the existing global switch "White sprocket holes / clear film (reversal
look)" (Settings → Film border, `ccr_backend.sprocket_mask_white`, QSettings
`adjust/sprocket_mask_white`, `spec/sprocket-hole-mask.md`).

- Icon: a film strip with its holes (`src/icons/sprocket-holes-icon.png`,
  black line art on transparency like the other toolbar icons, tinted by
  `theme.load_tinted_icon`, brighter while on). An icon, not words: the
  toolbar has to fit the canvas at the window's minimum width in a Mac-wide
  font (`tests/test_panel_width.py`); the words "Sprockets" left 1 px, the
  icon leaves 47.
- Tooltip says what it does and warns that anything clearer than the film
  base is whitened, so a wrong black point can whiten deep shadows.
- A click calls `MainWindow.on_sprocket_mask_toggled` (the Settings box's
  path: store, persist, re-render every image). That method and the restore
  at start-up both call `MainWindow._sync_sprocket_action`, so the button
  always shows the switch however it was changed.

Nothing new is stored.

## Tests
`tests/test_sprocket_toolbar_toggle.py`: the click flips the switch and the
setting; a change from elsewhere moves the button; a new window shows the
saved state; it sits between Un-convert and the rotate icons.
