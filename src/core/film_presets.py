"""Film presets: one ID per film, push/pull and version, carrying what a roll
of that film needs across the workflow (spec/film-presets.md).

A preset is a plain dict:
  id       stable random hex; frames, sessions and film bases point at it
  film     the film and speed as typed ("Portra400", "XP2")
  push     -2 .. +2 (0 = box speed)
  version  1, 2, ...
  notes    free text ("Barcelona 2022")
  when     ISO date made
  clip     the slider settings in Copy Settings' clipboard form, without the
           geometry (crop, rotation, flips, fine rotation); None until written
  chosen   the paste-row ids the preset carries

The ID is (film case-folded, push, version) and is unique. Qt-free: the
widgets store the list as JSON in QSettings `film/presets`."""
import copy
import datetime
import json
import re
import uuid

PUSHES = (-2, -1, 0, 1, 2)
STORE_KEY = "film/presets"
GEOMETRY = ("crop", "rotation", "flip_h", "flip_v", "fine_rotation")
# Temperature and Tint are set per frame (auto white balance): a preset only
# keeps them when the user ticks them.
UNTICKED_BY_DEFAULT = ("adj:temperature", "adj:tint")
# a flat field per scan type, by its file name in the flat library, or
# "none" (spec/preset-flat-fields.md); a missing key is Automatic
FLAT_SCANS = ("trichrome", "colour", "bw")
NO_FLAT = "none"


# --------------------------------------------------------------------------- #
# IDs and labels
# --------------------------------------------------------------------------- #
def clamp_push(p):
    try:
        p = int(p)
    except (TypeError, ValueError):
        return 0
    return max(-2, min(2, p))


def push_text(p):
    """'+1', '−2' (a real minus), '' at 0."""
    p = clamp_push(p)
    if p > 0:
        return f"+{p}"
    if p < 0:
        return f"−{-p}"
    return ""


def id_key(film, push, version):
    return (str(film or "").strip().casefold(), clamp_push(push), max(1, int(version or 1)))


def key_of(p):
    return id_key(p.get("film"), p.get("push"), p.get("version"))


def label(p, notes=True):
    """'Colorplus200 +1 V2 (Barcelona 2022)'."""
    if not p:
        return ""
    bits = [str(p.get("film") or "").strip() or "Film"]
    pt = push_text(p.get("push"))
    if pt:
        bits.append(pt)
    bits.append(f"V{max(1, int(p.get('version') or 1))}")
    out = " ".join(bits)
    n = str(p.get("notes") or "").strip()
    if notes and n:
        out += f" ({n})"
    return out


def file_film(p):
    """The film part of a capture's file names: 'Portra400', 'Colorplus200Push1',
    'HP5Pull2' (letters and digits only; version and notes left out)."""
    if not p:
        return ""
    film = re.sub(r"[^0-9A-Za-z]+", "", str(p.get("film") or ""))
    push = clamp_push(p.get("push"))
    if push > 0:
        film += f"Push{push}"
    elif push < 0:
        film += f"Pull{-push}"
    return film


_LABEL_RE = re.compile(
    r"^\s*(?P<film>.*?)"
    r"(?:\s+(?P<push>[+\-−]\s?[12]))?"
    r"(?:\s+[Vv](?P<ver>\d+))?"
    r"(?:\s*\((?P<notes>[^()]*)\))?\s*$")


def parse_label(text):
    """A name -> {film, push, version, notes}: a trailing ' +1' / ' -1' /
    ' V2' / ' (notes)' are read as push, version and notes."""
    t = str(text or "").strip()
    m = _LABEL_RE.match(t)
    if not m or not m.group("film").strip():
        return {"film": t or "Film", "push": 0, "version": 1, "notes": ""}
    push = 0
    if m.group("push"):
        s = m.group("push").replace(" ", "").replace("−", "-")
        push = clamp_push(int(s))
    return {"film": m.group("film").strip(), "push": push,
            "version": int(m.group("ver") or 1), "notes": (m.group("notes") or "").strip()}


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #
def new_id():
    return uuid.uuid4().hex[:12]


def make(film, push=0, version=1, notes="", clip=None, chosen=(), when=None, pid=None,
         flats=None, auto_radius=False):
    return clean({"id": pid or new_id(), "film": film, "push": push, "version": version,
                  "notes": notes, "clip": clip, "chosen": list(chosen or ()),
                  "when": when or datetime.date.today().isoformat(),
                  "flats": flats or {}, "auto_radius": bool(auto_radius)})


