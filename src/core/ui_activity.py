"""When the user last did something (spec/background-yields.md).

Background renders check this so they give way while you scroll the
thumbnail list or move between frames. Qt-free; safe to read from any
thread (a float assignment is atomic under the GIL).
"""

import time

QUIET_S = 0.4          # this long after the last touch, the UI counts as idle

_last = -1e9


def touch() -> None:
    """The user just scrolled, clicked or switched frames."""
    global _last
    _last = time.monotonic()


def idle_for() -> float:
    """Seconds since the last touch."""
    return time.monotonic() - _last


def busy(quiet: float = QUIET_S) -> bool:
    """True within `quiet` seconds of the last touch."""
    return idle_for() < quiet
