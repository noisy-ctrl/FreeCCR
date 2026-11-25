"""Compare with a reference picture (spec/reference-compare.md)."""
import os
import struct
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import color_management as cm          # noqa: E402
from core import reference_compare as rc          # noqa: E402


def scene(seed=1, w=960, h=640):
    """A busy synthetic picture SIFT can hold on to: shapes at many sizes,
    text, a gradient, a little noise."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = np.linspace(40, 200, w, dtype=np.float32)[None, :, None].astype(np.uint8)
    for _ in range(140):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        kind = rng.integers(0, 3)
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        s = int(rng.integers(6, 70))
        if kind == 0:
            cv2.rectangle(img, (x, y), (x + s, y + int(s * rng.uniform(0.4, 1.6))), c, -1)
        elif kind == 1:
            cv2.circle(img, (x, y), s // 2, c, -1)
        else:
            cv2.line(img, (x, y), (x + int(rng.integers(-90, 90)), y + int(rng.integers(-90, 90))),
                     c, int(rng.integers(1, 5)))
    for i in range(10):
        cv2.putText(img, f"FRAME {seed}{i}", (int(rng.integers(0, w - 200)), int(rng.integers(30, h))),
                    cv2.FONT_HERSHEY_SIMPLEX, float(rng.uniform(0.6, 1.6)),
                    tuple(int(v) for v in rng.integers(0, 255, 3)), 2)
    img = cv2.GaussianBlur(img, (3, 3), 0.8)
    noise = rng.normal(0, 3, img.shape)
    return np.clip(img + noise, 0, 255).astype(np.uint8)


def _make_reference(shown, angle=7.0, scale=1.6, crop=(60, 40, 820, 560), mirror=True,
                    regrade=True):
    """The shown picture as a lab might give it back: cropped tighter, turned,
    bigger, mirrored and graded darker. Returns (reference, F) with F the
    shown -> reference map in continuous pixel coordinates (before the
    mirror), and the reference's width."""
    x0, y0, x1, y1 = crop
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    out_w, out_h = int((x1 - x0) * scale), int((y1 - y0) * scale)
    th = np.radians(angle)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]]) * scale
    # continuous: r = R (s - c) + out/2
    F = np.eye(3)
    F[:2, :2] = R
    F[:2, 2] = -R @ np.array([cx, cy]) + np.array([out_w / 2.0, out_h / 2.0])
    # OpenCV's warp works on pixel indices: index = continuous - 0.5
    Fi = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]]) @ F @ np.array(
        [[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]])
    ref = cv2.warpAffine(shown, Fi[:2], (out_w, out_h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REFLECT)
    if regrade:
        ref = (255.0 * (ref / 255.0) ** 1.4 * 0.85).astype(np.uint8)
    if mirror:
        ref = np.ascontiguousarray(ref[:, ::-1])
    return ref, F, out_w, out_h


# --------------------------------------------------------------------------- #
# Lining up
# --------------------------------------------------------------------------- #

def test_lines_up_a_cropped_turned_scaled_mirrored_regraded_reference():
    shown = scene(1)
    ref, F, rw, rh = _make_reference(shown)
    lu = rc.line_up(ref, shown)
    assert lu is not None and lu.mirrored and lu.inliers >= 25
    h, w = shown.shape[:2]
    # points inside the cropped area of the shown picture
    pts = np.array([[150, 120], [700, 130], [420, 330], [160, 520], [760, 500]], np.float64)
    worst = 0.0
    for p in pts:
        r = F @ np.array([p[0], p[1], 1.0])
        r = r[:2] / r[2]
        r[0] = rw - r[0]                                     # the mirror
        q = lu.H @ np.array([r[0] / rw, r[1] / rh, 1.0])
        q = q[:2] / q[2]
        worst = max(worst, abs(q[0] - p[0] / w), abs(q[1] - p[1] / h))
    assert worst < 0.015, worst


def test_lines_up_without_a_mirror_and_reports_it():
    shown = scene(2)
    ref, F, rw, rh = _make_reference(shown, angle=-3.0, scale=0.8, mirror=False)
    lu = rc.line_up(ref, shown)
    assert lu is not None and not lu.mirrored


def test_refuses_an_unrelated_picture():
    assert rc.line_up(scene(3), scene(4)) is None


def test_an_unconverted_negative_is_matched_inverted():
    shown = scene(5)
    ref, _F, _w, _h = _make_reference(shown, mirror=False, regrade=False)
    negative = 255 - shown
    assert rc.line_up(ref, negative, invert_shown=True) is not None


def test_pixel_matrix_and_qt_args_map_as_the_homography_does():
    H = np.array([[0.9, 0.05, 0.03], [-0.04, 1.1, 0.02], [0.01, -0.02, 1.0]])
    M = rc.pixel_matrix(H, (400, 300), (1200, 800))
    p = np.array([123.0, 77.0, 1.0])
    want = H @ np.array([p[0] / 400, p[1] / 300, 1.0])
    want = want[:2] / want[2] * np.array([1200, 800])
    got = M @ p
    assert np.allclose(got[:2] / got[2], want, atol=1e-9)
    from PySide6.QtGui import QTransform
    from PySide6.QtCore import QPointF
    t = QTransform(*rc.qt_args(M))
    q = t.map(QPointF(123.0, 77.0))
    assert abs(q.x() - want[0]) < 1e-6 and abs(q.y() - want[1]) < 1e-6


def test_fitted_rect_keeps_the_aspect_and_centres():
    x, y, w, h = rc.fitted_rect((300, 200), (10, 20, 600, 600))
    assert (w, h) == (600, 400) and (x, y) == (10, 120)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def _jpeg_with_icc(rgb, icc=None, orientation=None):
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, 100])
    data = buf.tobytes()
    segs = b""
    if orientation:
        tiff = (b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">H", 1)
                + struct.pack(">HHII", 274, 3, 1, orientation << 16) + b"\x00\x00\x00\x00")
        body = b"Exif\x00\x00" + tiff
        segs += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    if icc:
        chunks = [icc[i:i + 60000] for i in range(0, len(icc), 60000)]
        for i, c in enumerate(chunks):
            body = b"ICC_PROFILE\x00" + bytes([i + 1, len(chunks)]) + c
            segs += b"\xff\xe2" + struct.pack(">H", len(body) + 2) + body
    return data[:2] + segs + data[2:]