def _clean_flats(f):
    if not isinstance(f, dict):
        return {}
    return {k: str(v).strip() for k, v in f.items()
            if k in FLAT_SCANS and isinstance(v, str) and str(v).strip()}


def _clean_clip(c):
    if not isinstance(c, dict):
        return None
    c = copy.deepcopy(c)
    for k in GEOMETRY:
        c.pop(k, None)
    if not isinstance(c.get("adjustments"), dict):
        c["adjustments"] = {}
    if not isinstance(c.get("flags"), dict):
        c["flags"] = {}
    c.setdefault("profile", "color")
    return c


def clean(p):
    """A tidy record, or None when it has no film."""
    if not isinstance(p, dict):
        return None
    film = str(p.get("film") or "").strip()
    if not film:
        return None
    try:
        ver = max(1, int(p.get("version") or 1))
    except (TypeError, ValueError):
        ver = 1
    pid = str(p.get("id") or "").strip() or new_id()
    chosen = [str(x) for x in (p.get("chosen") or []) if str(x) not in GEOMETRY]
    return {"id": pid, "film": film, "push": clamp_push(p.get("push")), "version": ver,
            "notes": str(p.get("notes") or "").strip(),
            "when": str(p.get("when") or ""),
            "clip": _clean_clip(p.get("clip")), "chosen": chosen,
            "flats": _clean_flats(p.get("flats")),
            # measure each frame's sharpening radius (spec/preset-auto-radius.md)
            "auto_radius": bool(p.get("auto_radius"))}


def normalise(data):
    """A list of clean presets: unique ids and unique IDs (the first kept)."""
    if isinstance(data, str):
        try:
            data = json.loads(data) if data.strip() else []
        except ValueError:
            data = []
    if isinstance(data, dict):
        data = data.get("presets", [])
    out, ids, keys = [], set(), set()
    for p in data or []:
        c = clean(p)
        if c is None or key_of(c) in keys:
            continue
        if c["id"] in ids:
            c["id"] = new_id()
        ids.add(c["id"])
        keys.add(key_of(c))
        out.append(c)
    return out


def dumps(presets):
    return json.dumps(normalise(presets))


def find(presets, pid):
    if not pid:
        return None
    return next((p for p in presets or [] if p.get("id") == pid), None)


def find_key(presets, film, push, version):
    k = id_key(film, push, version)
    return next((p for p in presets or [] if key_of(p) == k), None)


def taken(presets, film, push, version, but=None):
    """Is that ID used by a preset other than `but` (an id)?"""
    p = find_key(presets, film, push, version)
    return p is not None and p.get("id") != but


def next_version(presets, film, push):
    """1 + the highest version of that film and push (1 when there is none)."""
    k = (str(film or "").strip().casefold(), clamp_push(push))
    vs = [p["version"] for p in presets or [] if key_of(p)[:2] == k]
    return max(vs) + 1 if vs else 1


def films(presets):
    """The films used, most recent first, each once (as first typed)."""
    seen, out = set(), []
    for p in sorted(presets or [], key=lambda p: p.get("when") or "", reverse=True):
        f = p["film"]
        if f.casefold() not in seen:
            seen.add(f.casefold())
            out.append(f)
    return out


def ordered(presets):
    """Newest first, then by label."""
    return sorted(presets or [], key=lambda p: (_neg(p.get("when") or ""), label(p).casefold()))


def _neg(s):
    return tuple(-ord(ch) for ch in s)


def add(presets, p):
    """`presets` + `p` (refused, returning the list unchanged, if its ID is
    taken). Returns (presets, the stored record or the existing one)."""
    lst = normalise(presets)
    c = clean(p)
    if c is None:
        return lst, None
    have = find_key(lst, c["film"], c["push"], c["version"])
    if have is not None:
        return lst, have
    lst.append(c)
    return lst, c


def ensure(presets, film, push, version, notes=""):
    """The preset with that ID, made when there is none. (presets, preset)."""
    have = find_key(presets, film, push, version)
    if have is not None:
        return normalise(presets), have
    return add(presets, make(film, push, version, notes))


def update(presets, pid, **fields):
    """Change a preset's film / push / version / notes / clip / chosen.
    Refused (None) when the new ID belongs to another preset."""
    lst = normalise(presets)
    p = find(lst, pid)
    if p is None:
        return lst, None
    new = dict(p, **fields)
    new = clean(new)
    if new is None or taken(lst, new["film"], new["push"], new["version"], but=pid):
        return lst, None
    lst[lst.index(p)] = new
    return lst, new


