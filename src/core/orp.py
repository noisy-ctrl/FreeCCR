"""Open Rail Protocol (ORP) v1: a plain-text protocol a stepper-motor focus
rail speaks over USB serial so FreeCCR can move it (docs/open-rail-protocol.md,
spec/focus-rail.md). Qt-free. The line rules are the Open Light Protocol's
(`core.olp`): ASCII, one message per line, bytes outside printable ASCII
ignored.

    HELLO                  -> ORP 1 STEPS_PER_MM=800 STEPS_PER_REV=3200 MIN=0 MAX=80000
                              HOME=SWITCH NAME=...  (one line)
    STATUS                 -> STATUS POS=1234 MOVING=0 HOMED=1 ENABLED=1
    GOTO 1500 ID=7         -> OK   ... later: DONE ID=7 POS=1500
    MOVE -200 ID=8         -> OK   ... later: DONE ID=8 POS=1300   (from the target)
    STOP                   -> OK   (the move ends: DONE ... STOPPED=1)
    ZERO                   -> OK | ERR moving
    HOME ID=9              -> OK | ERR no home   ... later: DONE ID=9 POS=200 (or FAIL=...)
    SPEED 3200 ACCEL=20000 -> OK
    ENABLE 0 / ENABLE 1    -> OK

DONE and STATUS lines also come unprompted, between replies.
"""
from core import olp

VERSION = 1
HELLO = olp.HELLO
STATUS = b"STATUS\n"
STOP = b"STOP\n"
ZERO = b"ZERO\n"
NEWLINE = olp.NEWLINE
LineParser = olp.LineParser
clean_line = olp.clean_line

# Fields that take the rest of the line (so they can hold spaces).
REST_KEYS = ("NAME", "FAIL")

# USB vendor IDs: ESP32 boards first (Espressif's native USB, then the USB
# bridges ESP32 dev boards carry), the Scanlight's RP2040 last among USB.
ESP32_VIDS = (0x303A, 0x10C4, 0x1A86, 0x0403)
LIGHT_VIDS = frozenset({0x2E8A})


def _int(v, default=None):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _id_part(move_id):
    return f" ID={int(move_id)}" if move_id is not None else ""


def goto_line(pos, move_id=None):
    return f"GOTO {int(round(pos))}{_id_part(move_id)}\n".encode("ascii")


def move_line(steps, move_id=None):
    return f"MOVE {int(round(steps))}{_id_part(move_id)}\n".encode("ascii")


def home_line(move_id=None):
    return f"HOME{_id_part(move_id)}\n".encode("ascii")


def speed_line(steps_per_s, accel=None):
    s = max(1, int(round(steps_per_s)))
    a = f" ACCEL={max(1, int(round(accel)))}" if accel else ""
    return f"SPEED {s}{a}\n".encode("ascii")


def enable_line(on):
    return f"ENABLE {1 if on else 0}\n".encode("ascii")


def parse_fields(text):
    """KEY=VALUE tokens (keys upper-cased). NAME= and FAIL= take the rest of
    the line, so they can have spaces."""
    out = {}
    rest = text
    up = rest.upper()
    cut = len(rest)
    for key in REST_KEYS:
        i = up.find(key + "=")
        if i >= 0 and (i == 0 or rest[i - 1] == " "):
            cut = min(cut, i)
    tail = rest[cut:]
    rest = rest[:cut]
    # the rest-of-line field (only one is expected on a line)
    if tail:
        k, _, v = tail.partition("=")
        out[k.strip().upper()] = v.strip()
    for tok in rest.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


def _flag(f, key):
    return str(f.get(key, "0")).strip() not in ("", "0")


