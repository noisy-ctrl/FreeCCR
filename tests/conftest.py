"""
pytest session setup.

Mirror src/main.py's startup ordering: import onnxruntime BEFORE any PySide6/Qt
import. onnxruntime's native DLLs fail to initialise if first loaded AFTER Qt on
Windows ("DLL initialization routine failed" — a hard access violation under
pytest's accumulated native state). Several GUI tests build a MainWindow whose
dust panel probes dust_detect.is_available() -> `import onnxruntime`, which would
then load after Qt and crash the whole run.

pytest imports this conftest before any test module (and thus before any test's
module-level QApplication), so pre-loading onnxruntime here makes that later
import a cached no-op — the suite neither crashes nor emits faulthandler
access-violation noise. Guarded so it is a no-op when onnxruntime is absent.
See spec/dust-removal.md §8.
"""
try:
    import onnxruntime  # noqa: F401  (must precede any PySide6/Qt import)
except Exception:
    pass


# Auto crop learning (spec/auto-crop-learning.md) keeps a JSON memory under the
# user's app folder. Point it at a throwaway file for the whole run so tests
# never read or write the real one.
import os as _os
import tempfile as _tempfile

_os.environ.setdefault(
    "FREECCR_CROP_MEMORY",
    _os.path.join(_tempfile.mkdtemp(prefix="freeccr-crop-memory-"), "auto_crop_memory.json"))


# Background renders give way while the UI is busy (spec/background-yields.md).
# One test switching frames must not leave the next one's detail render
# deferred, so every test starts with the UI idle.
import sys as _sys

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _ui_idle():
    mod = _sys.modules.get("core.ui_activity")
    if mod is not None:
        mod._last = -1e9
    yield


@_pytest.fixture(autouse=True)
def _no_camera_settle(monkeypatch):
    """A fake camera needs no time after connecting: the GFX's settle
    (camera_backends.RECONNECT_SETTLE_S) would be a real 1 s sleep in every
    test that fires. Tests of the settle itself set it back."""
    try:
        from core import camera_backends
    except Exception:
        yield
        return
    monkeypatch.setattr(camera_backends, "RECONNECT_SETTLE_S", 0.0)
    yield