def test_a_jpeg_with_no_profile_comes_back_as_it_is(tmp_path):
    img = scene(6, 320, 200)
    p = tmp_path / "a.jpg"
    p.write_bytes(_jpeg_with_icc(img))
    ref = rc.load_reference(str(p))
    want = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
    assert ref.rgb.shape == want.shape and np.array_equal(ref.rgb, want)
    assert ref.profile is None and ref.bits == 8 and ref.converted


def test_a_jpeg_profile_is_read_and_converted(tmp_path):
    img = np.zeros((40, 60, 3), np.uint8)
    img[:, :30] = (200, 60, 40)
    img[:, 30:] = (128, 128, 128)
    p = tmp_path / "pro.jpg"
    p.write_bytes(_jpeg_with_icc(img, cm.PROPHOTO_ICC_BYTES))
    assert rc.jpeg_icc(p.read_bytes()) == cm.PROPHOTO_ICC_BYTES
    ref = rc.load_reference(str(p))
    assert ref.profile and "ProPhoto" in ref.profile and ref.converted
    red = ref.rgb[20, 10].astype(int)
    grey = ref.rgb[20, 50].astype(int)
    assert red[0] >= 250 and red[1] <= 5            # ProPhoto's orange-red is past sRGB
    assert grey.max() - grey.min() <= 1 and abs(int(grey[1]) - 146) <= 2


def test_the_jpeg_orientation_is_applied(tmp_path):
    img = np.zeros((40, 80, 3), np.uint8)
    img[:, :10] = 255                                # a white band on the left
    p = tmp_path / "o.jpg"
    p.write_bytes(_jpeg_with_icc(img, orientation=6))
    ref = rc.load_reference(str(p))
    assert ref.rgb.shape[:2] == (80, 40)             # stood up
    assert ref.rgb[:8, :, 0].mean() > 200            # the band is now along the top


