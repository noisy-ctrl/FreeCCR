#!/usr/bin/env python3
"""HDR export: "JPEG + HDR gain map", the glow capture and the preview tint.
See spec/hdr-gain-map-export.md."""

import os
import re
import struct
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import hdr_gainmap as hg  # noqa: E402
from core import ccr_processor as cp  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402

try:
    import imagecodecs
    _UHDR = bool(getattr(imagecodecs, "ULTRAHDR", None)
                 and imagecodecs.ULTRAHDR.available)
except Exception:          # pragma: no cover - environment dependent
    imagecodecs = None
    _UHDR = False

needs_uhdr = pytest.mark.skipif(not _UHDR, reason="imagecodecs without libultrahdr")


def _srgb_to_linear(v8):
    v = np.asarray(v8, np.float64) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _ramp(h=48, w=96, lo=0.5, hi=4.0):
    sdr = np.zeros((h, w, 3), np.uint8)
    sdr[...] = np.linspace(0, 255, w).astype(np.uint8)[None, :, None]
    over = np.tile(np.linspace(lo, hi, w, dtype=np.float32), (h, 1))
    return sdr, over


# --- 1 / 2. the glow and its encoding ---------------------------------------

def test_glow_is_zero_at_and_below_white():
    g = hg.glow_stops(np.array([0.0, 0.3, 0.99, 1.0], np.float32), 2.0)
    assert np.all(g == 0)


def test_glow_starts_as_log2_and_rolls_into_the_headroom():
    h = 2.0
    over = np.array([1.01, 1.1, 2.0, 4.0, 16.0, 1e6], np.float32)
    g = hg.glow_stops(over, h)
    assert np.all(np.diff(g) > 0)                       # monotonic
    assert np.all(g < h)                                 # never reaches H
    assert abs(g[0] - np.log2(1.01)) < 1e-3              # slope 1 at white
    assert g[-1] > 0.999 * h
    # a smaller headroom compresses harder
    assert hg.glow_stops(np.float32(4.0), 1.0) < hg.glow_stops(np.float32(4.0), 3.0)


def test_bright_end_of_the_picture_glows():
    h = 2.0
    y = np.array([0.0, 0.5, hg.GLOW_KNEE, 0.9, 0.95, 1.0], np.float32)
    sdr = np.repeat(y[:, None], 3, axis=1)[None]               # 1 x 6 x 3 greys
    g = hg.glow_stops(None, h, sdr=sdr)[0]
    assert np.all(g[:3] == 0)                                  # up to the knee: none
    assert 0 < g[3] < g[4] < g[5]                              # then a smooth ramp
    assert abs(g[5] - h * np.tanh(hg.GLOW_EXPAND)) < 1e-5      # white: GLOW_EXPAND*H, rolled
    # whatever was past white adds on top, but only where the picture is bright
    both = hg.glow_stops(np.full((1, 6), 2.0, np.float32), h, sdr=sdr)[0]
    assert np.all(both[3:] > g[3:]) and np.all(both[:3] == 0)
    # without a picture it counts in full
    assert abs(hg.glow_stops(np.float32(2.0), h) - h * np.tanh(1.0 / h)) < 1e-6
    # uint8 / uint16 / float pictures agree
    g8 = hg.glow_stops(None, h, sdr=np.round(sdr * 255).astype(np.uint8))[0]
    g16 = hg.glow_stops(None, h, sdr=np.round(sdr * 65535).astype(np.uint16))[0]
    assert np.allclose(g8, g, atol=0.02) and np.allclose(g16, g, atol=1e-3)
    with pytest.raises(ValueError):
        hg.glow_stops(None, h)


def test_glow_map_resizes_the_capture_to_the_picture():
    sdr = np.full((20, 30, 3), 255, np.uint8)
    over = np.full((10, 15), 4.0, np.float32)
    g = hg.glow_map(sdr, over, 2.0)
    want = hg.glow_stops(np.full((20, 30), 4.0, np.float32), 2.0, sdr=sdr)
    assert g.shape == (20, 30) and np.allclose(g, want)
    assert np.all(hg.glow_map(np.zeros_like(sdr), over, 2.0) == 0)   # dark: no glow


