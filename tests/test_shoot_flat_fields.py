#!/usr/bin/env python3
"""Capture → Shoot Flat Fields (spec/ir-dust-and-flat-sets.md §5): each
chosen light lit alone, its exposure found from the RAW's brightest part,
the flats built with their light, format and carrier opening and saved in
the library, and Capture's light, exposure, folder and live view put back."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import base_exposure as be  # noqa: E402
from core import flat_capture as fc  # noqa: E402
from core import flat_field as ff  # noqa: E402
from test_film_base_exposure import SPEEDS, ISOS  # noqa: E402

# level of each LED's brightest part per ISO-100 second, at full
K = {"w": 150.0, "r": 40.0, "g": 60.0, "b": 25.0, "ir": 0.134 / 256}


@pytest.fixture(autouse=True)
def _clean_library():
    ff.set_library([])
    yield
    ff.set_library([])


# --------------------------------------------------------------------------- #
# reading a flat shot
# --------------------------------------------------------------------------- #
def _bayer(level=(0.5, 0.6, 0.4), h=240, w=360, black=1024, white=16383, clip_box=None):
    """An RGGB mosaic of a lit opening: an off-centre hotspot, a dark mask
    round it."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    field = 1.0 + 0.6 * np.exp(-(((xx / w - 0.55) ** 2 + (yy / h - 0.45) ** 2) / (2 * 0.15 ** 2)))
    field /= field.max()
    inside = (xx > 0.1 * w) & (xx < 0.9 * w) & (yy > 0.08 * h) & (yy < 0.92 * h)
    cols = np.zeros((h, w), np.uint8)
    cols[0::2, 1::2] = 1
    cols[1::2, 0::2] = 3
    cols[1::2, 1::2] = 2
    lv = np.array([level[0], level[1], level[2], level[1]], np.float32)[cols]
    v = np.where(inside, field * lv, 0.003)
    rng = np.random.default_rng(1)
    v = v + rng.normal(0, 0.004, v.shape)
    raw = black + np.clip(v, 0, None) * (white - black)
    if clip_box is not None:
        y0, y1, x0, x1 = clip_box
        raw[y0:y1, x0:x1] = white + 500
    raw = np.clip(raw, 0, white).astype(np.uint16)
    return raw, cols, "RGBG", [black] * 4, white


def test_a_reading_is_each_colours_brightest_part_and_both_paths_agree():
    raw, cols, desc, black, white = _bayer()
    r = fc.reading_from_mosaic(raw, cols, desc, black, white)
    for ch, want in zip("RGB", (0.5, 0.6, 0.4)):
        assert r.levels[ch] == pytest.approx(want, rel=0.03), ch
        assert not r.clipped[ch]
    generic = fc.reading_from_mosaic(raw, cols, desc, black, white, block=fc.BLOCK + 1)
    for ch in "RGB":                         # an odd block can't use the 2x2 path
        assert generic.levels[ch] == pytest.approx(r.levels[ch], rel=0.01)
    assert r.level("w") == r.levels["G"] and r.level("r") == r.levels["R"]


def test_a_clipped_hotspot_is_clipped():
    raw, cols, desc, black, white = _bayer(clip_box=(100, 116, 190, 206))
    r = fc.reading_from_mosaic(raw, cols, desc, black, white)
    assert all(r.clipped[ch] for ch in "RGB")
    assert r.is_clipped("w") and r.is_clipped("b")
    # one colour over: only its own shot and white care
    raw, cols, desc, black, white = _bayer(level=(1.4, 0.6, 0.4))
    r = fc.reading_from_mosaic(raw, cols, desc, black, white)
    assert r.is_clipped("r") and r.is_clipped("w") and not r.is_clipped("g")


@pytest.mark.skipif(not os.path.exists("/mnt/user-data/uploads/R0016_645_XP2_003_BW.raf"),
                    reason="the user's IR shot isn't here")
def test_the_users_ir_shot_with_no_film():
    r = fc.read_flat("/mnt/user-data/uploads/R0016_645_XP2_003_BW.raf")
    assert 0.10 < r.level("ir") < 0.16 and not r.is_clipped("ir")


