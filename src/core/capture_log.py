"""The capture log and capture file names (spec/capture-log-and-names.md).

Every shot Capture takes is written to `freeccr-capture-log.jsonl` in the
capture folder: one JSON object a line, a shot's line written again as its
files arrive (a reader keeps the last line per roll and shot). Files FreeCCR
downloads are named `<roll>_<format>_<film>_<shot>_<slot>`, e.g.
`R0025_135_Colorplus_04_1R` (spec/capture-short-names.md; the older
`R0025_35mm_Colorplus_004_1RED` names are still read). Qt-free.
"""
import datetime
import json
import os
import re
import threading
import time

LOG_NAME = "freeccr-capture-log.jsonl"
GRACE_S = 60.0                     # files this long after a shot's end still join it
COLOUR_ORDER = ("red", "green", "blue")
# 1R / 2G / 3B: by name a shot's files then sort red, green, blue in any app
# (the user, 2026-11-09; plain RED/GREEN/BLUE sorted blue first). Short since
# 2026-11-23 (spec/capture-short-names.md: the names made the frame list
# scroll sideways); 1RED / 2GREEN / 3BLUE / WHITE are still read.
WORDS = {"red": "1R", "green": "2G", "blue": "3B", "white": "W", "bw": "BW",
         # the IR shot for dust (spec/ir-dust-and-flat-sets.md): after the
         # colour shot(s), sorting last; never merged, paired with its shot
         "ir": "4IR"}
# a format's tag in a file name where it isn't its own key (135 film)
FILE_TAG = {"35mm": "135"}
SHOT_DIGITS = 2                    # 04; 100 and up as they are
# a slot word in a name, new and old: 1R 2G 3B W BW 4IR, 1RED RED WHITE ...
_SLOT_WORD = r"(?:[123]?(?:RED|GREEN|BLUE)|[123][RGB]|WHITE|W|BW|4IR)"
_WORD_SLOTS = {"RED": "red", "GREEN": "green", "BLUE": "blue", "R": "red", "G": "green",
               "B": "blue", "WHITE": "white", "W": "white", "BW": "bw", "IR": "ir"}
DEFAULT_ROLL = "R0001"


# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #
def clean(text):
    """Letters and digits only: 'Portra 400' -> 'Portra400'."""
    return re.sub(r"[^0-9A-Za-z]+", "", str(text or ""))


def clean_roll(text):
    """A roll id: letters, digits and '-' only."""
    return re.sub(r"[^0-9A-Za-z-]+", "", str(text or "")).strip("-")


def format_tag(fmt):
    """A film format as it goes in a file name: '35mm' -> '135', '645' -> '645'."""
    key = clean(fmt)
    return FILE_TAG.get(key.lower(), key)


def shot_stem(roll, fmt, film, shot, slot):
    """'R0034_645_Portra400_13_1R' (2G, 3B, W, BW, 4IR); format and film left
    out when not set; the shot in two digits."""
    parts = [clean_roll(roll) or DEFAULT_ROLL]
    for p in (format_tag(fmt), clean(film)):
        if p:
            parts.append(p)
    parts.append(f"{int(shot):0{SHOT_DIGITS}d}")
    parts.append(WORDS.get(slot, clean(slot).upper() or "W"))
    return "_".join(parts)


_SLOT_ORDER = {"red": 0, "green": 1, "blue": 2}


def slot_order(name):
    """For a file FreeCCR named (`..._13_1R`, `..._13_2G_1`, or the older
    `..._013_1RED` / `..._013_RED`): (stem up to the shot number, 0/1/2 for
    red/green/blue, retake number), so a shot sorts red, green, blue — by
    name it is BLUE, GREEN, RED. None for any other name."""
    stem = os.path.splitext(os.path.basename(str(name)))[0]
    m = _NAME_SLOT_RE.match(stem)
    if not m:
        return None
    slot = _word_slot(m.group(3))
    if slot not in _SLOT_ORDER:
        return None
    return f"{m.group(1)}_{m.group(2)}".lower(), _SLOT_ORDER[slot], int(m.group(4) or 0)


def single_slot(scan_type):
    """The slot word of a one-exposure shot."""
    return "bw" if scan_type == "bw" else "white"


def next_roll(last):
    """'R0034' -> 'R0035'; a roll without a number at its end gets '-2';
    nothing -> 'R0001'."""
    last = clean_roll(last)
    if not last:
        return DEFAULT_ROLL
    m = re.match(r"^(.*?)(\d+)$", last)
    if not m:
        return last + "-2"
    head, num = m.groups()
    return f"{head}{int(num) + 1:0{len(num)}d}"