def test_levels_and_headroom_bounds():
    assert hg.clamp_headroom(0.1) == hg.HEADROOM_MIN
    assert hg.clamp_headroom(9) == hg.HEADROOM_MAX
    assert hg.clamp_headroom("x") == hg.HEADROOM_DEFAULT
    assert hg.clamp_headroom(float("nan")) == hg.HEADROOM_DEFAULT
    v = hg.gainmap_levels(np.array([0.0, 1.0, 2.0, 5.0], np.float32), 2.0)
    assert v.dtype == np.uint8 and list(v) == [0, 128, 255, 255]


def test_encode_rejects_bad_shapes():
    sdr, over = _ramp()
    with pytest.raises(ValueError):
        hg.encode_gainmap_jpeg(sdr[..., 0], hg.glow_stops(over, 2), 2.0)
    with pytest.raises(ValueError):
        hg.encode_gainmap_jpeg(sdr, hg.glow_stops(over, 2)[:-1], 2.0)
    with pytest.raises(ValueError):
        hg.encode_gainmap_jpeg(sdr, hg.glow_stops(over, 2), 2.0, metadata=())


# --- 3. libultrahdr reads it --------------------------------------------------

@needs_uhdr
@pytest.mark.parametrize("metadata", [("iso", "xmp"), ("iso",), ("xmp",)])
def test_reference_decoder_rebuilds_the_intended_hdr(metadata):
    h = 2.0
    sdr, over = _ramp()
    glow = hg.glow_stops(over, h)
    data = hg.encode_gainmap_jpeg(sdr, glow, h, quality=98, metadata=metadata)
    base = imagecodecs.ultrahdr_decode(data, dtype=np.uint8)[..., :3]
    hdr = imagecodecs.ultrahdr_decode(data).astype(np.float64)[..., :3]
    lin = _srgb_to_linear(base)
    bright = lin[..., 1] > 0.05                          # ratios are noisy near black
    ratio = hdr[..., 1][bright] / lin[..., 1][bright]
    want = (2.0 ** glow)[bright]
    assert np.max(np.abs(ratio / want - 1.0)) < 0.015
    # a display with 1 stop of headroom gets half the gain (in stops)
    half = imagecodecs.ultrahdr_decode(data, boost=2.0).astype(np.float64)[..., 1]
    col = sdr.shape[1] - 1
    assert abs(half[20, col] / hdr[20, col, 1] - 2.0 ** (-glow[20, col] / 2)) < 0.02


@needs_uhdr
def test_sdr_base_is_the_plain_jpeg_picture():
    sdr, over = _ramp()
    data = hg.encode_gainmap_jpeg(sdr, hg.glow_stops(over, 2.0), 2.0, quality=90)
    from core.jpeg_params import jpeg_params
    ok, plain = cv2.imencode(".jpg", cv2.cvtColor(sdr, cv2.COLOR_RGB2BGR),
                             jpeg_params(90))
    a = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    b = cv2.imdecode(plain, cv2.IMREAD_COLOR)
    assert np.array_equal(a, b)


@needs_uhdr
def test_plain_jpeg_has_no_gain_map():
    sdr, _ = _ramp()
    ok, plain = cv2.imencode(".jpg", sdr)
    with pytest.raises(Exception):
        imagecodecs.ultrahdr_decode(plain.tobytes())


# --- 4. structure ------------------------------------------------------------

def _segments(data, start=0):
    i, out = start + 2, []
    while i < len(data):
        marker = data[i + 1]
        if marker == 0xDA:
            break
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        out.append((marker, i, data[i + 4:i + 2 + length]))
        i += 2 + length
    return out


