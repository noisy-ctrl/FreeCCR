"""Film Look: a 3-D LUT (.cube) applied as the decode out of density.

The look is fitted (tools/fit_look.py) from pairs of lab scans and our own
scans of the same negatives, and maps FreeCCR's density base — the display
value of the windowed black-point conversion, after Channel Levels — to
sRGB-encoded display values, the same contract as Cineon Log → Workspace.
See spec/film-look-lut.md.
"""
import os
import shutil

import numpy as np


class CubeError(ValueError):
    """A .cube file that is not a readable 3-D LUT."""


class LookLUT:
    """A parsed 3-D LUT. `table` is (n, n, n, 3) float32 indexed [b][g][r]
    (red varies fastest in the file, as the Adobe .cube spec requires)."""

    def __init__(self, table, domain_min=(0.0, 0.0, 0.0),
                 domain_max=(1.0, 1.0, 1.0), title="", path=None, headroom=False):
        table = np.ascontiguousarray(table, dtype=np.float32)
        if table.ndim != 4 or table.shape[3] != 3 or len(set(table.shape[:3])) != 1:
            raise CubeError(f"bad LUT table shape {table.shape}")
        self.table = table
        self.size = int(table.shape[0])
        self.domain_min = np.asarray(domain_min, dtype=np.float32).reshape(3)
        self.domain_max = np.asarray(domain_max, dtype=np.float32).reshape(3)
        if np.any(self.domain_max <= self.domain_min):
            raise CubeError("DOMAIN_MAX must exceed DOMAIN_MIN")
        self.title = title
        self.path = path
        self._flat = table.reshape(-1, 3)
        # headroom (spec/film-look-lut.md revision 4): past DOMAIN_MAX each
        # output channel carries on at its grey-axis end slope instead of
        # clamping; only for a cube that says so (HEADROOM_MARK)
        self.headroom = bool(headroom)
        n = self.size
        g = table[[n - 1, n - 2], [n - 1, n - 2], [n - 1, n - 2]]    # last two grey nodes
        step = (self.domain_max - self.domain_min) / (n - 1)
        self.top_slope = np.maximum((g[0] - g[1]) / np.maximum(step, 1e-9), 0.0).astype(
            np.float32)


# --------------------------------------------------------------------------- #
# .cube I/O (Adobe Cube LUT Specification 1.0; Resolve's LUT_3D_INPUT_RANGE)
# --------------------------------------------------------------------------- #

HEADROOM_MARK = "FREECCR_HEADROOM"   # a comment line: continue past DOMAIN_MAX


def read_cube(path) -> LookLUT:
    size = None
    dmin, dmax = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
    title = ""
    rows = []
    headroom = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("#") and line.lstrip("# ").strip() == HEADROOM_MARK:
                    headroom = True
                if not line or line.startswith("#"):
                    continue
                head = line.split(None, 1)[0].upper()
                if head == "TITLE":
                    title = line[5:].strip().strip('"')
                elif head == "LUT_3D_SIZE":
                    size = int(line.split()[1])
                elif head == "LUT_1D_SIZE":
                    raise CubeError("1-D LUTs are not supported — a film look "
                                    "needs a 3-D LUT")
                elif head == "DOMAIN_MIN":
                    dmin = [float(v) for v in line.split()[1:4]]
                elif head == "DOMAIN_MAX":
                    dmax = [float(v) for v in line.split()[1:4]]
                elif head == "LUT_3D_INPUT_RANGE":
                    lo, hi = (float(v) for v in line.split()[1:3])
                    dmin, dmax = [lo] * 3, [hi] * 3
                elif head[0].isdigit() or head[0] in "+-.":
                    vals = line.split()
                    if len(vals) != 3:
                        raise CubeError(f"bad data line: {line!r}")
                    rows.append([float(v) for v in vals])
                # any other keyword is ignored, as the spec allows
    except (OSError, UnicodeError) as e:
        raise CubeError(str(e)) from e
    except (IndexError, ValueError) as e:
        if isinstance(e, CubeError):
            raise
        raise CubeError(f"unreadable .cube: {e}") from e
    if not size or size < 2:
        raise CubeError("missing LUT_3D_SIZE")
    if len(rows) != size ** 3:
        raise CubeError(f"expected {size ** 3} entries for LUT_3D_SIZE {size}, "
                        f"found {len(rows)}")
    table = np.asarray(rows, dtype=np.float32).reshape(size, size, size, 3)
    return LookLUT(table, dmin, dmax, title=title, path=path, headroom=headroom)