def parse_reply(line):
    """One of:
    ("hello", {"version", "steps_per_mm", "steps_per_rev", "min", "max", "home",
               "name"})
    ("status", {"pos", "moving", "homed", "enabled", "driver"} - the keys given)
    ("done", {"id", "pos", "stopped", "limit", "fail"})
    ("ok", "") | ("err", text) | ("light", info): an Open Light Protocol
    answer (that port has a light on it) | None for anything else."""
    text = clean_line(line)
    if not text:
        return None
    head, _, tail = text.partition(" ")
    head = head.upper()
    if head == "ORP":
        version, _, rest = tail.partition(" ")
        v = _int(version)
        if v is None:
            return None
        f = parse_fields(rest)
        try:
            spm = float(f.get("STEPS_PER_MM", "0"))
        except ValueError:
            spm = 0.0
        try:                       # a belt makes it fractional (2133.33)
            spr = float(f.get("STEPS_PER_REV", "0"))
        except ValueError:
            spr = 0.0
        home = f.get("HOME", "NONE").upper()
        if home not in ("SWITCH", "STALL", "NONE"):
            home = "NONE"
        return ("hello", {"version": v, "steps_per_mm": max(0.0, spm),
                          "steps_per_rev": max(0.0, spr),
                          "min": _int(f.get("MIN")), "max": _int(f.get("MAX")),
                          "home": home.lower(), "name": f.get("NAME", "")})
    if head == "STATUS":
        f = parse_fields(tail)
        out = {}
        if "POS" in f and _int(f["POS"]) is not None:
            out["pos"] = _int(f["POS"])
        for key, name in (("MOVING", "moving"), ("HOMED", "homed"), ("ENABLED", "enabled"),
                          ("DRIVER", "driver")):
            if key in f:
                out[name] = _flag(f, key)
        return ("status", out)
    if head == "DONE":
        f = parse_fields(tail)
        return ("done", {"id": _int(f.get("ID")), "pos": _int(f.get("POS")),
                         "stopped": _flag(f, "STOPPED"), "limit": _flag(f, "LIMIT"),
                         "fail": f.get("FAIL", "")})
    if head == "OK":
        return ("ok", "")
    if head == "ERR":
        return ("err", tail)
    if head == "OLP":
        reply = olp.parse_reply(text)
        if reply is not None and reply[0] == "hello":
            return ("light", reply[1])
    return None


def rank_port(device, vid=None):
    """Sort key for the rail's port list: an ESP32's USB IDs first, then any
    USB serial port, then the rest, a light's controller and the Mac's own
    ports last."""
    from core import capture_light as cl
    name = str(device).lower()
    if vid in ESP32_VIDS:
        return (-1, ESP32_VIDS.index(vid), name)
    base = cl.port_rank(device)
    if vid in LIGHT_VIDS:
        return (1, 0, name)
    if base[0] == 1 and vid is not None:
        return (0, 0, name)
    return (base[0], 0, name)


def list_ports():
    """Serial port device names, an ESP32's first. [] without pyserial."""
    from core import capture_light as cl
    if not cl.SERIAL_AVAILABLE:
        return []
    try:
        infos = list(cl.serial.tools.list_ports.comports())
    except Exception:
        return []
    infos.sort(key=lambda p: rank_port(p.device, getattr(p, "vid", None)))
    return [p.device for p in infos]


def mm_text(steps, steps_per_mm):
    """A position for people: millimetres to 1 µm, or steps when the rail
    doesn't say how many make a millimetre."""
    if steps is None:
        return "—"
    if steps_per_mm and steps_per_mm > 0:
        return f"{steps / steps_per_mm:.3f} mm"
    return f"{int(steps)} steps"


def turn_text(um, steps_per_mm, steps_per_rev):
    """A distance as an angle of the screw ("1.8°", "18°"), or "" when the
    rail doesn't say its steps per mm and per turn."""
    if not (steps_per_mm and steps_per_mm > 0 and steps_per_rev and steps_per_rev > 0):
        return ""
    deg = abs(float(um)) / 1000.0 * steps_per_mm / steps_per_rev * 360.0
    return f"{deg:.1f}°" if deg < 10 else f"{deg:.0f}°"


def lead_mm(steps_per_mm, steps_per_rev):
    """Millimetres of travel per screw turn, or None."""
    if steps_per_mm and steps_per_mm > 0 and steps_per_rev and steps_per_rev > 0:
        return steps_per_rev / steps_per_mm
    return None


def jog_text(um, sign):
    """A jog button's label, signed, in mm, to the step's precision:
    20 µm -> "+0.02", 200 -> "+0.2", 5 -> "+0.005"."""
    mm = abs(float(um)) / 1000.0
    txt = f"{mm:.3f}".rstrip("0").rstrip(".")
    if txt in ("", "0"):
        txt = "0"
    return ("+" if sign >= 0 else "−") + txt