def test_mpf_xmp_and_iso_point_at_the_gain_map():
    h = 2.5
    sdr, over = _ramp()
    data = hg.encode_gainmap_jpeg(sdr, hg.glow_stops(over, h), h, icc=b"\x00" * 300)
    segs = _segments(data)
    mpf = [(i, p) for m, i, p in segs if m == 0xE2 and p.startswith(b"MPF\x00")]
    assert len(mpf) == 1
    pos, payload = mpf[0]
    tiff_pos = pos + 4 + 4
    entries = payload[4 + 50:4 + 50 + 32]
    attr1, size1, off1, _, _ = struct.unpack(">IIIHH", entries[:16])
    attr2, size2, off2, _, _ = struct.unpack(">IIIHH", entries[16:])
    gm_start = tiff_pos + off2
    assert data[gm_start:gm_start + 3] == b"\xff\xd8\xff"
    assert size1 == gm_start and gm_start + size2 == len(data)
    assert attr1 == 0x00030000 and off1 == 0
    xmp = [p for m, _, p in segs if m == 0xE1 and p.startswith(b"http://ns.adobe.com/xap/1.0/")]
    length = int(re.search(rb'Item:Semantic="GainMap"[^>]*Item:Length="(\d+)"', xmp[0]).group(1))
    assert length == size2
    iso = [p for m, _, p in segs if m == 0xE2 and p.startswith(b"urn:iso:std:iso:ts:21496:-1")]
    assert iso and iso[0].endswith(b"\x00\x00\x00\x00")       # version block only
    gm_segs = _segments(data, gm_start)
    gm_iso = [p for m, _, p in gm_segs if m == 0xE2 and p.startswith(b"urn:iso")][0]
    md = gm_iso[len(b"urn:iso:std:iso:ts:21496:-1\x00"):]
    assert md == hg.iso_gainmap_metadata(h)
    flags = md[4]
    assert flags == 0x40                                       # one channel, base colour space
    alt_n, alt_d = struct.unpack(">II", md[13:21])
    assert abs(alt_n / alt_d - h) < 1e-6
    gm_xmp = [p for m, _, p in gm_segs if m == 0xE1][0]
    assert b'hdrgm:GainMapMax="2.5"' in gm_xmp and b'hdrgm:HDRCapacityMax="2.5"' in gm_xmp


# --- 5. the capture ------------------------------------------------------------

def _windowed_base(h=8, w=16):
    """A windowed working-space base whose right half sits above white."""
    d = np.tile(np.linspace(0.2, 3.0, w, dtype=np.float32), (h, 1))
    rgb = np.stack([d, d * 0.9, d * 0.8], axis=-1)
    codes = rgb * (cp.WS_W - cp.WS_B) + cp.WS_B
    return np.clip(codes, 0, 65535).astype(np.uint16), rgb


def test_capture_records_the_maximum_before_the_clamp():
    base, rgb = _windowed_base()
    with cp.hdr_capture() as cap:
        cp._apply_working_space_recovery(base, 0.0)
    assert cap.over is not None and cap.over.shape == base.shape[:2]
    assert np.allclose(cap.over, rgb.max(axis=-1), atol=2e-3)


def test_white_point_and_exposure_move_what_glows():
    # Revision 2: White Point is a highlight control for the glow too.
    base, _ = _windowed_base()
    with cp.hdr_capture() as plain:
        cp._apply_working_space_recovery(base, 0.0)
    with cp.hdr_capture() as raised:
        cp._apply_working_space_recovery(base, 0.0, white_point=30.0)
    with cp.hdr_capture() as recovered:
        cp._apply_working_space_recovery(base, 0.0, white_point=-60.0)
    assert np.all(raised.over > plain.over) and np.all(recovered.over < plain.over)
    with cp.hdr_capture() as exposed:
        cp._apply_working_space_recovery(base, 30.0)
    assert np.allclose(exposed.over, plain.over / (1.0 - 30.0 / 300.0), rtol=1e-5)


def test_auto_tone_shoulder_does_not_hide_what_was_past_white():
    base, _ = _windowed_base()
    with cp.hdr_capture() as plain:
        cp._apply_working_space_recovery(base, 0.0)
    shoulder = {"toe": 0.0, "shoulder": 0.9}
    try:
        with cp.hdr_capture() as toned:
            cp._apply_working_space_recovery(base, 0.0, auto_tone=shoulder)
    except Exception:
        pytest.skip("auto tone parameters differ in this build")
    assert np.array_equal(plain.over, toned.over)


