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
