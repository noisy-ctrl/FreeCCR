# Shorter capture names, and a frame list that doesn't scroll sideways

The user, 2026-11-23: "file names are now getting too long. There's now a horizontal
scroll in the left menu. 35mm could be shortened to 135. Image number could be 2
digits. Could be 1R rather than 1RED. Text could be smaller font."

## Names FreeCCR gives (core/capture_log.py)
- Slot words: `1R` `2G` `3B` (were 1RED 2GREEN 3BLUE), `W` (was WHITE), `BW`, `4IR`
  unchanged. They still sort red, green, blue, IR by name.
- The format's tag: 35mm → `135` (`FILE_TAG`, `format_tag`); every other format
  keeps its key (645, 6x6, 6x7, xpan …).
- The shot number in two digits (`SHOT_DIGITS`): 04, … 99, then 100 as it is (a
  roll is ≤ 40 frames).
- `R0025_35mm_Colorplus_004_1RED` → `R0025_135_Colorplus_04_1R` (25 → 25 characters
  shorter by 5; with the `_RGB` merge suffix and extension the list used to show 37).

**Old names are still read everywhere**: every pattern takes a shot of 2 or more
digits and both word sets (`_SLOT_WORD`); a log entry written before the change
matches files named with the old format tag too (`_entry_keys`: today's and the
old tag), so `entry_files` / `add_file` never drop an old shot's files as
"another shot's". The guide pictures (`.freeccr-guides/<roll>_<shot>.png`) keep
their three digits: no one sees them.

## The frame list (widgets/thumbnail_list.py)
- Each frame is labelled by `capture_log.list_label(name)`: a name FreeCCR gave,
  old or new, in the short form — a merge without its slot word or `_RGB`
  (`R0025_135_Colorplus_04`), a single frame with its short word
  (`R0026_120_HP5_03_BW`), a retake / duplicate number kept; any other name
  without its extension. The ⚠ mark stays in front.
- The full file name is the item's tooltip (until the hover card,
  spec/thumbnail-hover-card.md).
- Text at the caption size (`theme.FS_CAPTION`, 11 px); items sit on a fixed grid
  as wide as the list's viewport, the text elided in the middle when it is still
  too long, and no horizontal scroll bar.

Export file names are unchanged (they come from `display_name`, not the label).

## Tests (tests/test_capture_short_names.py)
New stems; old and new names read by `name_slot`, `slot_order`, `shot_key`,
`is_ir_file`, `_shot_from_name`/`next_shot`; a log entry with an old-format file
keeps it; `list_label` of old, new, merged, single, retake, duplicate and other
names; the list has no horizontal scroll bar, an 11 px font and short labels.