def _shot_from_name(name, roll):
    """The shot number in a file name of `roll`, or None."""
    stem = os.path.splitext(os.path.basename(name))[0]
    if not stem.startswith(roll + "_"):
        return None
    m = re.search(r"_(\d{2,})_" + _SLOT_WORD + r"(?:_\d+)?$", stem, re.I)
    return int(m.group(1)) if m else None


_NAME_SLOT_RE = re.compile(r"^(.*)_(\d{2,})_(" + _SLOT_WORD + r")(?:_(\d+))?$", re.I)


def _word_slot(word):
    """'1R' / '1RED' / 'RED' -> 'red', 'W' / 'WHITE' -> 'white', '4IR' -> 'ir'."""
    return _WORD_SLOTS.get(re.sub(r"^[1-4]", "", str(word).upper()))


def name_slot(name):
    """(prefix '<roll>_<format>_<film>' lower case, shot number, slot) for a
    file FreeCCR named (`..._13_1R`, `..._013_1RED`, `..._013_RED_1`,
    `..._13_4IR`, `..._013_WHITE`), else None (spec revision 2026-11-22)."""
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    m = _NAME_SLOT_RE.match(stem)
    if not m:
        return None
    slot = _word_slot(m.group(3))
    if slot is None:
        return None
    return m.group(1).lower(), int(m.group(2)), slot


def _entry_keys(entry):
    """{(prefix lower case, shot)} a log entry's files can carry, as
    `name_slot` reads names: today's and, for a log written before the short
    names, the old format tag (35mm, not 135)."""
    try:
        shot = int(entry.get("shot"))
    except (TypeError, ValueError):
        return set()
    keys = set()
    for fmt in (format_tag(entry.get("format")), clean(entry.get("format"))):
        parts = [clean_roll(entry.get("roll")) or DEFAULT_ROLL]
        for p in (fmt, clean(entry.get("film"))):
            if p:
                parts.append(p)
        keys.add(("_".join(parts).lower(), shot))
    return keys


def list_label(name):
    """A frame's name as the frame list shows it (spec/capture-short-names.md):
    a name FreeCCR gave in the short form ('R0025_35mm_Colorplus_004_1RED_RGB.raf'
    -> 'R0025_135_Colorplus_04'; a merge drops its slot word, a single frame
    keeps it, short: '..._04_BW'); any other name as it is."""
    base = os.path.basename(str(name or ""))
    stem = os.path.splitext(base)[0]
    m = re.match(r"^(.*?)_RGB(?:_(\d+))?$", stem)
    merged, dup = (True, m.group(2)) if m else (False, None)
    core = m.group(1) if m else stem
    n = _NAME_SLOT_RE.match(core)
    if not n:
        return base
    prefix, shot, word, retake = n.groups()
    parts = [FILE_TAG.get(p.lower(), p) for p in prefix.split("_")]
    out = "_".join(parts) + f"_{int(shot):0{SHOT_DIGITS}d}"
    if not merged:
        out += "_" + WORDS.get(_word_slot(word), word)
    for extra in (retake, dup):
        if extra:
            out += f"_{extra}"
    return out


def entry_files(entry):
    """An entry's files put right by their names: a file FreeCCR named for
    another shot is dropped, one named for this shot sits in the slot its
    name says; other names stay where they were."""
    files = dict(entry.get("files") or {})
    keys = _entry_keys(entry)
    if not keys:
        return files
    named, other = {}, {}
    for slot, name in files.items():
        if not name:
            continue
        ns = name_slot(name)
        if ns is None:
            other[slot] = name
        elif (ns[0], ns[1]) in keys and ns[2] not in named:
            named[ns[2]] = name
    for slot, name in other.items():
        if slot not in named:
            named[slot] = name
    return named


def next_shot(folder, roll):
    """1 + the highest shot of `roll` in the folder's log or file names."""
    roll = clean_roll(roll) or DEFAULT_ROLL
    best = 0
    for e in read_log(folder):
        if e.get("roll") == roll:
            try:
                best = max(best, int(e.get("shot") or 0))
            except (TypeError, ValueError):
                pass
    try:
        names = os.listdir(folder) if folder else []
    except OSError:
        names = []
    for n in names:
        s = _shot_from_name(n, roll)
        if s:
            best = max(best, s)
    return best + 1


# --------------------------------------------------------------------------- #
# the log file
# --------------------------------------------------------------------------- #
def log_path(folder):
    return os.path.join(folder, LOG_NAME)


