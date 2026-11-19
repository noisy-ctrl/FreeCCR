"""Show files in the system's file manager (Finder / Explorer / a Linux file
manager), selected. Used by the thumbnail list's right-click menu.

See spec/show-in-finder.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Iterable, List, Optional


def menu_label(platform: Optional[str] = None) -> str:
    platform = platform or sys.platform
    if platform == "darwin":
        return "Show in Finder"
    if platform.startswith("win"):
        return "Show in Explorer"
    return "Show in File Manager"


def frame_files(img) -> List[str]:
    """The files a frame was made from: a merge's sources (red, green, blue
    in their order), else its own file. Only files that still exist."""
    paths = list(getattr(img, "merge_sources", None) or [])
    if not paths:
        p = getattr(img, "file_path", None)
        paths = [p] if p else []
    seen, out = set(), []
    for p in paths:
        p = os.path.normpath(str(p))
        if p not in seen and os.path.exists(p):
            seen.add(p)
            out.append(p)
    return out


def commands(paths: Iterable[str], platform: Optional[str] = None) -> List[List[str]]:
    """The command lines that show `paths` selected. macOS's `open -R` takes
    several files at once; Explorer selects one file per window, so one
    command per folder (its first file); on Linux the folders are opened."""
    platform = platform or sys.platform
    paths = [os.path.normpath(p) for p in paths]
    if not paths:
        return []
    if platform == "darwin":
        return [["open", "-R", *paths]]
    by_folder = {}
    for p in paths:
        by_folder.setdefault(os.path.dirname(p), p)
    if platform.startswith("win"):
        return [["explorer", f"/select,{first}"] for first in by_folder.values()]
    return [["xdg-open", folder or "."] for folder in by_folder]


def reveal(paths: Iterable[str], platform: Optional[str] = None,
           run: Callable = None) -> bool:
    """Show `paths` in the file manager. Returns False when there was nothing
    to show or the command couldn't start."""
    run = run or (lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL))
    cmds = commands(paths, platform)
    ok = bool(cmds)
    for cmd in cmds:
        try:
            run(cmd)
        except Exception:
            ok = False
    return ok