def write_cube(path, table, domain_min=(0.0, 0.0, 0.0), domain_max=(1.0, 1.0, 1.0),
               title="", comments=(), headroom=False):
    table = np.asarray(table, dtype=np.float64)
    n = table.shape[0]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for c in comments:
            if str(c).strip() != HEADROOM_MARK:
                fh.write(f"# {c}\n")
        if headroom:
            fh.write(f"# {HEADROOM_MARK}\n")
        if title:
            fh.write(f'TITLE "{title}"\n')
        fh.write(f"LUT_3D_SIZE {n}\n")
        fh.write("DOMAIN_MIN {:.6f} {:.6f} {:.6f}\n".format(*domain_min))
        fh.write("DOMAIN_MAX {:.6f} {:.6f} {:.6f}\n".format(*domain_max))
        flat = table.reshape(-1, 3)              # [b][g][r] -> red fastest
        fh.write("\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat))
        fh.write("\n")


def identity_table(n=17, domain_min=(0.0,) * 3, domain_max=(1.0,) * 3):
    axes = [np.linspace(domain_min[c], domain_max[c], n, dtype=np.float32)
            for c in range(3)]
    b, g, r = np.meshgrid(axes[2], axes[1], axes[0], indexing="ij")
    return np.stack([r, g, b], -1)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #

_CHUNK = 1 << 20          # pixels per pass: bounds the gather arrays' memory


def apply_look(d: np.ndarray, lut: LookLUT) -> np.ndarray:
    """Map display values `d` (..., 3) through the LUT (trilinear). Inputs
    outside the domain clamp to its edge, as .cube consumers do, except past
    DOMAIN_MAX in a cube with headroom: each output channel carries on there
    at its grey-axis end slope, by its own input (spec/film-look-lut.md
    revision 4). Returns float32 of the same shape."""
    d = np.asarray(d, dtype=np.float32)
    shape = d.shape
    flat = d.reshape(-1, 3)
    out = np.empty_like(flat)
    n = lut.size
    scale = (n - 1) / (lut.domain_max - lut.domain_min)
    for s in range(0, flat.shape[0], _CHUNK):
        x = flat[s:s + _CHUNK]
        u = (np.nan_to_num(x) - lut.domain_min) * scale
        np.clip(u, 0.0, n - 1, out=u)
        i0 = np.minimum(u.astype(np.int32), n - 2)
        f = u - i0
        r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
        fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
        base = (b0 * n + g0) * n + r0
        t = lut._flat
        c000 = t[base]
        c100 = t[base + 1]
        c010 = t[base + n]
        c110 = t[base + n + 1]
        c001 = t[base + n * n]
        c101 = t[base + n * n + 1]
        c011 = t[base + n * n + n]
        c111 = t[base + n * n + n + 1]
        c00 = c000 + (c100 - c000) * fr
        c10 = c010 + (c110 - c010) * fr
        c01 = c001 + (c101 - c001) * fr
        c11 = c011 + (c111 - c011) * fr
        c0 = c00 + (c10 - c00) * fg
        c1 = c01 + (c11 - c01) * fg
        out[s:s + _CHUNK] = c0 + (c1 - c0) * fb
        if lut.headroom and np.any(lut.top_slope > 0):
            past = np.maximum(np.nan_to_num(x) - lut.domain_max, 0.0)
            out[s:s + _CHUNK] += past * lut.top_slope
    return out.reshape(shape)


# --------------------------------------------------------------------------- #
# The look's input, whatever the frame's conversion (spec revision 6)
# --------------------------------------------------------------------------- #

# The density the fitter measures on: LOOK_DENSITY_SLOPE * log10(film base /
# scan), i.e. the black-point-only conversion at the default slope.
LOOK_DENSITY_SLOPE = 0.8


def look_input_map(conversion_inputs):
    """How to turn a frame's converted value into the density a look was
    fitted on, per channel, or None when it already is that density.

    ("scale", k):  d_look = k * d  - a two-point density conversion (the auto
                   or a sampled white point divides by log10(base/white) per
                   channel; k puts it back), or film-stock slopes.
    ("linear", r): d_look = -0.8 * log10(1 - r * d) - the legacy linear
                   two-point stretch, r = 1 - white/base.

    None for the default black-point-only conversion (what the look was
    fitted on), and for bases the look isn't meant for: a reference-frame or
    Positive conversion, and the no-anchor invert (no film base to measure
    from; Channel Levels places it by hand)."""
    ci = conversion_inputs or {}
    if ci.get("mode") != "bw":
        return None
    black, white = tuple(ci.get("bw") or (None, None))
    if black is None:
        return None
    base = np.maximum(np.asarray(black, np.float64), 1.0)
    if white is None:
        slopes = ci.get("slopes")
        if slopes is None:
            return None
        k = LOOK_DENSITY_SLOPE / np.maximum(np.asarray(slopes, np.float64), 1e-6)
        return ("scale", k.astype(np.float32))
    dense = np.maximum(np.asarray(white, np.float64), 1.0)
    if ci.get("density", False):
        dmax = np.where(base > dense, np.log10(base / np.minimum(dense, base)), 0.0)
        return ("scale", (LOOK_DENSITY_SLOPE * dmax).astype(np.float32))
    return ("linear", (1.0 - dense / base).astype(np.float32))


class FrameLook:
    """A look plus the map that puts ONE frame's conversion into the look's
    own input density. It reads like the LookLUT it wraps (apply_look needs
    nothing else); the pipeline calls `map_input` on the de-windowed value
    BEFORE Channel Levels, so Levels works in the density the look - and the
    fitter's per-frame Channel Levels - were measured in."""

    def __init__(self, lut, kind, coef):
        self.lut = lut
        self.input_kind = kind
        self.input_coef = np.asarray(coef, np.float32).reshape(3)

    def __getattr__(self, name):
        # only reached for names the wrapper lacks; never for its own (a copy
        # or unpickle calls this before __init__ has set them)
        if name.startswith("__") or name in ("lut", "input_kind", "input_coef"):
            raise AttributeError(name)
        return getattr(self.lut, name)

    def map_input(self, d):
        d = np.asarray(d, np.float32)
        if self.input_kind == "scale":
            return d * self.input_coef
        # legacy linear two-point: d = 1 - (x - white)/(base - white), so
        # x/base = 1 - r*d and the look's density is -0.8*log10(x/base)
        t = np.maximum(1.0 - d * self.input_coef, np.float32(1e-5))
        return np.float32(-LOOK_DENSITY_SLOPE) * np.log10(t)


def for_frame(lut, conversion_inputs):
    """`lut` as it should see this frame: itself when the frame's conversion
    is already the look's input, else a FrameLook carrying the map."""
    if lut is None:
        return None
    m = look_input_map(conversion_inputs)
    if m is None:
        return lut
    return FrameLook(lut, *m)


# --------------------------------------------------------------------------- #
# Library (<app data>/looks, next to camera_profiles)
# --------------------------------------------------------------------------- #

def looks_dir() -> str:
    from core.catalog import default_catalog_path
    d = os.path.join(os.path.dirname(default_catalog_path()), "looks")
    os.makedirs(d, exist_ok=True)
    return d


def list_looks() -> list:
    """File names of every .cube in the library, sorted case-insensitively."""
    try:
        names = [f for f in os.listdir(looks_dir()) if f.lower().endswith(".cube")]
    except OSError:
        return []
    return sorted(names, key=str.lower)


def import_look(src_path: str) -> str:
    """Validate an external .cube and copy it into the library. Returns the
    library file name. Raises CubeError for an unreadable file."""
    read_cube(src_path)
    name = os.path.basename(src_path)
    dst = os.path.join(looks_dir(), name)
    if os.path.abspath(dst) != os.path.abspath(src_path):
        shutil.copyfile(src_path, dst)
    _cache.pop(dst, None)
    return name


_cache = {}               # path -> (mtime, LookLUT)
_warned = set()


def resolve_look(name):
    """The LookLUT for a library file name, or None (no look / missing file /
    unreadable — logged once per name, and the render proceeds without it)."""
    if not name:
        return None
    path = name if os.path.isabs(name) else os.path.join(looks_dir(), name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        if name not in _warned:
            _warned.add(name)
            print(f"[film look] '{name}' not found in {looks_dir()} - rendering without it")
        return None
    hit = _cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        lut = read_cube(path)
    except CubeError as e:
        if name not in _warned:
            _warned.add(name)
            print(f"[film look] could not read '{name}': {e}")
        return None
    _cache[path] = (mtime, lut)
    return lut