def read_log(folder):
    """The folder's shots, the last line of each (roll, shot), in the order
    they were first written. Unreadable lines are skipped."""
    if not folder:
        return []
    try:
        with open(log_path(folder), encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    order, last = [], {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        key = (e.get("roll"), e.get("shot"))
        if key not in last:
            order.append(key)
        last[key] = e
    out = []
    for k in order:
        e = last[k]
        if e.get("files"):
            e = dict(e)
            e["files"] = entry_files(e)
        out.append(e)
    return out


def append(folder, entry):
    """Write one line. Never raises (a log that can't be written is said once
    on the terminal; the shot itself is unaffected)."""
    try:
        with open(log_path(folder), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError as e:
        print(f"capture log: couldn't write in {folder}: {e}")
        return False


def logged_sets(paths):
    """Trichrome shots the logs of `paths`' folders know, whose red, green and
    blue files are all among `paths`: ([(r, g, b) paths, in shooting order],
    set of those paths)."""
    by_folder = {}
    for p in paths:
        by_folder.setdefault(os.path.dirname(os.path.abspath(p)), {})[
            os.path.normcase(os.path.basename(p))] = p
    sets, used = [], set()
    for folder, names in by_folder.items():
        for e in read_log(folder):
            files = e.get("files") or {}
            if e.get("scan_type") != "trichrome" or not all(files.get(c) for c in COLOUR_ORDER):
                continue
            trip = [names.get(os.path.normcase(str(files[c]))) for c in COLOUR_ORDER]
            if all(trip) and not any(t in used for t in trip):
                sets.append(tuple(trip))
                used.update(trip)
    return sets, used


def logged_singles(paths):
    """{path: "bw" | "colour"} for those of `paths` Capture logged as a
    single-exposure shot (B&W or white-light colour), so an import loads them
    on their own (spec/import-singles.md)."""
    by_folder = {}
    for p in paths:
        by_folder.setdefault(os.path.dirname(os.path.abspath(p)), {})[
            os.path.normcase(os.path.basename(p))] = p
    out = {}
    for folder, names in by_folder.items():
        for e in read_log(folder):
            kind = e.get("scan_type")
            if kind not in ("bw", "colour"):
                continue
            for f in (e.get("files") or {}).values():
                p = names.get(os.path.normcase(str(f)))
                if p is not None:
                    out[p] = kind
    return out


# --------------------------------------------------------------------------- #
# the shot being recorded (Capture's thread and the hot folder's both add)
# --------------------------------------------------------------------------- #
_lock = threading.Lock()
_shot = {"folder": None, "entry": None, "until": None}


def now_text():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def begin_shot(folder, entry):
    """Start recording a shot in `folder` (entry: roll, shot, scan_type,
    format, film, exposures, route). Writes its first line."""
    e = dict(entry)
    e.setdefault("v", 1)
    e.setdefault("time", now_text())
    e["files"] = dict(e.get("files") or {})
    e["done"] = False
    with _lock:
        _shot["folder"], _shot["entry"], _shot["until"] = folder, e, None
    if folder:
        append(folder, e)


def slots_of(entry):
    base = COLOUR_ORDER if entry.get("scan_type") == "trichrome" \
        else (single_slot(entry.get("scan_type")),)
    return tuple(base) + (("ir",) if entry.get("ir") else ())


def add_file(path, now=None):
    """A file of the shot being recorded has arrived: give it the next slot
    not yet filled and write the shot again. False when no shot is active,
    the file is already in it, or every slot is filled."""
    now = time.monotonic() if now is None else float(now)
    name = os.path.basename(str(path or ""))
    if not name or name == LOG_NAME:
        return False
    with _lock:
        e, folder, until = _shot["entry"], _shot["folder"], _shot["until"]
        if e is None or (until is not None and now > until):
            return False
        files = e["files"]
        if name in files.values():
            return False
        ns = name_slot(name)
        if ns is not None:
            # FreeCCR named it: it belongs to the shot and slot its name says
            # (a late report of the previous shot's file must not take this
            # shot's next slot: spec revision 2026-11-22)
            if (ns[0], ns[1]) not in _entry_keys(e) or ns[2] not in slots_of(e) \
                    or ns[2] in files:
                return False
            files[ns[2]] = name
        else:
            free = [s for s in slots_of(e) if s not in files]
            if not free:
                return False
            files[free[0]] = name
        snap = json.loads(json.dumps(e))
    if folder:
        append(folder, snap)
    return True


def note(path, key, value, now=None):
    """Something learnt about one of the recorded shot's files (e.g. that it
    clipped, spec/capture-clip-warning.md): kept under `key` by its slot and
    the shot written again. False when the file isn't in the shot."""
    now = time.monotonic() if now is None else float(now)
    name = os.path.basename(str(path or ""))
    with _lock:
        e, folder, until = _shot["entry"], _shot["folder"], _shot["until"]
        if e is None or (until is not None and now > until):
            return False
        slot = next((s for s, f in e["files"].items() if f == name), None)
        if slot is None:
            return False
        e.setdefault(key, {})[slot] = value
        snap = json.loads(json.dumps(e))
    if folder:
        append(folder, snap)
    return True


def end_shot(reason=None, now=None):
    """The sequence ended: mark the shot done (with why, if it stopped early).
    Files arriving in the next GRACE_S still join it."""
    now = time.monotonic() if now is None else float(now)
    with _lock:
        e, folder = _shot["entry"], _shot["folder"]
        if e is None or _shot["until"] is not None:
            return False
        _shot["until"] = now + GRACE_S
        e["done"] = True
        if reason and reason not in ("done", "finished"):
            e["stopped"] = str(reason)
        snap = json.loads(json.dumps(e))
    if folder:
        append(folder, snap)
    return True


def active_entry():
    with _lock:
        e = _shot["entry"]
        return json.loads(json.dumps(e)) if e is not None else None


def clear():
    with _lock:
        _shot["folder"], _shot["entry"], _shot["until"] = None, None, None


# --------------------------------------------------------------------------- #
# IR shots and the frames they belong to (spec/ir-dust-and-flat-sets.md)
# --------------------------------------------------------------------------- #
_SHOT_KEY_RE = re.compile(r"^(.*_\d{2,})_" + _SLOT_WORD + r"(?:_\d+)?$", re.I)
_IR_RE = re.compile(r"_\d{2,}_4IR(?:_\d+)?$", re.I)


def shot_key(path):
    """(folder, '<roll>_<format>_<film>_<shot>' lower case) for a file FreeCCR
    named, else None."""
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    m = _SHOT_KEY_RE.match(stem)
    if not m:
        return None
    return os.path.normcase(os.path.dirname(os.path.abspath(str(path)))), m.group(1).lower()


def _logged_ir(folder):
    """{basename of an IR file: [its shot's other basenames]} from a log."""
    out = {}
    for e in read_log(folder):
        files = e.get("files") or {}
        ir = files.get("ir")
        if ir:
            out[os.path.normcase(str(ir))] = [os.path.normcase(str(v))
                                              for k, v in files.items() if k != "ir" and v]
    return out


def is_ir_file(path, logs=None):
    """True for a shot's IR file: named `..._NNN_4IR`, or logged as one."""
    name = os.path.basename(str(path))
    if _IR_RE.search(os.path.splitext(name)[0]):
        return True
    folder = os.path.dirname(os.path.abspath(str(path)))
    logs = {} if logs is None else logs
    if folder not in logs:
        logs[folder] = _logged_ir(folder)
    return os.path.normcase(name) in logs[folder]


def split_ir(paths):
    """(the files that aren't IR shots, the IR shots)."""
    logs = {}
    keep, ir = [], []
    for p in paths:
        (ir if is_ir_file(p, logs) else keep).append(p)
    return keep, ir


def ir_for(frame_paths, candidates=(), look_in_folder=True, logs=None):
    """The IR file of the shot `frame_paths` (a frame's own files) came from:
    the log's `ir` for that shot, else a `..._4IR` file with the same shot
    name among `candidates` or (look_in_folder) beside the frame. None."""
    frame_paths = [p for p in frame_paths if p]
    if not frame_paths:
        return None
    logs = {} if logs is None else logs
    cands = list(candidates)
    folder = os.path.dirname(os.path.abspath(str(frame_paths[0])))
    if folder not in logs:
        logs[folder] = _logged_ir(folder)
    mine = {os.path.normcase(os.path.basename(p)) for p in frame_paths}
    for ir_name, others in logs[folder].items():
        if mine & set(others):
            for c in cands:
                if os.path.normcase(os.path.basename(c)) == ir_name:
                    return c
            p = os.path.join(folder, ir_name)
            if look_in_folder and os.path.exists(p):
                return p
            # the log can be in another case than the disk: try the folder
    keys = {shot_key(p) for p in frame_paths} - {None}
    if not keys:
        return None
    for c in cands:
        if _IR_RE.search(os.path.splitext(os.path.basename(c))[0]) and shot_key(c) in keys:
            return c
    if look_in_folder:
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        for n in sorted(names):
            p = os.path.join(folder, n)
            if _IR_RE.search(os.path.splitext(n)[0]) and shot_key(p) in keys:
                return p
    return None
