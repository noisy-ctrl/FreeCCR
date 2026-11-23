"""Disk space in Capture (spec/capture-disk-space.md)."""

import json
import os
import sys
from collections import namedtuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import capture_log as cl  # noqa: E402
from core import disk_space as ds  # noqa: E402

Usage = namedtuple("Usage", "total used free")


def _free(monkeypatch, n):
    monkeypatch.setattr(ds.shutil, "disk_usage", lambda p: Usage(10 ** 13, 0, n))


def test_gb_text():
    assert ds.gb_text(212.4e9) == "212 GB" and ds.gb_text(12.4e9) == "12 GB"
    assert ds.gb_text(4.26e9) == "4.3 GB"


def test_shots_from_the_last_logged_shot(tmp_path, monkeypatch):
    names = [cl.shot_stem("R1", "35mm", "X", 4, s) + ".raf" for s in ("red", "green", "blue", "ir")]
    for n in names:
        (tmp_path / n).write_bytes(b"x" * 1000)
    e = {"roll": "R1", "format": "35mm", "film": "X", "shot": 4, "scan_type": "trichrome",
         "ir": True, "done": True,
         "files": dict(zip(("red", "green", "blue", "ir"), names))}
    (tmp_path / cl.LOG_NAME).write_text(json.dumps(e) + "\n")
    assert ds.shot_bytes(str(tmp_path)) == 4000
    _free(monkeypatch, 400_000)
    assert ds.note(str(tmp_path)) == ("Disk: 0.0 GB free, about 100 shots", "ok")


def test_shots_from_the_folders_raws(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"DSCF00{i}.RAF").write_bytes(b"x" * 1000)
    (tmp_path / "notes.txt").write_bytes(b"x" * 10 ** 6)
    assert ds.shot_bytes(str(tmp_path), 3) == 3000
    _free(monkeypatch, 3000 * 20)
    assert ds.note(str(tmp_path), 3) == ("Disk: 0.0 GB free, about 20 shots", "warn")
    _free(monkeypatch, 3000 * 4)
    assert ds.note(str(tmp_path), 3)[1] == "stop"
    _free(monkeypatch, 3000)
    assert ds.note(str(tmp_path), 3)[0].endswith("about 1 shot")


def test_nothing_known_but_the_space(tmp_path, monkeypatch):
    _free(monkeypatch, 212e9)
    assert ds.note(str(tmp_path)) == ("Disk: 212 GB free", "ok")
    _free(monkeypatch, 4e9)
    assert ds.note(str(tmp_path))[1] == "warn"
    monkeypatch.setattr(ds.shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError()))
    assert ds.note(str(tmp_path)) == ("", "ok")


def test_capture_shows_it_with_a_hot_folder_only(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets import live_monitor as lm
    calls = []

    def fake_note(folder, per_shot=1):
        calls.append(per_shot)
        return ("Disk: 2.0 GB free, about 3 shots", "stop")
    monkeypatch.setattr(ds, "note", fake_note)
    mon = lm.LiveCaptureMonitor.__new__(lm.LiveCaptureMonitor)
    from PySide6.QtWidgets import QLabel
    mon.disk_note = QLabel()
    mon._disk_checked = None
    mon.scan_type = lambda: "trichrome"
    mon._ir_shot = lambda: {"shutter": "8s"}
    mon._refresh_disk_note(None)
    assert mon.disk_note.isHidden() and not calls
    mon._refresh_disk_note(str(tmp_path))
    assert calls == [4] and mon.disk_note.text().startswith("Disk: 2.0 GB")
    assert "font-weight: bold" in mon.disk_note.styleSheet()
    mon._refresh_disk_note(str(tmp_path))               # within 5 s: not again
    assert calls == [4]
    mon._refresh_disk_note(str(tmp_path), force=True)   # after a shot
    assert calls == [4, 4]
