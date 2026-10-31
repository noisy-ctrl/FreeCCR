"""The Details (sharpening) values an image starts with (spec/details-defaults.md).

The user's choice, 2026-10-31: Amount 30, Radius 2, Masking 0, changeable in
Settings → General → Details. An image whose adjustments carry no sharpen_*
keys renders with these, and the panel shows them; Settings changes them for
every image that hasn't set its own. Qt-free: MainWindow loads the saved
values at start-up (`adjust/detail_defaults`) and `set_current` applies a
change.
"""

KEYS = ("sharpen_amount", "sharpen_radius", "sharpen_masking")
FACTORY = {"sharpen_amount": 30, "sharpen_radius": 2, "sharpen_masking": 0}
LABELS = {"sharpen_amount": "Amount", "sharpen_radius": "Radius",
          "sharpen_masking": "Masking"}

_current = dict(FACTORY)


def clean(values):
    """{key: int 0-100} for every key; anything missing or unreadable is the
    factory value."""
    out = dict(FACTORY)
    if isinstance(values, dict):
        for k in KEYS:
            try:
                out[k] = max(0, min(100, int(round(float(values[k])))))
            except (KeyError, TypeError, ValueError):
                pass
    return out


def get(key):
    return _current[key]


def current():
    return dict(_current)


def set_current(values):
    _current.update(clean(values))
    return current()


def encode(values):
    v = clean(values)
    return ",".join(str(v[k]) for k in KEYS)


def decode(text):
    """The saved "30,2,0", or None when there is nothing usable."""
    try:
        parts = [p.strip() for p in str(text or "").split(",")]
        if len(parts) != len(KEYS) or not all(parts):
            return None
        return clean(dict(zip(KEYS, (float(p) for p in parts))))
    except (TypeError, ValueError):
        return None


def written_down(adjustments):
    """A saved adjustment dict with the sharpening it renders with spelled
    out: an edited dict that never set its own gets the current defaults, so
    reopening it (and a later change of defaults) leaves it as it was. An
    empty dict stays empty: an untouched image keeps following the defaults."""
    adj = dict(adjustments or {})
    if adj and "sharpen_amount" not in adj:
        for k in KEYS:
            adj.setdefault(k, _current[k])
    return adj
