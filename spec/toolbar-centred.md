# The canvas toolbar: Film and Capture dead centre, Export boxed

The user, 2026-11-20: "Can we also make the Export button in top bar similar
to that of the right one [the panel's Export…] so it stands out more. With
Auto crop, unconvert and convert moved to the left to make the top bar more
symmetrical - keeping film and capture buttons dead centre." Three drafts
(now / B: the conversion words midway between Capture and Export / C: on the
left after the tools) answered: "Middle looks closer to what I want" (B).

## Goals

- **Film Presets + Capture dead centre** of the toolbar at every width where
  the toolbar has room to spare (it is the toolbar's centre, which is the
  canvas's: the toolbar spans it).
- **Auto Crop, Un-convert, Convert midway** between Capture and Export: equal
  space either side of the three.
- **Export boxed** like the panel's Export… (`theme.style_button(...,
  "primary")`): the raised grey, the strong border, bold words, the same
  radius; hover / pressed / disabled as the panel's. As tall as the zoom box
  beside it.
- Everything else as before: the picture tools packed on the left, the zoom
  box last, Export right before it.

## Non-goals

- Moving any tool between groups, or changing what a button does.
- Centring when there is no room: at the canvas's minimum width the slack is
  gone and the groups simply meet (each keeps its minimum gap).

## Layout

```
[pictures] [bal L] (push)(push) Film Capture [bal R½] (push≥8) Auto Crop ·12· Un-convert Convert (push≥12) [bal R½] [Export] ·8· [Fit] ·6·
```

- Four stretches (`capture_push_2`, `capture_push`, `convert_push`,
  `export_push`), all Expanding with no size hint: Qt's toolbar layout gives
  equal-stretch items equal shares of the slack, so each takes a quarter.
  `convert_push` and `export_push` have a minimum of `CONVERT_GAP_PX` (8) /
  `EXPORT_GAP_PX` (12, a fixed 24 before), the gaps that stay at the
  narrowest window: the boxed Export marks itself, and the toolbar must still
  fit the canvas's 900 px minimum in a Mac-wide font.
- Two quarters on each side make the centre group dead centre exactly when
  the fixed widths either side are equal. They aren't (the pictures and the
  words measure differently, and fonts differ by platform), so a **balance**
  spacer pads the lighter side: `_balance_left` before the stretches on the
  left, or `_balance_right_a` / `_balance_right_b` (half each, either side of
  the conversion words, so they stay midway) on the right.
- The balance is MEASURED, not computed from sizes: `_balance_toolbar()`
  reads where the centre group's middle is against the toolbar's, `d` px
  right of it, and moves the signed balance `b` (right minus left) by `2d`
  (one px of balance moves the centre group half a px, as half the slack is
  on each side). `d` doesn't depend on the toolbar's width, so one step
  settles it and resizing never changes it; it runs (deferred, coalesced) on
  the toolbar's Resize and LayoutRequest events, and measures only while
  nothing is in the overflow arrow, the whole balance is spent and every
  stretch has slack beyond its minimum, and only for |d| ≥ 1 px.
- The balance is spent only from room the toolbar has to spare (its width
  minus its size hint without the balance): at the narrowest canvas it
  shrinks, so Export and the zoom box never go into the overflow arrow, and
  it comes back in full when there is room.
- Measured here (Linux UI font): balance 47 px on the right; centred to
  0.5 px at 1264, 1404 and 1804 px toolbars; at the 900 px minimum the
  toolbar needs exactly 900 and the centre group sits 2.5 px off.
- Export: `theme.style_tool_button(btn, "primary")` (the panel's primary QSS
  for a QToolButton, padding 2 px 10 px), width = its bold words +
  `EXPORT_BOX_PAD` (24), height = the zoom box's.

## Tests

- `tests/test_export_placement.py`: at 1600 and 2400 px windows the centre
  group's middle is within 1 px of the toolbar's; the conversion words are
  midway (gaps within 2 px); Export is right before the zoom box and boxed
  (border, bold); the minimum gaps hold; the order of actions.
- `tests/test_capture_toolbar_button.py`: Film and Capture between the
  stretches, Auto Crop after the convert stretch and its balance half.
- `tests/test_panel_width.py`: the toolbar still fits the canvas's minimum
  width in a Mac-wide font.
