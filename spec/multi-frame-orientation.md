# Rotate / flip several frames at once, and a lit mirror icon

Two small orientation features, asked for together:

1. With several thumbnails selected, the toolbar's rotate and mirror buttons
   (and the `[` / `]` keys) offer to apply the change to every selected frame.
2. The two mirror icons show whether the frame on screen is already mirrored.

## Goals

- A roll scanned emulsion-side-up, or a run of frames shot in portrait, can be
  righted with one click and one confirmation instead of a click per frame.
- You can tell at a glance whether the frame on screen is mirrored, and which
  way, without comparing it to the original.

## Non-goals

- No change with a single frame selected: the buttons act at once, as now.
- No new menu entries or thumbnail badges. The indicator is on the toolbar
  only (the user's choice).
- No indicator for the 90° rotation. It is visible in the picture itself.
- `fine_rotation_angle` is untouched, for the same reason the Orientation
  sync group leaves it out (spec/orientation-sync-group.md): it is measured
  per frame.
- No re-processing. Rotation and mirroring stay display-time transforms;
  nothing calls `update_thumbnail_and_preview()`.

## UX

### The prompt

When **more than one** thumbnail is selected, pressing one of the four
orientation controls asks first:

| Control | Title | Question |
| --- | --- | --- |
| Rotate Left, `[` | Rotate Left | Rotate all N selected frames left? |
| Rotate Right, `]` | Rotate Right | Rotate all N selected frames right? |
| Mirror Horizontal | Flip Horizontal | Flip all N selected frames horizontally? |
| Mirror Vertical | Flip Vertical | Flip all N selected frames vertically? |

- **Yes** (the default button, so Return confirms) applies it to every
  selected frame.
- **No**, Esc or closing the box **cancels**: nothing changes, not even the
  frame on screen (the user's choice).
- A rotation turns each frame 90° from where it is now.
- A flip **toggles each frame on its own** by default (the user's choice).

#### Mixed flips: a third button

When some of the selected frames are already flipped that way and some are
not, toggling and "make them all flipped" give different results, so the box
offers both (added at the user's request). Example, 5 selected, 2 already
flipped horizontally:

> **Flip all 5 selected frames horizontally?**
> 2 of them are already flipped this way. Flip Each turns those 2 back to
> normal; Flip the Other 3 leaves them as they are, so all 5 end up flipped.
>
> [Flip Each] [Flip the Other 3] [Cancel]

- **Flip Each** (default, Return): every frame toggles.
- **Flip the Other N**: only the N frames not yet flipped change; all end up
  flipped. The count in the label says which frames it touches.
- **Cancel**, Esc or closing: nothing changes.

With none or all of them flipped the two would give the same result, so the
box stays Yes / No. Rotations always get Yes / No.

Only frames that actually change get an undo snapshot, so "Flip the Other N"
leaves the already-flipped frames' undo history alone. The status hint after
a batch says how many changed ("Flipped 3 frames horizontally. All 5 selected
are now flipped.").

### Which frames

- More than one selected: exactly the selected frames. If the frame on screen
  was Ctrl/Cmd-clicked out of the selection, it is not touched.
- Otherwise: the frame on screen, with no prompt.

### Undo

Each changed frame gets its own undo snapshot before it changes, as Sync to
All and Paste do, so Ctrl+Z on a frame takes that frame back one step.

### The lit mirror icons

`Mirror Horizontal` and `Mirror Vertical` become checkable toolbar actions.
When the frame on screen is mirrored that way:

- the symbol is drawn brighter (`theme.ICON_ON`, white, against the normal
  `ICON` #cfcfcf);
- the button sits on the existing checked background (`SURFACE_ACTIVE`, the
  same look as the Dust button when it is on);
- the tooltip reads "Mirror Horizontal (this frame is mirrored)".

The state follows the frame on screen: image switch, flip, undo, Sync to All
and Paste all go through `update_preview`, which re-reads it. Clearing the
image list turns both off.

## Data model

Nothing new is stored. `rotation_angle`, `horizontal_mirrored` and
`vertical_mirrored` already live on `CCRImage`, are in its undo snapshot and
are saved in the catalog.

## Integration

`src/widgets/image_preview.py`:

- `_orientation_targets()` returns the frames an orientation control acts on;
  `_confirm_orientation_all(kind, targets)` shows the prompt.
- `rotate_left`, `rotate_right`, `mirror_horizontal`, `mirror_vertical` all
  route through one `_change_orientation(kind)`, which: works out the
  targets; asks when there is more than one; pushes an undo snapshot and
  changes each target; for the frame on screen, drops any crop drag, resets
  the zoom and re-applies the canvas transform (what the old handlers did);
  refreshes each changed thumbnail; and, for a batch, saves the catalog.
- The pure steps are module functions, testable without Qt:
  `next_orientation(kind, rotation, hflip, vflip, mode="each")` returns the
  new triple (`mode="rest"` sets the flip instead of toggling it);
  `flipped_count(kind, frames)`; and `orientation_prompt(kind, frames)`,
  which builds the box's title, question, note, choices and cancel label.
  `_ask_orientation_all(prompt)` shows it and returns the chosen mode or
  None; tests replace it.
- `_sync_mirror_actions()` sets the two actions' checked state and tooltips
  from the frame on screen. It runs at the end of the orientation change, in
  `update_preview` once the orientation is read, and in the empty-list reset.
  Signals are blocked while it sets the state. A click on a checkable action
  flips its state before the handler runs, so the handler always re-syncs at
  the end, which also undoes that flip when the prompt is cancelled.

`src/ui/theme.py`: `ICON_ON = "#ffffff"`, and `load_tinted_icon(...,
on_color=None)` adds a `QIcon.On` pixmap in that colour when given.

## Test plan (`tests/test_multi_frame_orientation.py`)

1. `next_orientation`: left/right wrap through 0/90/180/270; each flip only
   toggles its own flag.
2. One frame selected: the frame on screen changes, no prompt is shown.
3. Several selected, Yes: every selected frame changes, the one on screen
   included; the canvas state follows; each has one undo snapshot; every
   changed thumbnail is refreshed.
4. Several selected, No: nothing changes and no undo snapshot is pushed.
5. Mixed flips: the prompt offers Flip Each / Flip the Other N / Cancel
   with the counts in the note; Flip Each toggles every frame; Flip the Other
   N changes (and snapshots) only the unflipped ones. Uniform selections and
   rotations get Yes / No.
6. The frame on screen outside a multi-selection is left alone.
7. Mirror actions are checkable, follow the frame on screen through
   `_sync_mirror_actions`, and go back to unchecked after a cancelled prompt.
8. `load_tinted_icon(on_color=…)` gives the On state a brighter pixmap than
   Off.

## Refinement notes

- The old handlers reset the zoom and the crop drag unconditionally. They now
  do so only when the frame on screen is among the targets: a batch that
  leaves it alone must not jump the view.
- `[` / `]` used to act on the frame on screen even with several selected.
  Prompting there too keeps the keys and buttons consistent. With one frame
  selected the keys still work instantly.
- The canvas's `current_rotation` / `current_*_flip` are always re-read from
  the image after the change, rather than updated in parallel, so they cannot
  drift from the backend.