# --------------------------------------------------------------------------- #
# finding the exposures
# --------------------------------------------------------------------------- #
def camera(k=K, ceiling=0.95):
    def shoot(key, levels, exp):
        e = be.exposure_of(exp["shutter"], exp["iso"])
        led = levels.get(key if key != "w" else "w", 0) / 255.0
        level = k[key] * e * led
        ch = fc.CHANNEL.get(key)
        names = [ch] if ch else list("RGB")
        r = fc.FlatReading(levels={n: min(level, 1.03) for n in names},
                           clipped={n: level >= ceiling for n in names})
        r.path = f"{key}-{exp['shutter']}-{exp['iso']}-{levels.get(key)}.raf"
        return r
    return shoot


def run(cal, shoot, log=None):
    while not cal.done:
        key, levels, exp = cal.next_shot()
        assert levels == {**{k: 0 for k in be.LIGHT_KEYS}, key: levels[key]}   # alone
        if log is not None:
            log.append((key, exp["shutter"], exp["iso"]))
        cal.landed(shoot(key, levels, exp))
    return cal


def test_each_light_lands_in_its_window_in_a_few_shots():
    log = []
    cal = run(fc.FlatShooting(["ir", "rgb", "white"], SPEEDS, ISOS, "1/60",
                              ir_start={"shutter": "8s", "iso": "3200"}), camera(), log)
    assert cal.error is None, cal.error
    assert list(cal.result) == ["white", "rgb", "ir"]          # always this order
    w = cal.result["white"]["w"]
    assert log[0] == ("w", "1/60", "100")                       # the camera's speed first
    assert fc.ACCEPT[0] <= w["level"] <= fc.ACCEPT[1] and w["iso"] == "100"
    for key in "rgb":
        e = cal.result["rgb"][key]
        assert fc.ACCEPT[0] <= e["level"] <= fc.ACCEPT[1], (key, e)
        assert e["path"].startswith(key + "-")
    ir = cal.result["ir"]["ir"]
    assert (ir["shutter"], ir["iso"]) == ("8s", "3200")         # 13 % at the start: kept
    assert cal.shots <= 9
    assert fc.summary(cal.result) == ("White 1/250, ISO 100 (60 %); RGB R 1/80 · G 1/80 · "
                                      "B 1/50, ISO 100; IR 8 s, ISO 3200 (13 %)")


def test_a_weak_ir_goes_to_30_s_then_raises_the_iso():
    k = dict(K, ir=K["ir"] / 3)
    cal = run(fc.FlatShooting(["ir"], SPEEDS, ISOS, "1/60",
                              ir_start={"shutter": "8s", "iso": "3200"}), camera(k))
    ir = cal.result["ir"]["ir"]
    assert ir["shutter"] == "30s" and be.iso_number(ir["iso"]) <= 12800
    assert fc.ACCEPT_IR[0] <= ir["level"] <= fc.ACCEPT_IR[1]


def test_too_bright_at_the_shortest_speed_dims_the_led():
    k = dict(K, w=400.0)
    cal = run(fc.FlatShooting(["white"], SPEEDS, ISOS, "1/60"), camera(k))
    w = cal.result["white"]["w"]
    assert w["dimmed"] and w["levels"]["w"] < 255 and w["shutter"] == "1/250"
    assert any("dimmed" in n for n in cal.notes)


def test_no_light_says_so():
    cal = run(fc.FlatShooting(["rgb"], SPEEDS, ISOS, "1/60"), camera(dict(K, r=0.0)))
    assert cal.done and not cal.result and "red flat is still too dark" in cal.error
    assert "the red LED on" in cal.error


def test_a_dark_flat_at_the_cameras_limit_is_kept_with_a_note():
    k = dict(K, ir=K["ir"] / 40)                # 30 s, ISO 12800: ~7 %
    cal = run(fc.FlatShooting(["ir"], SPEEDS, ISOS, "1/60",
                              ir_start={"shutter": "8s", "iso": "3200"}), camera(k))
    assert cal.error is None
    ir = cal.result["ir"]["ir"]
    assert fc.FLOOR <= ir["level"] < fc.ACCEPT_IR[0]
    assert any("darker than ideal" in n for n in cal.notes)


