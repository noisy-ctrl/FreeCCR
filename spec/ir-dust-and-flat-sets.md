# IR dust removal and flat-field sets

Status: BUILT 2026-11-19 (§2–§5); Capture → Shoot Flat Fields… (end of §5) not yet.

Decisions (the user, 2026-11-19): the IR shot is part of each Shoot; dust
found in it is FLAGGED, not healed ("the dust removal tool button changing
colour or with a red notification dot. Inside dust removal, click to
continue, with a before and after toggle … a paint tool to delete selected
areas that are false positives"); flats by format and light; flats TRACK the
image, by the carrier's edge ("The frame will 100% move by more than 1%").

The user, 2026-11-19, after his IR tests on the GFX 50S II (Scanlight's IR
LED, XP2): "I can clearly see the dust in all. I think a high ISO and
shortish shutter speed is a win-win like 4000 ISO at 10 seconds. Can we build
some kind of threshold that isolates the dust? … We might need to create an
IR flat field too. The hotspot seems to be IR reflecting off the lens. … any
flat field needs to track the image? Can we also make a RGB option in the
flat field settings. It makes sense to have one for each channel and that be
referenced in combine? … make a 120/35mm and also versions with
white/RGB/IR flat field? These should also be tied to the kind of
film/capture? Part of the unified workflow."

## 1. What the tests measured

Files: R0011 001 (white light, 1/10 s ISO 100) + 002 (IR, 3 s ISO 320);
R0016 003 (IR, no film: 8 s ISO 3200); R0015 019 (IR, 5 s ISO 4000).

- IR reaches the sensor about 15 stops below white light (the camera's
  IR-cut filter). Signal per 2×2 photosite block: 002 SNR 11, 019 SNR 15,
  003 SNR 23. The noise is mostly photon noise; ISO raises the level, not the
  light, so 10 s → 30 s gains only √3. ISO 3200–4000 at 5–10 s is a sensible
  setting.
- The lens IR hotspot is 1.8× brighter in the centre than the corners and
  fixed to the optics (it was the same in a dark room). Dividing by an IR flat
  (003) removes it.
- XP2's dye still shows in IR at 16–24 % of its white-light density
  (crosstalk). With IR alone a threshold catches dust AND dense fine detail
  (scaffolding, window bars). Dividing by the white-light frame raised to that
  power (fitted per frame) cancels the picture: on 001/002 at 6σ, 569 → 251
  detections, elongated false ones 28 → 6, at 8σ 142 → 32 with none
  elongated. Real specks line up between the two shots within 1 block.
- Dust blocks IR less than visible light (50–90 % of the visible shadow, small
  specks least), so the smallest specks are the hardest; more IR signal helps
  them most.

## 2. The IR shot (Capture)

- Shoot gets an **IR for dust** option (Capture → Shoot row, remembered): after
  the colour shot(s) the IR LED lights alone at full and the camera fires at
  the IR exposure (its own shutter / ISO pickers, default 8 s, ISO 3200).
- Files: always `…_4IR` (after `_1RED/_2GREEN/_3BLUE` or `_WHITE`/`_BW`; it
  sorts after the colours); the capture log entry gets `"ir": true`, its
  slots end with `ir` (`capture_log.slots_of`) and `files.ir` names the file,
  so a shutter-cable shot with the camera's own name is known too.
- Capture → the Film base box (shown with a USB camera's exposure lists):
  **IR for dust** (`ir_shot_cb`, `live/ir_shot`, default off) with the IR
  shot's shutter and ISO from the camera's own lists (`ir_shutter_combo`,
  `ir_iso_combo`, `live/ir_shutter` / `live/ir_iso`, default the nearest to
  8 s and ISO 3200). USB shutter only (the exposure must be set); needs the
  Scanlight. `CaptureSequence.start(..., ir=)` queues an `"ir"` step after
  the colours: the IR LED alone at 255, `fire({"shutter", "iso"})`; after it
  a trichrome goes back to white as before, a single shot to the levels it
  had (`light.levels()`); Capture puts the camera's own shutter / ISO back
  (`_shoot_exp_before`, as for a film-base Shoot).
- On import / Hot Folder Tether the IR file is **attached** to its shot's frame
  (`img.ir_source`, catalogue key), never merged or loaded as a frame of its
  own. `capture_log.split_ir` takes IR files out of a batch before anything
  else sees it (`load_images_from_files`, `_route_existing`);
  `CCRBackend._attach_ir_shots` (in `_commit_load`) pairs each frame with
  `capture_log.ir_for(frame files, the IR files opened)`: the log's `ir` for
  that shot, else a `_4IR` of the same shot name among the files opened or
  beside the frame (so opening only the colour files finds it). Hot Folder
  Tether: `_route_tether_file` sends an IR file to `_tether_ir`, which waits
  (`_tether_ir_waiting`) until its frame is there (`_on_capture` →
  `_take_waiting_ir`). Right-click a thumbnail → **Attach IR Shot…** /
  **Change IR Shot…** pairs one by hand.

## 3. Finding the dust (`core/ir_dust.py`)

On 2×2 photosite blocks of both RAWs (no demosaic, camera-native linear):

1. `L = ln(IR) − ln(IR flat)` (the IR flat of §5; none → a heavily smoothed
   copy of the IR frame stands in for the hotspot).
2. Crosstalk: `L −= k·ln(visible)`, k fitted per frame (0–0.4, the value that
   leaves the least high-pass variance). The visible frame is the shot's white
   / B&W frame, or the sum of a trichrome's three.
3. Register the IR to the visible frame (phase correlation, ≤ 4 blocks;
   measured < 1).
4. Background = grey closing of `L` (disc of radius `r`, default 5 blocks,
   larger dust is rare), smoothed; `d = L − background` (≤ 0 at dust).
5. `d` is centred on its local level (the background is an upper
   envelope, so clean film sits a little below 0) and divided by a LOCAL
   noise map (the hotspot leaves the middle less noisy than the corners),
   both taken with dust clipped out. Seeds at `z < −T` (Sensitivity 0..100 →
   T 10σ..4σ, default 50 = 7σ), grown by hysteresis into `z < −T/2`.
   Measured on 001/002: of what 7σ finds, 92 % is also a dark speck (> 22 %
   dip) in the white-light shot, 90 % at 6σ, 52 % at 5σ. On the user's own
   circled IR frame (R0015 008, his edited JPEG, IR alone, no white-light
   shot): the strictest setting finds 32 of his 35 circles plus 33 spots he
   didn't circle, most of them compact dark specks too.
   A component over 0.2 % of the frame (at least 1500 blocks) is picture.
6. Each component → a dust spot of kind `"ir"`: compact → its centroid and a
   radius (area-equivalent × 1.35 + 1 block); long (length/width > 3.5, a
   hair) → a polyline of slices along its main axis, radius from its width.
   Coordinates are normalised to the RAW's visible area, which is the frame's
   decode (rawpy applies no crop margins: checked on the GFX's RAF), so the
   existing heal, editing, undo and catalogue take them as they are; the heal
   treats `"ir"` like `"auto"` (a generous hole). Slices aren't searched yet.
7. Speed (12.8 M blocks): the wide blurs run at a quarter size
   (`smooth_field`), registration on the middle 1024²: ~1.2 s a frame after
   the two RAW reads (~0.6 s each, cached).

## 4. Flagged, then healed (the Dust panel)

- Finding runs in the background as soon as a frame has its IR shot (after a
  load, `DustRemovalPanel.find_new_ir`; on Hot Folder arrival; on Attach),
  one frame at a time (`find_ir`, `_IrFindWorker`), and only FLAGS:
  `img.ir_dust_pending` (spots, catalogue key, in the undo snapshot so
  undoing a Heal flags them again) + `img.ir_dust_sens`; the map stays in
  memory (`img._ir_dust_map`) so Sensitivity re-thresholds without a re-read.
- **The red dot**: the toolbar's Dust removal icon carries a red dot while the
  frame on screen has dust flagged (`ImagePreview.refresh_dust_badge`,
  `theme.with_badge`), with a line in its tooltip.
- In Dust removal, an **IR Dust** section first: a status line (how many
  specks, outlined in AMBER on the picture, `_draw_ir_pending`), Attach /
  Change IR Shot…, Find Again, **Sensitivity**, **Heal Them** (one undo
  step; replaces earlier IR spots, leaves hand and AI spots), **Before**
  (the picture as it was just before Heal, a display swap of the pixmaps
  kept at Heal, preview and hi-res, `_dust_before`; never a re-render, so the
  heal's choice of patches is untouched) and **Erase Spots** (a brush that
  removes every spot it touches, healed of any kind or flagged; one undo
  step; drawn light blue).

## 5. Flat-field sets

**What a flat holds.** A field profile (`.ffc`, as now) records in `meta`:
`light` (white / rgb / red / green / blue / ir), `format` (a
`film_formats` key), the carrier opening it was shot through (`opening`:
its four sides as lines in the flat's normalised coordinates, found by
`guide_tracker.find_opening` on the flat), and the camera, lens and aperture
it already records. Shot with the EMPTY CARRIER in place, at that format's
rail height: the flat then holds the carrier's own effects (height, light
bouncing off its edges, its glass), and its opening is the reference the
tracking needs. Without an opening (shot on the bare light) it is applied
untracked, as now.

**RGB.** Three flat shots, one per LED, are merged like a trichrome scan
(`ccr_merge.merge_raw_channels`, the same grid as the frames) and measured as
one profile: each channel's gain comes from its own light, applied to the
merged frame — the same as flattening each source before the merge, since
the merge takes channel c from the frame lit by colour c.

**Chosen per frame** (`flat_field.profile_for(img)`): the light from the
frame (a merge → rgb; a B&W frame → the B&W light it was shot under, else
white; a colour frame → white; the IR shot → ir), the format from the frame
(`img.film_format` → the capture guide's → the roll's); the best match in
the library (light AND format, else light alone); none → the single active
profile, byte for byte as now. Shown per frame in the info line.

**Tracking by the carrier's edge** (the user: "The frame will 100% move by
more than 1%"). The camera moves on the rail; the light and the carrier
don't, and the film sits in the carrier, so the carrier opening is the
anchor. For each frame the opening is found on its raw preview (cached on
the frame with its scan's identity); the similarity (scale about the
centre, shift, turn) that maps the flat's opening onto the frame's is
applied to the gain map before multiplying (`apply_field(..., warp=)`),
by opposite sides as `GuideTracker` does. Limits: no opening found in the
frame (edges cropped out, a slide mount) → the flat untracked, said in the
info line; a move > 15 % or a turn > 3° is refused as a mis-find. The lens'
own fall-off stays with the sensor, so it is moved slightly wrongly with
the light's; for the rail's few per cent that error is far below the
flat's own variation (to measure on his flats).

**Built** (`core/flat_field.py` §7): `build_tagged_profile` (finds the
opening with `guide_tracker.find_opening` on an 8-bit view of the decode,
fills the mask from the inside with `fill_outside` — the edge rows/columns of
the opening carried outward — and records `light`, `format`, `opening`);
`profile_for(img)` (light: a merge → rgb, `img.scan_light`, else white;
format: `img.film_format` → the capture guide's; exact format, else a
format-less flat, else any of that light; with no tagged flat in the library
the active profile, byte for byte); `track` = `guide_tracker.solve` from the
flat's opening to the frame's, refused outside scale 0.85–1.15 or a shift
over 15 %; `tracked_profile` warps the map (cached per move);
`CCRImage._apply_field_correction` is now per frame and keeps the frame's
opening (`_flat_opening_memo`, `img.flat_opening`), found once per scan;
`library_signature` joins `active_field_signature`, so adding or removing a
flat flags the frames (⚠, Replace…). The backend loads the library at start
and after a save/delete (`refresh_field_library`). The wizard (Settings →
Create Field Correction Profile…) has **Light** and **Format**; RGB takes
the three shots (ordered by the light each was lit by, `order_rgb`) and
merges them with `it8_profile.decode_target_merged`. The library row says
"White light · 6x4.5 · follows the carrier"; Settings notes how many flats
are chosen per frame. The IR flat, when there is one for the frame's
format, is divided out of the IR shot before the dust search
(`ir_dust.ir_flat_for`).
Measured on a synthetic rig (an off-centre hotspot × fall-off, the camera
moved by scale 0.95–1.06 and shifts of 2–4 %): unevenness inside the opening
0.185 raw, 0.036–0.045 with the flat untracked, 0.009 tracked (the flat on
its own shot: 0.009); the move found within 0.0007.
Not yet: `img.scan_light` is set by nothing (a B&W frame under one LED uses
the white flat); Capture → Shoot Flat Fields… below.

**Capture → Shoot Flat Fields…** (not built; USB camera + Scanlight): asks the format
(the roll's by default) and which lights (white, RGB, IR); with the film out
and the empty carrier in, for each light it finds an exposure that puts the
brightest part of the opening at ~60 % of the RAW's range (the film-base
Calibration machinery), shoots it (two shots averaged for IR, which is
noisy), builds and saves the profile into the library with its light,
format and opening, and puts the light and the camera back.

## 6. Tests (planned)

- Synthetic: a picture with fine dense detail + round specks and hairs; IR =
  picture^k × specks × hotspot + noise: IR-only finds the detail, IR + visible
  doesn't; specks found at the chosen σ, hairs as polylines; spots normalised
  correctly; k recovered.
- The user's files (skipped when absent): 001/002 finds the specks in the
  measured crops.
- Pairing by log and by name; the IR file never becomes its own frame.
- Flat sets: chosen by light and format; the trichrome flat's channels come
  from their own lights; no set → the active profile byte for byte.