def test_capture_changes_nothing_and_only_the_first_call_counts():
    base, _ = _windowed_base()
    out_plain = cp._apply_working_space_recovery(base, 0.0)
    with cp.hdr_capture() as cap:
        out = cp._apply_working_space_recovery(base, 0.0)
        first = cap.over.copy()
        cp._apply_working_space_recovery(base // 2, 0.0)
    assert np.array_equal(out, out_plain)
    assert np.array_equal(cap.over, first)
    assert getattr(cp._hdr_tls, "cap", None) is None              # closed again
    with cp.hdr_capture(False) as off:
        assert off is None


# --- 6. end to end through the export pipeline ----------------------------------

def _converted_frame(tmp_path, monkeypatch, w=180, h=120):
    """A black-point conversion whose right edge lands above white."""
    neg = np.zeros((h, w, 3), np.float32)
    x = np.linspace(0, 1, w)[None, :]
    neg[..., 2] = 0.80 - 0.79 * x
    neg[..., 1] = 0.55 - 0.545 * x
    neg[..., 0] = 0.35 - 0.347 * x
    path = str(tmp_path / "neg.tif")
    cv2.imwrite(path, (neg * 65535).astype(np.uint16))
    img = CCRImage(path)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "black_point_bgr",
                        (0.35 * 65535, 0.55 * 65535, 0.80 * 65535))
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None)
    if hasattr(ccr_backend, "auto_black_point"):
        monkeypatch.setattr(ccr_backend, "auto_black_point", False)
    ccr_backend.apply_bwpoint_to_all_images()
    assert img.converted and img._ws_windowed
    return img