def test_cancel_and_failure():
    cal = fc.FlatShooting(["white"], SPEEDS, ISOS, "1/60")
    cal.next_shot()
    cal.cancel()
    assert cal.done and cal.error == "cancelled" and cal.next_shot() is None
    cal = fc.FlatShooting(["white"], SPEEDS, ISOS, "1/60")
    cal.failed("The camera didn't fire.")
    assert cal.error == "The camera didn't fire."
    assert fc.FlatShooting([], SPEEDS, ISOS, "1/60").error == "No light chosen."


# --------------------------------------------------------------------------- #
# building the flats
# --------------------------------------------------------------------------- #
def _fake_decoders(seen):
    from test_flat_sets import _shot

    def decode(path):
        seen.append(("decode", os.path.basename(path)))
        return _shot()[0]

    def merge(paths):
        seen.append(("merge", [os.path.basename(p) for p in paths]))
        return _shot()[0]
    return decode, merge


def _result():
    def e(key, sh="1/125"):
        return {"key": key, "path": f"/tmp/{key}.raf", "shutter": sh, "iso": "100",
                "level": 0.6, "levels": fc.solo_levels(key), "dimmed": False}
    return {"white": {"w": e("w")}, "rgb": {k: e(k) for k in "rgb"},
            "ir": {"ir": dict(e("ir", "8s"), iso="3200")}}


def test_the_flats_are_built_tagged_and_saved_one_file_per_light_and_format(tmp_path):
    seen = []
    decode, merge = _fake_decoders(seen)
    saved = fc.build_flats(_result(), "645", str(tmp_path), decode=decode, merge=merge)
    assert [s["light"] for s in saved] == ["white", "rgb", "ir"]
    assert ("merge", ["r.raf", "g.raf", "b.raf"]) in seen       # RGB merged in R, G, B order
    for s in saved:
        p = ff.load_profile(s["path"])
        assert ff.profile_light(p) == s["light"] and ff.profile_format(p) == "645"
        assert ff.profile_opening(p)                               # follows the carrier
        assert p.meta["shot_by"] == "Capture" and "path" not in str(p.meta["shots"])
    assert os.path.basename(saved[0]["path"]) == "Flat white 645.ffc"
    assert saved[1]["profile"].name == "RGB flat · 6x4.5"
    # again: the same files, replaced
    again = fc.build_flats({"white": _result()["white"]}, "645", str(tmp_path),
                           decode=decode, merge=merge)
    assert again[0]["path"] == saved[0]["path"]
    assert len(os.listdir(tmp_path)) == 3
    any_fmt = fc.build_flats({"white": _result()["white"]}, None, str(tmp_path),
                             decode=decode, merge=merge)
    assert os.path.basename(any_fmt[0]["path"]) == "Flat white any.ffc"
    assert ff.profile_format(any_fmt[0]["profile"]) is None


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
from core import capture_light as cl  # noqa: E402
from test_capture_routes import _wait  # noqa: E402


