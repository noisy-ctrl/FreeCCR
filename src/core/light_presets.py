"""Scanlight presets: named R, G, B, W, IR levels (spec/live-capture-monitor.md
§9). Qt-free; the monitor stores them as JSON in QSettings `live/presets`.

Shape: {"presets": [{"name", "r", "g", "b", "w", "ir"}], "selected": i}.
Import also accepts "selected_index", the key the other capture app wrote.
"""
import json

from core import capture_light as cl

DEFAULT = {"name": "Preset 1", "r": 255, "g": 255, "b": 255, "w": 255, "ir": 0}


def _clean_one(p):
    if not isinstance(p, dict):
        return None
    name = str(p.get("name", "")).strip()
    if not name:
        return None
    out = {"name": name}
    for k in cl.CHANNELS:
        try:
            out[k] = max(0, min(255, int(round(float(p.get(k, 0))))))
        except (TypeError, ValueError):
            out[k] = 0
    return out


def normalise(data):
    """A clean presets dict: at least one preset, unique names, a valid
    selection."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            data = None
    presets, seen = [], set()
    if isinstance(data, dict):
        for p in data.get("presets", []) or []:
            c = _clean_one(p)
            if c and c["name"].lower() not in seen:
                seen.add(c["name"].lower())
                presets.append(c)
    if not presets:
        presets = [dict(DEFAULT)]
    sel = 0
    if isinstance(data, dict):
        for key in ("selected", "selected_index"):
            if key in data:
                try:
                    sel = int(data[key])
                except (TypeError, ValueError):
                    sel = 0
                break
    return {"presets": presets, "selected": max(0, min(len(presets) - 1, sel))}


def dumps(data):
    return json.dumps(normalise(data))


def levels_of(preset):
    return {k: int(preset.get(k, 0)) for k in cl.CHANNELS}


def names(data):
    return [p["name"] for p in normalise(data)["presets"]]


def unique_name(data, name):
    """`name`, or `name 2`, `name 3`… if taken (case-insensitive)."""
    taken = {n.lower() for n in names(data)}
    base = (name or "Preset").strip() or "Preset"
    if base.lower() not in taken:
        return base
    i = 2
    while f"{base} {i}".lower() in taken:
        i += 1
    return f"{base} {i}"


def add(data, name, levels):
    d = normalise(data)
    p = {"name": unique_name(d, name), **levels_of(levels)}
    d["presets"].append(p)
    d["selected"] = len(d["presets"]) - 1
    return d


def overwrite(data, index, levels):
    d = normalise(data)
    if 0 <= index < len(d["presets"]):
        name = d["presets"][index]["name"]
        d["presets"][index] = {"name": name, **levels_of(levels)}
    return d


def rename(data, index, name):
    d = normalise(data)
    name = (name or "").strip()
    if not name or not (0 <= index < len(d["presets"])):
        return d
    others = {p["name"].lower() for i, p in enumerate(d["presets"]) if i != index}
    if name.lower() in others:
        return d
    d["presets"][index]["name"] = name
    return d


def delete(data, index):
    """The last preset can't be deleted."""
    d = normalise(data)
    if len(d["presets"]) <= 1 or not (0 <= index < len(d["presets"])):
        return d
    del d["presets"][index]
    d["selected"] = max(0, min(d["selected"] if d["selected"] < index else d["selected"] - 1,
                               len(d["presets"]) - 1))
    return d


def import_from(data, other):
    """Add the presets in `other` (a dict or JSON text) whose names are new.
    Returns (new data, number added)."""
    d = normalise(data)
    if isinstance(other, str):
        try:
            other = json.loads(other)
        except (TypeError, ValueError):
            return d, 0
    if not isinstance(other, dict):
        return d, 0
    taken = {p["name"].lower() for p in d["presets"]}
    added = 0
    for p in other.get("presets", []) or []:
        c = _clean_one(p)
        if c and c["name"].lower() not in taken:
            taken.add(c["name"].lower())
            d["presets"].append(c)
            added += 1
    return d, added
