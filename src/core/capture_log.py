"""The capture log and capture file names (spec/capture-log-and-names.md).

Every shot Capture takes is written to `freeccr-capture-log.jsonl` in the
capture folder: one JSON object a line, a shot's line written again as its
files arrive (a reader keeps the last line per roll and shot). Files FreeCCR
downloads are named `<roll>_<format>_<film>_<shot>_<COLOUR>`. Qt-free.
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
WORDS = {"red": "RED", "green": "GREEN", "blue": "BLUE", "white": "WHITE", "bw": "BW"}
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


def shot_stem(roll, fmt, film, shot, slot):
    """'R0034_645_Portra400_013_RED'; format and film left out when not set."""
    parts = [clean_roll(roll) or DEFAULT_ROLL]
    for p in (clean(fmt), clean(film)):
        if p:
            parts.append(p)
    parts.append(f"{int(shot):03d}")
    parts.append(WORDS.get(slot, clean(slot).upper() or "WHITE"))
    return "_".join(parts)


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
    m = re.search(r"_(\d{3,})_[A-Z]+(?:_\d+)?$", stem)
    return int(m.group(1)) if m else None


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
    return [last[k] for k in order]


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
    return COLOUR_ORDER if entry.get("scan_type") == "trichrome" \
        else (single_slot(entry.get("scan_type")),)


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
        free = [s for s in slots_of(e) if s not in files]
        if not free:
            return False
        files[free[0]] = name
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