def delete(presets, pid):
    return [p for p in normalise(presets) if p["id"] != pid]


def new_version(presets, pid):
    """A copy of `pid` on the next free version of its film and push (notes
    cleared). (presets, the copy)."""
    lst = normalise(presets)
    p = find(lst, pid)
    if p is None:
        return lst, None
    v = next_version(lst, p["film"], p["push"])
    return add(lst, make(p["film"], p["push"], v, "", copy.deepcopy(p["clip"]), p["chosen"],
                         flats=dict(p.get("flats") or {}),
                         auto_radius=bool(p.get("auto_radius"))))


def write_clip(presets, pid, clip, chosen):
    """Write the settings (clipboard form) and the rows chosen."""
    clip = _clean_clip(clip)
    chosen = [c for c in chosen or () if c not in GEOMETRY]
    return update(presets, pid, clip=clip if chosen else None, chosen=chosen)


def set_look(presets, pid, look):
    """The preset's Film Look (a looks-library value, '' for none)."""
    lst = normalise(presets)
    p = find(lst, pid)
    if p is None:
        return lst, None
    clip = copy.deepcopy(p["clip"]) or _clean_clip({})
    chosen = [c for c in p["chosen"] if c != "look_lut"]
    if look:
        clip["look_lut"] = look
        chosen.append("look_lut")
    else:
        clip.pop("look_lut", None)
    if not chosen:
        clip = None
    return update(lst, pid, clip=clip, chosen=chosen)


def set_flat(presets, pid, scan, ref):
    """The preset's flat for a scan type: a library file name, "none", or
    "" / None for Automatic. (presets, the preset or None)."""
    lst = normalise(presets)
    p = find(lst, pid)
    if p is None or scan not in FLAT_SCANS:
        return lst, None
    flats = dict(p.get("flats") or {})
    if ref:
        flats[scan] = str(ref)
    else:
        flats.pop(scan, None)
    return update(lst, pid, flats=flats)


def set_auto_radius(presets, pid, on):
    """Whether the preset's frames get Auto Radius measured on each of them
    (spec/preset-auto-radius.md). (presets, the preset or None)."""
    return update(normalise(presets), pid, auto_radius=bool(on))


def auto_radius_of(p):
    return bool((p or {}).get("auto_radius"))


def flat_of(p, scan):
    """The preset's flat for a scan type: a file name, "none", or "" (Automatic)."""
    return ((p or {}).get("flats") or {}).get(scan, "")


def flats_map(presets):
    """{preset id: flats} of the presets that name any (for flat_field)."""
    return {p["id"]: dict(p["flats"]) for p in normalise(presets) if p.get("flats")}


def look_of(p):
    if not p or not p.get("clip") or "look_lut" not in (p.get("chosen") or ()):
        return ""
    return p["clip"].get("look_lut") or ""


def carried(p):
    """How many settings rows the preset carries (the look counted)."""
    return len((p or {}).get("chosen") or ()) if (p or {}).get("clip") else 0


def summary(p):
    """'Film Look Portra · 14 settings' / 'no settings yet'."""
    n = carried(p)
    look = look_of(p)
    bits = []
    if look:
        bits.append("Film Look " + look_name(look))
        n -= 1
    if n > 0:
        bits.append(f"{n} setting{'' if n == 1 else 's'}")
    k = len((p or {}).get("flats") or {})
    if k:
        bits.append(f"{k} flat field{'' if k == 1 else 's'}")
    if auto_radius_of(p):
        bits.append("Auto radius")
    return " · ".join(bits) if bits else "no settings yet"


def look_name(value):
    """'Noritsu Lomo 800.cube|colour|bal' -> 'Noritsu Lomo 800 (colour only)'."""
    from core.look_lut import split_look_name
    name, colour = split_look_name(value)
    name = name[:-5] if name.lower().endswith(".cube") else name
    return f"{name} (colour only)" if colour else name


def load(settings):
    """The presets from a QSettings-like object (value / setValue)."""
    try:
        raw = settings.value(STORE_KEY, "", type=str)
    except TypeError:
        raw = settings.value(STORE_KEY, "")
    return normalise(raw or "")


def save(settings, presets):
    settings.setValue(STORE_KEY, dumps(presets))
    return normalise(presets)