def test_a_16_bit_tiff_with_a_wide_profile(tmp_path):
    import tifffile
    img16 = np.zeros((50, 64, 3), np.uint16)
    img16[:, :32] = np.array([200, 60, 40]) * 257
    img16[:, 32:] = np.linspace(0, 65535, 32, dtype=np.float64)[None, :, None].astype(np.uint16)
    p = tmp_path / "wide.tif"
    tifffile.imwrite(str(p), img16, photometric="rgb", iccprofile=cm.PROPHOTO_ICC_BYTES)
    ref = rc.load_reference(str(p))
    assert ref.bits == 16 and ref.converted and "ProPhoto" in ref.profile
    ramp = ref.rgb[25, 32:].astype(int)
    assert np.all(np.diff(ramp[:, 1]) >= 0)          # still a rising ramp
    assert np.all(ramp.max(axis=1) - ramp.min(axis=1) <= 1)   # grey stays grey


def test_a_grey_tiff_gives_three_equal_channels(tmp_path):
    import tifffile
    g = (np.linspace(0, 65535, 100)[None, :] * np.ones((30, 1))).astype(np.uint16)
    p = tmp_path / "grey.tif"
    tifffile.imwrite(str(p), g, photometric="minisblack")
    ref = rc.load_reference(str(p))
    assert ref.rgb.shape == (30, 100, 3)
    assert np.array_equal(ref.rgb[..., 0], ref.rgb[..., 1])
    assert np.array_equal(ref.rgb[..., 0], ref.rgb[..., 2])


