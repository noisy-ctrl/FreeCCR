#!/usr/bin/env python3
"""Rebuild a Film Look's shoulder (core/look_shoulder.py), for a .cube that
already exists.

    python3.11 tools/look_shoulder.py "Noritsu Lomo 800.cube" [--detail 50]
        [--out "Noritsu Lomo 800 (fixed).cube"] [--title "…"]

--detail 0 (default) only removes a flat top and a jump to white, clipping
nothing new; 50 and 100 keep more highlight contrast and reach white sooner.
Prints what changed, in levels of 255 along the grey axis. Import the result
in FreeCCR (Film Look → Import…); a file with the same name replaces the
library's copy, so every frame using it follows.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from core import look_lut  # noqa: E402
from core import look_shoulder  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cube")
    ap.add_argument("--detail", type=float, default=0.0, help="0..100 (default 0)")
    ap.add_argument("--out", help="output .cube (default: <name> (shoulder N).cube)")
    ap.add_argument("--title", help="the TITLE written in the file")
    a = ap.parse_args(argv)
    lut = look_lut.read_cube(a.cube)
    lo, hi = float(lut.domain_min[0]), float(lut.domain_max[0])
    detail = max(0.0, min(100.0, a.detail))
    table, info = look_shoulder.reshape(lut.table, lo, hi, detail / 100.0)
    if info is None:
        print("This look's grey axis has no flattening shoulder: nothing to change.")
        return 0
    base = os.path.splitext(os.path.basename(a.cube))[0]
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.cube)),
                                f"{base} (shoulder {detail:g}).cube")
    title = a.title or (lut.title or base)
    kept = []                                   # the source's own notes, first
    with open(a.cube, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            kept.append(line[1:].strip())
    look_lut.write_cube(out, table, lut.domain_min, lut.domain_max, title=title,
                        comments=kept + [f"Shoulder rebuilt by tools/look_shoulder.py, "
                                         f"highlight detail {detail:g}"])
    rep = look_shoulder.report(lut.table, table, lo, hi)
    print(f"wrote {out}")
    print(f"  knee at density {info['knee']:.3f} (grey luma {info['knee_y'] * 255:.0f}); "
          f"white at {rep['white_at'][1]:.3f} (was {rep['white_at'][0]:.3f})")
    for k in ("levels_0.65_0.80", "levels_0.80_0.97", "flattest_0.05_band_0.65_0.97"):
        b, n = rep[k]
        print(f"  {k}: {b:.1f} -> {n:.1f} levels")
    print(f"  monotone: {rep['monotone_after']}, never darker: {rep['never_darker']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