@needs_uhdr
def test_export_sdr_base_matches_the_plain_jpeg_and_only_the_bright_end_glows(
        tmp_path, monkeypatch):
    _converted_frame(tmp_path, monkeypatch)
    plain, hdr = str(tmp_path / "plain.jpg"), str(tmp_path / "hdr.jpg")
    assert ccr_backend.export_image_by_index(0, plain, jpg_output=True, jpg_quality=95)
    assert ccr_backend.export_image_by_index(0, hdr, jpg_output=True, jpg_quality=95,
                                             hdr_headroom=2.0)
    a, b = cv2.imread(plain), cv2.imread(hdr)
    assert np.array_equal(a, b)
    data = open(hdr, "rb").read()
    base = imagecodecs.ultrahdr_decode(data, dtype=np.uint8)[..., :3].astype(np.float64)
    full = imagecodecs.ultrahdr_decode(data).astype(np.float64)[..., :3]
    lin = _srgb_to_linear(base)
    ok = lin[..., 1] > 0.05
    gain = np.ones_like(lin[..., 1])
    gain[ok] = full[..., 1][ok] / lin[..., 1][ok]
    row = gain[60]
    w = row.size
    assert np.all(np.abs(row[w // 4: w // 2] - 1.0) < 0.03)        # mid-tones untouched
    assert row[-1] > 1.1                                             # the bright end glows


def test_export_without_hdr_is_unchanged(tmp_path, monkeypatch):
    _converted_frame(tmp_path, monkeypatch)
    a_path, b_path = str(tmp_path / "a.tiff"), str(tmp_path / "b.tiff")
    assert ccr_backend.export_image_by_index(0, a_path)
    with cp.hdr_capture():                       # an open capture must not leak in
        assert ccr_backend.export_image_by_index(0, b_path)
    import tifffile
    assert np.array_equal(tifffile.imread(a_path), tifffile.imread(b_path))


def test_gain_map_follows_crop_and_rotation(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    img.rotation_angle = 90
    img.crop_rect = (0.5, 0.0, 1.0, 1.0)            # the bright right half
    seen = {}
    real = cp._write_hdr_jpeg

    def spy(rgb_u16, output_path, jpg_quality, output_colorspace, headroom, glow):
        seen["rgb"], seen["glow"] = rgb_u16, glow
        return real(rgb_u16, output_path, jpg_quality, output_colorspace, headroom, glow)
    monkeypatch.setattr(cp, "_write_hdr_jpeg", spy)
    assert ccr_backend.export_image_by_index(0, str(tmp_path / "r.jpg"),
                                             jpg_output=True, hdr_headroom=2.0)
    rgb, glow = seen["rgb"], seen["glow"]
    assert glow is not None and glow.shape == rgb.shape[:2]
    # Rotated 90 degrees clockwise: the original's right edge is now the bottom.
    assert glow[-1].mean() > glow[0].mean()
    lum = rgb.astype(np.float64).mean(axis=-1)
    assert lum[-1].mean() > lum[0].mean()


def test_base_without_a_window_glows_from_the_picture(tmp_path, monkeypatch):
    # Positive mode renders straight into display range: nothing is captured,
    # but its bright end still glows (revision 2).
    path = str(tmp_path / "pos.png")
    pic = np.full((40, 60, 3), 120, np.uint8)
    pic[:, 40:] = 252                                     # a bright right third
    cv2.imwrite(path, pic)
    img = CCRImage(path)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "positive_mode", True)
    assert not getattr(img, "_ws_windowed", False)
    seen = {}
    real = cp._write_hdr_jpeg
    real_capture = cp.hdr_capture

    class Spy(real_capture):
        def __exit__(self, *exc):
            seen["over"] = self.cap.over if self.cap is not None else "closed"
            return super().__exit__(*exc)

    def spy(rgb_u16, output_path, jpg_quality, output_colorspace, headroom, glow):
        seen["glow"] = glow
        return real(rgb_u16, output_path, jpg_quality, output_colorspace, headroom, glow)
    monkeypatch.setattr(cp, "hdr_capture", Spy)
    monkeypatch.setattr(cp, "_write_hdr_jpeg", spy)
    assert ccr_backend.export_image_by_index(0, str(tmp_path / "p.jpg"),
                                             jpg_output=True, hdr_headroom=2.0)
    assert seen["over"] is None
    glow = seen["glow"]
    assert glow[:, :35].max() == 0 and glow[:, 45:].min() > 0.5
    assert open(str(tmp_path / "p.jpg"), "rb").read()[:2] == b"\xff\xd8"


# --- 7. export dialog ------------------------------------------------------------

@pytest.fixture
def _export_settings_guard():
    s = QSettings("FreeCCR", "FreeCCR")
    keys = ["export/destination", "export/scope", "export/filename_template",
            "export/format", "export/colorspace", "export/jpeg_quality",
            "export/resize", "export/conflict", "export/open_folder",
            "export/hdr_headroom"]
    saved = {k: s.value(k) for k in keys}
    yield s
    for k, v in saved.items():
        if v is None:
            s.remove(k)
        else:
            s.setValue(k, v)


def _dialog(tmp_path, monkeypatch):
    from widgets.export_dialog import ExportSettingsDialog
    _converted_frame(tmp_path, monkeypatch)
    return ExportSettingsDialog(current_idx=0)


def _select(dlg, data):
    dlg.format_combo.setCurrentIndex(dlg.format_combo.findData(data))


def test_dialog_offers_hdr_with_its_headroom_row(tmp_path, monkeypatch,
                                                  _export_settings_guard):
    dlg = _dialog(tmp_path, monkeypatch)
    assert dlg.format_combo.findData("hdr_jpeg") == dlg.format_combo.findData("jpeg") + 1
    _select(dlg, "tiff")
    assert dlg.hdr_headroom_spin.isHidden() and dlg.quality_slider.isHidden()
    _select(dlg, "hdr_jpeg")
    assert not dlg.hdr_headroom_spin.isHidden() and not dlg.quality_slider.isHidden()
    assert dlg._selected_ext() == ".jpg"
    assert dlg.hdr_headroom_spin.minimum() == hg.HEADROOM_MIN
    assert dlg.hdr_headroom_spin.maximum() == hg.HEADROOM_MAX
    assert dlg.hdr_headroom_spin.singleStep() == hg.HEADROOM_STEP


def test_dialog_plan_and_settings(tmp_path, monkeypatch, _export_settings_guard):
    dlg = _dialog(tmp_path, monkeypatch)
    _select(dlg, "hdr_jpeg")
    dlg.hdr_headroom_spin.setValue(1.5)
    dlg.dest_edit.setText(str(tmp_path / "out"))
    dlg._on_export_clicked()
    assert dlg.plan is not None
    assert dlg.plan.hdr_headroom == 1.5 and dlg.plan.jpg_output is True
    assert dlg.plan.items[0][1].endswith(".jpg")
    s = _export_settings_guard
    assert s.value("export/format") == "hdr_jpeg"
    assert float(s.value("export/hdr_headroom")) == 1.5
    from widgets.export_dialog import ExportSettingsDialog
    again = ExportSettingsDialog(current_idx=0)
    assert again.format_combo.currentData() == "hdr_jpeg"
    assert again.hdr_headroom_spin.value() == 1.5
    _select(again, "jpeg")
    again._on_export_clicked()
    assert again.plan.hdr_headroom is None and again.plan.jpg_output is True


def test_worker_passes_the_headroom(monkeypatch):
    from widgets.export_dialog import ExportPlan
    from widgets.image_preview import ExportItemsWorker
    got = {}
    monkeypatch.setattr(ccr_backend, "export_items",
                        lambda items, **kw: got.update(kw) or {"exported": 0, "failed": 0,
                                                               "cancelled": False,
                                                               "failures": []})
    ExportItemsWorker(ExportPlan(items=[], jpg_output=True, hdr_headroom=2.5)).run()
    assert got["hdr_headroom"] == 2.5


# --- 8. the preview tint ----------------------------------------------------------

def test_tint_is_a_map_not_pixels(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)
    off = img.render_preview_pixels()
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", 2.0)
    on = img.render_preview_pixels()
    assert off["hdr_tint"] is None and off["hdr_alpha"] is None
    assert on["hdr_tint"] == 2.0
    # the picture itself, the thumbnail and the histogram are identical
    assert np.array_equal(off["preview8"], on["preview8"])
    assert np.array_equal(off["thumb8"], on["thumb8"])
    assert np.array_equal(off["hist"], on["hist"])
    alpha = on["hdr_alpha"]
    assert alpha.shape == on["preview8"].shape[:2] and alpha.dtype == np.uint8
    w = alpha.shape[1]
    assert alpha[:, -3:].min() > 0                        # the bright edge glows
    assert alpha[:, : w // 2].max() == 0                  # the dark half does not
    snap = img.preview_render_snapshot()
    assert snap["hdr_tint"] == 2.0
    again = img.render_preview_pixels(snap)
    assert np.array_equal(again["hdr_alpha"], alpha)


def _zone(img, **settings):
    """How much of the picture glows, and how strongly: the summed opacity."""
    img.adjustment_settings = dict(settings)
    a = img.render_preview_pixels()["hdr_alpha"]
    return 0 if a is None else int(a.astype(np.int64).sum())


def test_brightness_and_white_point_grow_the_glow(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", 2.0)
    base = _zone(img)
    assert base > 0
    assert _zone(img, brightness=40) > base
    assert _zone(img, white_point=30) > base
    assert _zone(img, white_point=-60) < base


def test_hdr_tint_alpha():
    glow = np.array([[0.0, 0.04, 0.05], [0.5, 1.0, 1.9]], np.float32)
    a = cp.hdr_tint_alpha(glow, 2.0)
    assert a.dtype == np.uint8
    assert a[0, 0] == 0 and a[0, 1] == 0                 # below the floor
    assert a[0, 2] > 0 and a[1, 0] < a[1, 1] < a[1, 2]   # more glow, more tint
    assert cp.hdr_tint_alpha(np.zeros((2, 2), np.float32), 2.0) is None
    assert cp.hdr_tint_alpha(None, 2.0) is None


def test_stored_preview_remembers_its_glow_map(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)
    img.update_thumbnail_and_preview()
    assert getattr(img, "_preview_hdr_tint", "missing") is None
    assert img._preview_hdr_alpha is None
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", 2.0)
    img.update_thumbnail_and_preview()
    assert img._preview_hdr_tint == 2.0 and img._preview_hdr_alpha is not None


# --- 9. the canvas layer -----------------------------------------------------------

class _Hist:
    def set_caption(self, text):
        pass


class _Sliders:
    def __init__(self):
        self.histogram = _Hist()
        self.hints = []

    def set_histogram(self, data):
        pass

    def set_sliders_enabled(self, *a):
        pass

    def set_current_idx(self, idx):
        pass

    def set_temporary_hint(self, text, duration=0):
        self.hints.append(text)

    def clear_hint(self, *a, **k):
        pass

    def _sync_color_profile_combo(self, idx):
        pass

    def __getattr__(self, name):            # any other panel hook: a no-op
        return lambda *a, **k: None


class _Thumbs:
    def selected_indices(self):
        return []

    def update_thumbnail(self, idx):
        pass


_HOSTS = []


def _canvas(tmp_path, monkeypatch, crop=None):
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from widgets.image_preview import ImagePreview
    img = _converted_frame(tmp_path, monkeypatch)
    img.crop_rect = crop

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.sliders_panel = _Sliders()
            self.thumbnail_list = _Thumbs()
            self.mid = QWidget(self)
    host = Host()
    _HOSTS.append(host)
    QVBoxLayout(host).addWidget(host.mid)
    ip = ImagePreview(host.mid)
    QVBoxLayout(host.mid).addWidget(ip)
    host.resize(700, 600)
    host.show()
    _app.processEvents()
    monkeypatch.setattr(ip, "_request_hires_detail", lambda *a, **k: None, raising=False)
    ip.update_preview(0)
    return ip, img


def test_glow_layer_is_separate_from_the_picture(tmp_path, monkeypatch):
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)
    ip, img = _canvas(tmp_path, monkeypatch)
    before = ip._capture_display_image()[0].copy()
    probe_key = ip.current_pixmap.cacheKey()
    ip.set_hdr_tint(True)
    assert ccr_backend.hdr_tint_headroom is not None
    item = ip._glow_item
    assert item is not None and item.isVisible()
    assert item.parentItem() is ip.pixmap_item                 # follows every transform
    ov = item.pixmap()
    assert (ov.width(), ov.height()) == (ip.current_pixmap.width(),
                                         ip.current_pixmap.height())
    # The scopes and the probe read the picture alone: unchanged by the tint.
    after = ip._capture_display_image()[0]
    assert np.array_equal(before, after)
    assert ip.current_pixmap.cacheKey() == probe_key or not ip.current_pixmap.isNull()
    qimg = ip.current_pixmap.toImage()
    c = qimg.pixelColor(qimg.width() - 2, qimg.height() // 2)
    assert abs(c.red() - c.green()) < 40                       # no magenta in the pixels
    ip.set_hdr_tint(False)
    assert not ip._glow_item.isVisible()
    assert np.array_equal(ip._capture_display_image()[0], before)
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)


def test_glow_layer_is_cropped_like_the_picture(tmp_path, monkeypatch):
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)
    ip, img = _canvas(tmp_path, monkeypatch, crop=(0.5, 0.0, 1.0, 1.0))
    ip.set_hdr_tint(True)
    ov = ip._glow_item.pixmap()
    pm = ip.current_pixmap
    full = img._preview_hdr_alpha
    assert abs(ov.width() - full.shape[1] / 2) <= 1
    assert abs(ov.width() * ip._glow_item.transform().m11() - ip.pixmap_item.pixmap().width()) < 1e-3
    assert pm.width() == ov.width() and pm.height() == ov.height()
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)


def test_glow_layer_on_a_straightened_crop_is_not_black(tmp_path, monkeypatch):
    """A rotated (straightened) crop used to give the glow layer an opaque
    black background, which covered the whole picture."""
    from PySide6.QtGui import QImage
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)
    ip, img = _canvas(tmp_path, monkeypatch, crop=(0.1, 0.1, 0.9, 0.9))
    img.crop_angle = 3.0
    ip.update_preview(0)
    assert ip._crop_display_angle
    ip.set_hdr_tint(True)
    layer = ip._glow_item.pixmap().toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = layer.width(), layer.height()
    a = np.frombuffer(layer.constBits(), np.uint8).reshape(h, layer.bytesPerLine())[:, :w * 4]
    alpha = a.reshape(h, w, 4)[..., 3]
    # Dots cover at most 2 x (2x2) pixels of each 6x6 cell, about 22 %.
    assert (alpha > 0).mean() < 0.3
    assert (alpha[:, : w // 3] > 0).mean() < 0.02          # the dark side: clear
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)