def test_a_big_reference_is_reduced(tmp_path):
    img = scene(7, 1600, 1000)
    p = tmp_path / "big.png"
    cv2.imwrite(str(p), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    ref = rc.load_reference(str(p), max_side=800)
    assert ref.rgb.shape[:2] == (500, 800) and ref.full_size == (1600, 1000)


def test_a_file_that_is_not_a_picture_raises(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"not a picture")
    with pytest.raises((ValueError, OSError)):
        rc.load_reference(str(p))


# --------------------------------------------------------------------------- #
# The canvas (offscreen)
# --------------------------------------------------------------------------- #

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QPointF, Qt              # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

_app = QApplication.instance() or QApplication([])
_KEEP = []


class _Store(dict):
    def value(self, k, d=None):
        return self.get(k, d)

    def setValue(self, k, v):
        self[k] = v


@pytest.fixture
def ip(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from ui.main_window import MainWindow
    w = MainWindow()
    _KEEP.append(w)
    w.resize(1400, 900)
    w.show()
    shown = scene(11, 900, 600)
    paths = []
    for name, arr in (("a.png", shown), ("b.png", scene(12, 900, 600))):
        p = tmp_path / name
        cv2.imwrite(str(p), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        paths.append(str(p))
    imgs = [CCRImage(p) for p in paths]
    for im in imgs:
        im.converted = True
    monkeypatch.setattr(ccr_backend, "images", imgs)
    monkeypatch.setattr(ccr_backend, "file_paths", [im.file_path for im in imgs])
    monkeypatch.setattr(ccr_backend, "save_catalog", lambda *a, **k: None)
    ip = w.image_preview
    ip.compare.store = _Store()
    ip.compare.threaded = False
    ip.update_preview(0)
    _app.processEvents()
    ref, _F, _w, _h = _make_reference(shown, angle=4.0, scale=1.2, crop=(40, 30, 860, 570))
    rp = tmp_path / "lab.jpg"
    cv2.imwrite(str(rp), cv2.cvtColor(ref, cv2.COLOR_RGB2BGR))
    ip._test_ref_path = str(rp)
    yield ip
    ip.compare.set_on(False)


def _turn_on_with_file(ip, monkeypatch):
    monkeypatch.setattr(ip.compare, "ask_source", lambda: "file")
    monkeypatch.setattr(ip.compare, "ask_file", lambda: ip._test_ref_path)
    assert ip.set_compare(True)
    _app.processEvents()


def test_the_toolbar_picture_sits_after_false_colour_and_collage_is_in_the_file_menu(ip):
    acts = ip.toolbar.actions()
    i = acts.index(ip.compare_action)
    assert acts[i - 1] is ip.fc_action and acts[i + 1] is ip.sample_action
    assert ip.compare_action.toolTip().startswith("Reference:")
    assert not ip.compare_action.icon().isNull()
    assert ip.collage_action not in acts
    mw = ip.window()
    bar = mw.menuBar().actions()          # kept alive, or PySide frees the File menu
    file_menu = [m for m in bar if m.text() == "File"][0].menu()
    assert ip.collage_action in file_menu.actions()


def test_turning_on_with_no_reference_asks_and_cancel_turns_it_off(ip, monkeypatch):
    monkeypatch.setattr(ip.compare, "ask_source", lambda: None)
    assert not ip.set_compare(True)
    assert not ip.compare.on and not ip.compare_action.isChecked()


def test_a_file_reference_is_lined_up_on_the_reference_side(ip, monkeypatch):
    from core.ccr_backend import ccr_backend
    _turn_on_with_file(ip, monkeypatch)
    c = ip.compare
    img = ccr_backend.images[0]
    assert img.compare_ref == {"file": ip._test_ref_path}
    assert c.clip is not None and c.clip.parentItem() is ip.pixmap_item
    assert c.clip.isVisible() and c._shown["lu"] is not None and c.status == ""
    assert c.clip.zValue() > 2.5
    # the reference's centre lands on the picture's centre
    pm = ip.pixmap_item.pixmap()
    t = c.item.transform()
    sz = c._shown["size"]
    q = t.map(QPointF(sz[0] / 2, sz[1] / 2))
    assert abs(q.x() - pm.width() / 2) < 0.03 * pm.width()
    assert abs(q.y() - pm.height() / 2) < 0.03 * pm.height()


def _clip_box(ip):
    return ip.pixmap_item.mapToScene(ip.compare.clip.path()).boundingRect()


def test_line_orientation_swap_and_drag_move_the_clip(ip, monkeypatch):
    _turn_on_with_file(ip, monkeypatch)
    c = ip.compare
    pic = ip.pixmap_item.mapToScene(ip.pixmap_item.boundingRect()).boundingRect()
    right = _clip_box(ip).intersected(pic)
    assert right.left() > pic.left() + 0.3 * pic.width()           # the right half
    c.toggle_swap()
    left = _clip_box(ip).intersected(pic)
    assert left.right() < pic.right() - 0.3 * pic.width()          # now the left half
    c.toggle_swap()
    c.set_upright(False)
    below = _clip_box(ip).intersected(pic)
    assert below.top() > pic.top() + 0.3 * pic.height()
    assert ip.compare.store["view/compare_line"] == "level"
    c.set_upright(True)
    # drag the line to a quarter of the picture
    view = ip.view
    pv = c._picture_vp()
    x0 = c.line_pos()
    assert c.hit(QPoint(int(x0), int(pv.center().y())))
    c.press(QPoint(int(x0), int(pv.center().y())))
    c.drag_to(QPoint(int(pv.left() + pv.width() / 4), int(pv.center().y())))
    c.release()
    assert abs(c.line_pos() - (pv.left() + pv.width() / 4)) <= 1.5
    # locked: the view doesn't hand it the mouse
    c.set_locked(True)
    assert not c.can_drag(QPoint(int(c.line_pos()), int(pv.center().y())))


def test_the_view_drags_the_line_with_the_mouse(ip, monkeypatch):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    _turn_on_with_file(ip, monkeypatch)
    c, view = ip.compare, ip.view
    pv = c._picture_vp()
    start = QPointF(c.line_pos(), pv.center().y())
    end = QPointF(pv.left() + pv.width() * 0.8, pv.center().y())
    for kind, pos, btn, btns in ((QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                                 (QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
                                 (QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)):
        ev = QMouseEvent(kind, pos, view.viewport().mapToGlobal(pos.toPoint()), btn, btns,
                         Qt.NoModifier)
        {QEvent.MouseButtonPress: view.mousePressEvent, QEvent.MouseMove: view.mouseMoveEvent,
         QEvent.MouseButtonRelease: view.mouseReleaseEvent}[kind](ev)
    assert abs(c.line_pos() - end.x()) <= 1.5 and not c.dragging


def test_the_scopes_never_see_it(ip, monkeypatch):
    before = ip._capture_display_image(max_w=200)
    _turn_on_with_file(ip, monkeypatch)
    after = ip._capture_display_image(max_w=200)
    a = before[0] if isinstance(before, tuple) else before
    b = after[0] if isinstance(after, tuple) else after
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_an_unrelated_reference_is_shown_whole_with_a_note(ip, monkeypatch, tmp_path):
    p = tmp_path / "other.png"
    cv2.imwrite(str(p), scene(40, 500, 700))
    monkeypatch.setattr(ip.compare, "ask_source", lambda: "file")
    monkeypatch.setattr(ip.compare, "ask_file", lambda: str(p))
    assert ip.set_compare(True)
    c = ip.compare
    assert c._shown["lu"] is None and c.status.startswith("Couldn't line it up")
    c.set_lineup(False)
    assert c.status == "" and c.store["view/compare_lineup"] is False


def test_a_frame_reference_from_the_list_and_the_picker(ip, monkeypatch):
    from core.ccr_backend import ccr_backend
    c = ip.compare
    other = ccr_backend.images[1]
    assert c.use_frame(other)
    img = ccr_backend.images[0]
    assert img.compare_ref["frame"][1] == other.display_name
    assert c.on and ip.compare_action.isChecked() and c._shown is not None
    # unrelated pictures: shown whole
    assert c.status.startswith("Couldn't line it up")
    # the picker seam
    c.remove_reference()
    assert img.compare_ref is None and c.status.startswith("No reference")
    monkeypatch.setattr(c, "ask_frame", lambda im: other)
    assert c.choose(img, "frame") and img.compare_ref["frame"][1] == other.display_name


def test_the_list_offers_it_on_another_frame_only(ip, monkeypatch):
    from widgets import thumbnail_list as tl
    src = open(tl.__file__).read()
    assert "Compare the Frame on Screen with This" in src
    assert "indices[0] != getattr(ip, \"current_idx\", None)" in src


def test_the_menu(ip, monkeypatch):
    _turn_on_with_file(ip, monkeypatch)
    m = ip.compare.build_menu()
    texts = [a.text() for a in m.actions() if a.text()]
    assert texts == ["Upright Line", "Level Line", "Swap Sides", "Centre the Line", "Lock Line",
                     "Line Up with the Frame", "Line Up Again", "Choose a File…",
                     "Choose an Open Frame…", "Show the Reference File", "Remove Reference"]


def test_compare_ref_round_trips_through_the_catalogue(tmp_path):
    from core import catalog
    for ref in ({"file": "/x/lab.tif"}, {"frame": ["k|abc", "DSCF0050"]}, None):
        assert catalog._compare_ref_from_json(catalog._compare_ref_to_json(ref)) == ref


def test_the_line_up_runs_off_the_gui_thread(ip, monkeypatch):
    import time
    c = ip.compare
    c.threaded = True
    monkeypatch.setattr(c, "ask_source", lambda: "file")
    monkeypatch.setattr(c, "ask_file", lambda: ip._test_ref_path)
    assert ip.set_compare(True)
    assert c.status == "Lining up…"
    t0 = time.time()
    while c._shown is None and time.time() - t0 < 20:
        _app.processEvents()
        time.sleep(0.01)
    assert c._shown is not None and c._shown["lu"] is not None and c.status == ""


def test_the_clip_never_reaches_past_the_picture(ip, monkeypatch):
    _turn_on_with_file(ip, monkeypatch)
    br = ip.compare.clip.path().boundingRect()
    pic = ip.pixmap_item.boundingRect()
    assert pic.adjusted(-0.5, -0.5, 0.5, 0.5).contains(br)
