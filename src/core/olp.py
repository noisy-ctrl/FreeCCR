"""Open Light Protocol (OLP) v1: a plain-text protocol any homemade or
commercial light can speak over USB serial so FreeCCR can drive it
(docs/open-light-protocol.md, spec/open-light-protocol.md). Qt-free.

One command per line, ASCII, ending in a newline. FreeCCR sends:

    HELLO                          -> OLP 1 CH=R,G,B,W,IR SHUTTER=1 FOCUS=1 NAME=...
    SET R=200 G=0 B=0 W=0 IR=0     -> OK          (0..255; keys it lacks: ignored)
    OFF                            -> OK
    SHUTTER 300                    -> OK | ERR ... (pulse the release, in ms)
    FOCUS 1 / FOCUS 0              -> OK | ERR ... (hold / let go the half-press)
    STATUS                         -> STATUS TEMP=31.5 VBUS=12.1  (any keys)

A light may also send STATUS lines on its own. It ignores bytes outside
printable ASCII (other than CR and LF), so it can share a port scan with
binary-protocol lights.
"""

VERSION = 1
# A newline first: it ends any partial line another protocol's bytes left in
# the light's buffer (a light ignores empty lines and answers junk with ERR).
NEWLINE = b"\n"
HELLO = b"\nHELLO\n"
OFF = b"OFF\n"
STATUS = b"STATUS\n"
MAX_LINE = 512
SHUTTER_MAX_MS = 10000
CHANNEL_KEYS = (("r", "R"), ("g", "G"), ("b", "B"), ("w", "W"), ("ir", "IR"))


def _byte(v):
    try:
        return max(0, min(255, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def set_line(levels):
    """SET with every channel FreeCCR knows (a light ignores the ones it
    hasn't got)."""
    levels = levels or {}
    parts = [f"{key}={_byte(levels.get(k, 0))}" for k, key in CHANNEL_KEYS]
    return ("SET " + " ".join(parts) + "\n").encode("ascii")


def shutter_line(ms):
    ms = max(1, min(SHUTTER_MAX_MS, int(round(float(ms)))))
    return f"SHUTTER {ms}\n".encode("ascii")


def focus_line(on):
    return f"FOCUS {1 if on else 0}\n".encode("ascii")


def clean_line(raw):
    """Printable ASCII only (what a light keeps of a line)."""
    return "".join(ch for ch in raw if " " <= ch <= "~").strip()


def parse_fields(text):
    """KEY=VALUE tokens (keys upper-cased); NAME= takes the rest of the
    line, so a name can have spaces."""
    out = {}
    rest = text
    i = rest.upper().find("NAME=")
    if i >= 0:
        out["NAME"] = rest[i + 5:].strip()
        rest = rest[:i]
    for tok in rest.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


def parse_reply(line):
    """("hello", info) | ("status", {"temp": °C, "vbus": V, ...}) | ("ok", "")
    | ("err", text) | None for anything else."""
    text = clean_line(line)
    if not text:
        return None
    head, _, tail = text.partition(" ")
    head = head.upper()
    if head == "OLP":
        version, _, rest = tail.partition(" ")
        try:
            v = int(version)
        except ValueError:
            return None
        f = parse_fields(rest)
        chans = [c.strip().lower() for c in f.get("CH", "").split(",") if c.strip()]
        return ("hello", {"version": v, "channels": chans,
                          "shutter": f.get("SHUTTER", "0") == "1",
                          "focus": f.get("FOCUS", "0") == "1",
                          "name": f.get("NAME", "")})
    if head == "STATUS":
        f = parse_fields(tail)
        out = {}
        for key, name in (("TEMP", "temp"), ("VBUS", "vbus")):
            if key in f:
                try:
                    out[name] = float(f[key])
                except ValueError:
                    pass
        return ("status", out)
    if head == "OK":
        return ("ok", "")
    if head == "ERR":
        return ("err", tail)
    return None


class LineParser:
    """Bytes in, whole lines out (bytes outside printable ASCII dropped, CR
    ignored, a line capped at MAX_LINE so junk can't grow it)."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, chunk):
        out = []
        for b in bytes(chunk or b""):
            if b == 0x0A:
                out.append(self.buf.decode("ascii", "ignore"))
                self.buf.clear()
            elif 0x20 <= b <= 0x7E:
                if len(self.buf) < MAX_LINE:
                    self.buf.append(b)
        return out
