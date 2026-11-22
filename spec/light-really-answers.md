# The light is "Responding" only when the light answers

The user, 2026-11-22: Capture showed the Scanlight connected and
"Responding", yet nothing he sent lit it. A test script found the light's
controller up on USB but its firmware neither reading nor reporting (it had
lost its power and come back in a bad state; power first, then USB, fixed it).
FreeCCR counted ANY bytes from the port as the light answering, so a stuck
light, or another board on a `usbmodem` port (the focus rail's ESP32 answers
HELLO with `ORP 1 …`), looked connected.

## Behaviour (`core/light_link.py`)
- **Only a light's own answer counts**: a Scanlight packet with a known
  header (`capture_light.D2H_KNOWN`: temperature 1, supply 2, the firmware
  answer 3) or an Open Light Protocol `HELLO`/`STATUS` reply sets
  `_heard_at`. "Responding" (state `ok`) needs one within `STALE_S`.
- **Something else answering**: bytes but no light's answer by the link
  check (after the one DTR retry) → `silent` with `NOT_LIGHT_TEXT` ("…is it
  the right port?"); no bytes at all keeps `SILENT_TEXT`.
- **A light that stops reporting**: a Scanlight reports five times a second,
  so once it has sent reports (`_reports`), `STALE_S` (3 s) without any known
  packet → `silent` with `STOPPED_TEXT` (switch it off at its power, then
  power first, USB second). Reports coming back → `ok` again. Not for Open
  Light Protocol lights, which only answer when asked.

Nothing else changes: the port stays open, levels are still sent, the
monitor shows the detail as its status line.

## Tests (`tests/test_live_capture_light.py`)
Another board's text is not taken for the light and ends `NOT_LIGHT_TEXT`;
a light whose reports stop is `STOPPED_TEXT` after `STALE_S` and `ok` again
when they resume; the firmware answer alone counts.
