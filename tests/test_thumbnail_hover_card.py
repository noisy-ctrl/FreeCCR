"""The frame list's hover card: shutter speeds and RAW clipping inside the
picture (spec/thumbnail-hover-card.md)."""

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import frame_card as fc  # noqa: E402


class Img:
    def __init__(self, files, ir=None, **kw):
        self.is_merged = len(files) == 3
        self.merge_sources = list(files) if self.is_merged else None
        self.file_path = files[0]
        self.ir_source = ir
        self.source_ops = []
        self.crop_rect = None
        self.crop_angle = 0.0
        self.capture_guide = None
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def raws(tmp_path):
    out = []
    for n in ("R1_135_X_04_1R.raf", "R1_135_X_04_2G.raf", "R1_135_X_04_3B.raf",
              "R1_135_X_04_4IR.raf", "scan.tif"):
        p = tmp_path / n
        p.write_bytes(n.encode())
        out.append(str(p))
    return out


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    fc._EXPOSURE_CACHE.clear()
    fc._CLIP_CACHE.clear()
    times = {"1R": (1 / 6, 100), "2G": (0.2, 100), "3B": (0.2, 100), "4IR": (15.0, 1000)}

    def fake_exif(path):
        for k, v in times.items():
            if f"_{k}." in path:
                return v
        return (None, None)
    import core.it8_profile as it8
    monkeypatch.setattr(it8, "read_shot_exposure", fake_exif)


def test_the_files_of_a_frame(raws):
    m = Img(raws[:3], ir=raws[3])
    assert [s for s, _ in fc.frame_files(m)] == ["red", "green", "blue", "ir"]
    assert fc.frame_files(Img([raws[4]])) == [("white", raws[4])]


def test_where_clipping_is_read():
    a, what = fc.picture_area(Img(["x.raf"], crop_rect=[0.1, 0.2, 0.9, 0.8], crop_angle=0.5))
    assert what == "crop" and a == pytest.approx(
        {"cx": 0.5, "cy": 0.5, "w": 0.8, "h": 0.6, "angle": 0.5})
    g = {"cx": 0.4, "cy": 0.5, "w": 0.7, "h": 0.6, "angle": 0.1, "format": "35mm"}
    a, what = fc.picture_area(Img(["x.raf"], capture_guide=g))
    assert what == "frame guide" and a["w"] == 0.7 and "format" not in a
    assert fc.picture_area(Img(["x.raf"], crop_rect=[0.1, 0.1, 0.9, 0.9],
                               source_ops=[(0, (0, 0, 0.5, 1))])) == (None, "middle")
    assert fc.picture_area(Img(["x.raf"])) == (None, "middle")


def test_shutter_text():
    assert fc.shutter_text(1 / 6) == "1/6 s"
    assert fc.shutter_text(1 / 250) == "1/250 s"
    assert fc.shutter_text(0.4) == "1/2.5 s"
    assert fc.shutter_text(1.0) == "1 s" and fc.shutter_text(15.0) == "15 s"
    assert fc.shutter_text(None) == "?"


def test_the_card_reads_the_picture_only_and_marks_a_clip(raws):
    m = Img(raws[:3], ir=raws[3], crop_rect=[0.1, 0.1, 0.9, 0.9])
    html = fc.card_html(m, "R1_135_X_04_1R_RGB.raf")
    assert html.count("checking…") == 3 and "…" in html            # nothing read yet
    seen = []

    def reader(path, area):
        seen.append((os.path.basename(path), area))
        return {"R": 0.0042} if path.endswith("_1R.raf") else {}
    for p, _k, area, clip in fc.to_read(m):
        fc.exposure(p)
        if clip:
            fc.clipping(p, area, reader)
    assert [s for s, _ in seen] == ["R1_135_X_04_1R.raf", "R1_135_X_04_2G.raf",
                                    "R1_135_X_04_3B.raf"]          # never the IR shot
    assert all(a["w"] == pytest.approx(0.8) for _, a in seen)      # inside the crop
    html = fc.card_html(m, "R1_135_X_04_1R_RGB.raf", warn="#d9534f")
    assert "1/6 s · ISO 100" in html and "15 s · ISO 1000" in html
    assert "color:#d9534f'>clips: red 0.42 %" in html
    assert html.count("not clipped") == 2 and "checking" not in html
    assert "inside the crop" in html and "sprocket holes" in html
    assert fc.to_read(m) == []                                     # all cached
    assert "not a RAW" in fc.card_html(Img([raws[4]]), "scan.tif")


def test_the_reader_reads_each_file_once_and_calls_back(raws):
    calls, done = [], []

    def reader(path, area):
        calls.append(path)
        return {}
    r = fc.ClipReader(done=done.append, reader=reader)
    m = Img(raws[:3], ir=raws[3])
    r.ask(fc.to_read(m))
    r.ask(fc.to_read(m))                       # asked again before it finished
    t = time.time()
    while len(done) < 4 and time.time() - t < 5:
        time.sleep(0.01)
    assert sorted(calls) == sorted(raws[:3]) and len(done) == 4
    assert fc.to_read(m) == []


def test_the_list_shows_the_card_and_redraws_it(raws, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QListWidgetItem, QToolTip
    app = QApplication.instance() or QApplication([])
    from widgets.thumbnail_list import ThumbnailList
    from core import ccr_backend as cbm
    m = Img(raws[:3], ir=raws[3], display_name="R1_135_X_04_1R_RGB.raf")
    monkeypatch.setattr(cbm.ccr_backend, "get_image_by_index", lambda i: m if i == 0 else None)
    monkeypatch.setattr(fc.ClipReader, "ask", lambda self, items: asked.extend(items))
    asked = []
    tl = ThumbnailList(lambda i: None)
    item = QListWidgetItem("R1_135_X_04")
    item.setData(Qt.UserRole, 0)
    tl.thumbnail_list.addItem(item)
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *a, **k: shown.append(a[1]))
    monkeypatch.setattr(QToolTip, "isVisible", lambda: True)
    pos = tl.thumbnail_list.visualItemRect(item).center()
    tl._show_card(pos, tl.thumbnail_list.viewport().mapToGlobal(pos))
    assert shown and "R1_135_X_04_1R_RGB.raf" in shown[-1] and "checking…" in shown[-1]
    assert len(asked) == 4 and tl._card_idx == 0
    for p, _k, area, clip in asked:
        fc.exposure(p)
        if clip:
            fc.clipping(p, area, lambda *_: {})
    tl._on_card_read(raws[0])
    assert "checking" not in shown[-1] and shown[-1].count("not clipped") == 3
