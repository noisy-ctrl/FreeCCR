"""How much room the capture folder's disk has left, in gigabytes and in shots
(spec/capture-disk-space.md). Qt-free."""
import os
import shutil
import statistics
from typing import Optional, Tuple

WARN_SHOTS = 30            # fewer shots left than this: the note turns amber
STOP_SHOTS = 5             # ... and red
WARN_BYTES = 5e9           # with no size per shot known: amber under 5 GB
RAW_EXT = (".raf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef",
           ".srw", ".raw", ".3fr", ".iiq", ".tif", ".tiff")


def free_bytes(folder) -> Optional[int]:
    """Free bytes on the disk holding `folder`, or None."""
    try:
        return int(shutil.disk_usage(folder).free)
    except (OSError, TypeError, ValueError):
        return None


def _logged_shot_bytes(folder) -> Optional[int]:
    """The size of the newest logged shot whose files are all there."""
    from core import capture_log
    for e in reversed(capture_log.read_log(folder)):
        files = [v for v in (capture_log.entry_files(e) or {}).values() if v]
        if not files or not e.get("done", True):
            continue
        total = 0
        try:
            for f in files:
                total += os.path.getsize(os.path.join(folder, f))
        except OSError:
            continue
        return total
    return None


def _typical_file_bytes(folder, newest=12) -> Optional[int]:
    """The median size of the folder's newest RAW files."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(RAW_EXT)]
    except OSError:
        return None
    paths = [os.path.join(folder, n) for n in names]
    try:
        paths.sort(key=os.path.getmtime)
        sizes = [os.path.getsize(p) for p in paths[-newest:]]
    except OSError:
        return None
    return int(statistics.median(sizes)) if sizes else None


def shot_bytes(folder, files_per_shot=1) -> Optional[int]:
    """Bytes one shot takes: the newest complete logged shot's files, else the
    folder's typical RAW size × `files_per_shot`; None when nothing is known."""
    got = _logged_shot_bytes(folder)
    if got:
        return got
    one = _typical_file_bytes(folder)
    return one * max(1, int(files_per_shot)) if one else None


def gb_text(n) -> str:
    gb = n / 1e9
    if gb >= 100:
        return f"{gb:.0f} GB"
    if gb >= 10:
        return f"{gb:.0f} GB"
    return f"{gb:.1f} GB"


def note(folder, files_per_shot=1) -> Tuple[str, str]:
    """(text, level) for Capture: 'Disk: 212 GB free, about 480 shots' and
    "ok" / "warn" / "stop"; ('', 'ok') when the disk can't be read."""
    free = free_bytes(folder)
    if free is None:
        return "", "ok"
    per = shot_bytes(folder, files_per_shot)
    text = f"Disk: {gb_text(free)} free"
    if per:
        shots = int(free // per)
        text += f", about {shots} shot{'' if shots == 1 else 's'}"
        level = "stop" if shots < STOP_SHOTS else "warn" if shots < WARN_SHOTS else "ok"
    else:
        level = "warn" if free < WARN_BYTES else "ok"
    return text, level