@pytest.fixture
def flat_mon(film_mon, tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    mon, usb, light, lm, path = film_mon
    lib = tmp_path / "field_profiles"
    lib.mkdir()
    monkeypatch.setattr(ccr_backend, "field_profiles_dir", lambda: str(lib))
    shoot = camera()

    def read(p, _rect=None):
        levels, exp = usb.shots[p]
        key = next((k for k in ("ir", "w", "r", "g", "b") if levels.get(k)), "w")
        r = shoot(key, levels, exp)
        r.path = p
        return r
    mon._read_flat = read
    seen = []
    decode, merge = _fake_decoders(seen)
    mon._build_flats = lambda result, fmt, out, demosaic=True: fc.build_flats(
        result, fmt, out, decode=decode, merge=merge)
    mon._flats_threaded = False
    return mon, usb, light, lib, seen


from test_film_base_shoot import film_mon  # noqa: E402,F401


def test_the_button_needs_the_camera_and_the_light(flat_mon):
    mon, usb, light, lib, seen = flat_mon
    assert mon.flat_btn.isEnabled() and mon.flat_btn.text() == "Shoot Flat Fields…"
    mon._exp_choices = {}
    mon._refresh_flat_btn()
    assert not mon.flat_btn.isEnabled() and "USB camera" in mon.flat_btn.toolTip()


def test_shooting_flats_saves_them_and_puts_everything_back(flat_mon):
    mon, usb, light, lib, seen = flat_mon
    before = dict(mon._levels)
    asked = []
    mon._ask_flat_fields = lambda fmt: (asked.append(fmt)
                                        or {"format": "645", "lights": ["white", "rgb"]})
    mon.flat_btn.click()
    m = mon._measure
    assert m is not None and m["kind"] == "flat" and mon.flat_btn.text() == "Stop Flat Fields"
    temp = m["temp"]
    assert usb.dests[-1] == temp and usb.holds == [True]
    assert not mon.shoot_btn.isEnabled() and not mon.measure_btn.isEnabled()
    assert _wait(lambda: mon._measure is None and not mon._flat_building, 20)
    names = sorted(os.listdir(lib))
    assert names == ["Flat rgb 645.ffc", "Flat white 645.ffc"], mon.capture_caption.text()
    # each shot lit its own LED alone
    lit = [tuple(k for k, v in lv.items() if v) for lv, _e in usb.shots.values()]
    assert set(lit) == {("w",), ("r",), ("g",), ("b",)}
    # the library is read at once: a 645 merge gets the RGB flat
    import types
    trip = types.SimpleNamespace(film_format="645", merge_sources=["a", "b", "c"],
                                 is_merged=True)
    assert ff.profile_light(ff.profile_for(trip)) == "rgb"
    # put back: the light, the camera's exposure, the folder, live view; shots deleted
    assert mon._levels == before
    assert usb.sets[-2:] == [("shutter", "1/8"), ("iso", "250")]
    assert usb.dests[-1] == mon._usb_dest and usb.holds == [True, False]
    assert not os.path.exists(temp)
    assert mon.capture_caption.text().startswith("Flats saved: White light flat · 6x4.5, "
                                                 "RGB flat · 6x4.5 (White ")
    assert mon.flat_btn.text() == "Shoot Flat Fields…" and mon.flat_btn.isEnabled()
    assert mon.shoot_btn.isEnabled()
    from widgets import live_monitor as lm
    assert lm._settings().value("live/flat_lights") == "white,rgb"


def test_stop_waits_for_the_shot_and_saves_nothing(flat_mon):
    mon, usb, light, lib, seen = flat_mon
    assert mon.start_flat_fields({"format": None, "lights": ["rgb"]})
    temp = mon._measure["temp"]
    assert _wait(lambda: mon._measure is not None and mon._measure["in_flight"], 5)
    mon.flat_btn.click()                         # Stop
    assert mon._measure is not None and mon._measure["stopping"]
    assert _wait(lambda: mon._measure is None, 10)
    assert os.listdir(lib) == [] and not os.path.exists(temp)
    assert mon.capture_caption.text() == "Flat fields stopped; nothing was saved."
    assert seen == []


def test_a_failed_build_says_so(flat_mon):
    mon, usb, light, lib, seen = flat_mon

    def broken(*a, **k):
        raise ff.FieldProfileError("The calibration shot is essentially black")
    mon._build_flats = broken
    assert mon.start_flat_fields({"format": "645", "lights": ["white"]})
    assert _wait(lambda: mon._measure is None and not mon._flat_building, 20)
    assert mon.capture_caption.text().startswith("Couldn't build the flats: The calibration")
    assert os.listdir(lib) == []


def test_the_default_format_is_the_rolls_else_the_guides(flat_mon):
    mon, usb, light, lib, seen = flat_mon
    mon.canvas.guide = dict(mon.canvas.guide, format="6x7")
    assert mon._flat_default_format() == "6x7"
    mon._mw.capture_session = {"roll": "R0001", "format": "645"}
    assert mon._flat_default_format() == "645"


def test_the_window_asks_format_and_lights():
    from widgets.flat_shoot_dialog import FlatShootDialog
    d = FlatShootDialog("645", ["white", "ir"])
    assert d.values() == {"format": "645", "lights": ["white", "ir"]}
    d.light_boxes["white"].setChecked(False)
    d.light_boxes["ir"].setChecked(False)
    from PySide6.QtWidgets import QDialogButtonBox
    assert not d.buttons.button(QDialogButtonBox.Ok).isEnabled()
    d.format_combo.setCurrentIndex(0)
    d.light_boxes["rgb"].setChecked(True)
    assert d.values() == {"format": None, "lights": ["rgb"]}
