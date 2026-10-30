#!/usr/bin/env python3
"""Expose for the film base (spec/film-base-exposure.md): the camera's own
numbers, reading the base in the RAW, the plan (ISO 100 up to 1 s, then ISO up
by at most two stops, then longer speeds; the LED dimmed only past the
shortest speed), the measuring state machine against a fake linear camera, and
the film-base list."""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import base_exposure as be  # noqa: E402

# the GFX 50S II's lists, in its own spelling (test 10)
SPEEDS = ['1/16000', '1/12800', '1/10000', '1/8000', '1/6400', '1/5000', '1/4000',
          '1/3200', '1/2500', '1/2000', '1/1600', '1/1250', '1/1000', '1/800', '1/640',
          '1/500', '1/400', '1/320', '1/250', '1/200', '1/160', '1/125', '1/100', '1/80',
          '1/60', '1/50', '1/40', '1/30', '1/25', '1/20', '1/15', '1/13', '1/10', '1/8',
          '1/6', '1/5', '1/4', '1/3', '1/2.5', '1/2', '1/1.6', '1/1.3', '1s', '1.3s',
          '1.6s', '2s', '2.5s', '3s', '4s', '5s', '6s', '8s', '10s', '13s', '15s', '20s',
          '25s', '30s', '1m', '2m', '4m', '8m', '15m', '30m', '60m']
ISOS = ['100', '125', '160', '200', '250', '320', '400', '500', '640', '800', '1000',
        '1250', '1600', '2000', '2500', '3200', '4000', '5000', '6400', '8000', '10000',
        '12800', '-1', '-2', '-3']
CEILING = 1.034            # saturated photosites read this, measured (test 10)


# --------------------------------------------------------------------------- #
# the camera's numbers
# --------------------------------------------------------------------------- #
def test_the_cameras_own_spellings():
    assert be.seconds('1/16000') == pytest.approx(1 / 16000)
    assert be.seconds('1/2.5') == pytest.approx(0.4)
    assert be.seconds('1s') == 1.0 and be.seconds('1.3s') == pytest.approx(1.3)
    assert be.seconds('60m') == 3600.0 and be.seconds('2m') == 120.0
    assert be.seconds('bulb') is None and be.seconds('') is None and be.seconds(None) is None
    assert be.iso_number('100') == 100 and be.iso_number('-1') is None
    assert be.exposure_of('1/8', '250') == pytest.approx(0.3125)
    assert be.short_text('1s') == '1 s' and be.short_text('1/3') == '1/3'
    assert be.short_text('2m') == '2 min'


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #
RULES = be.default_rules(ISOS)


def test_default_rules():
    assert RULES["base_iso"] == "100" and RULES["max_iso"] == "400"     # two stops
    assert RULES["preferred"] == 1.0 and RULES["longest"] == 30.0
    assert RULES["shortest"] == pytest.approx(1 / 250)
    r = be.default_rules(['160', '200', '640', '800', '-1'])
    assert (r["base_iso"], r["max_iso"]) == ('160', '640')


def test_the_plan_is_the_longest_speed_at_or_under_the_target():
    # test 10's red through the base: 76.6 % at 1/8, ISO 250
    k = 0.766 / be.exposure_of('1/8', '250')
    p = be.plan_exposure(k, SPEEDS, ISOS, RULES)
    assert (p.shutter, p.iso) == ('1/3', '100')
    assert p.predicted == pytest.approx(k / 3) and p.predicted <= be.TARGET
    nxt = SPEEDS[SPEEDS.index('1/3') + 1]
    assert k * be.seconds(nxt) > be.TARGET                     # one longer would be over
    assert p.dim == 1.0 and not p.short


def test_iso_rises_only_past_one_second_and_at_most_two_stops():
    # within 1 s at ISO 100: ISO stays
    p = be.plan_exposure(0.83, SPEEDS, ISOS, RULES)
    assert (p.shutter, p.iso) == ('1s', '100')
    # needs ~1.7 s at ISO 100: 1 s, and ISO up just enough (160: 0.8)
    p = be.plan_exposure(0.5, SPEEDS, ISOS, RULES)
    assert (p.shutter, p.iso) == ('1s', '160') and p.predicted <= be.TARGET
    # past two stops at 1 s: ISO 400, and the speed goes longer
    p = be.plan_exposure(0.15, SPEEDS, ISOS, RULES)
    assert (p.shutter, p.iso) == ('1.3s', '400') and not p.short
    # past 30 s at ISO 400: short, and how long it would need
    p = be.plan_exposure(0.001, SPEEDS, ISOS, RULES)
    assert (p.shutter, p.iso) == ('30s', '400') and p.short
    assert p.need_s == pytest.approx(212.5)
    # ISO 100 only (another camera's rules): the speed does it all
    fixed = dict(RULES, max_iso='100')
    p = be.plan_exposure(0.5, SPEEDS, ISOS, fixed)
    assert (p.shutter, p.iso) == ('1.6s', '100')


def test_too_bright_at_the_shortest_speed_asks_to_dim():
    p = be.plan_exposure(1000.0, SPEEDS, ISOS, RULES)
    assert p.shutter == '1/250' and p.dim == pytest.approx(be.TARGET / 4.0)


def test_never_above_the_target_over_many_bases():
    for k in np.geomspace(0.005, 150, 80):
        p = be.plan_exposure(float(k), SPEEDS, ISOS, RULES)
        if p.dim < 1:
            continue
        assert p.predicted <= be.TARGET + 1e-12
        iso, t = be.iso_number(p.iso), be.seconds(p.shutter)
        assert 100 <= iso <= 400                                  # never an Auto value
        if iso > 100:
            assert t >= 1.0                                       # ISO only past 1 s
        if t > 1.0:
            assert iso == 400                                     # longer only at ISO 400


# --------------------------------------------------------------------------- #
# reading the base
# --------------------------------------------------------------------------- #
def _mosaic(h=60, w=80, left=(0.5, 0.4, 0.2), right=(0.9, 0.9, 0.9), black=1024, white=16383):
    cols = np.tile(np.array([[0, 1], [3, 2]]), (h // 2, w // 2))        # R G / G B
    frac = np.zeros((h, w))
    for side, vals in ((slice(0, w // 2), left), (slice(w // 2, w), right)):
        r, g, b = vals
        lut = np.array([r, g, b, g])
        frac[:, side] = lut[cols[:, side]]
    m = (black + frac * (white - black)).astype(np.uint16)
    return m, cols


def test_a_reading_is_each_colours_own_photosites_inside_the_patch():
    m, cols = _mosaic()
    r = be.reading_from_mosaic(m, cols, "RGBG", [1024] * 4, 16383, (0.0, 0.0, 0.5, 1.0))
    assert r.levels["R"] == pytest.approx(0.5, abs=1e-3)
    assert r.levels["G"] == pytest.approx(0.4, abs=1e-3)
    assert r.levels["B"] == pytest.approx(0.2, abs=1e-3)
    assert r.spreads["G"] == pytest.approx(0.0, abs=1e-3)
    assert r.level("r") == r.levels["R"] and r.level("w") == r.levels["R"]   # the brightest
    whole = be.reading_from_mosaic(m, cols, "RGBG", [1024] * 4, 16383)
    assert whole.spreads["B"] > 1.0                                         # two levels: uneven


def test_patches():
    x0, y0, x1, y1 = be.patch_around(0.5, 0.5, 800, 600)
    assert (x1 - x0) * 800 == pytest.approx(0.03 * 600)
    assert (y1 - y0) * 600 == pytest.approx(0.03 * 600)
    assert be.patch_around(0.0, 1.0, 800, 600)[0] == 0.0                   # kept inside
    assert be.patch_inset((0.0, 0.0, 0.1, 1.0)) == pytest.approx((0.02, 0.2, 0.08, 0.8))


# --------------------------------------------------------------------------- #
# measuring, against a fake linear camera
# --------------------------------------------------------------------------- #
def camera(k, spread=0.01, others=0.05):
    """level = k_c x t x ISO/100 x LED/255, clipped like the GFX."""
    def shoot(colour, levels, exp):
        e = be.exposure_of(exp["shutter"], exp["iso"])
        if colour == "w":
            led = max(levels.values()) / 255.0 if levels else 1.0
            lv = {ch: min(CEILING, k[ch] * e * led) for ch in "RGB"}
        else:
            led = (levels or {}).get(colour, 255) / 255.0
            main = be.CHANNEL[colour]
            lv = {ch: min(CEILING, (k[colour] if ch == main else others) * e * led)
                  for ch in "RGB"}
        return be.Reading(levels=lv, spreads={ch: spread for ch in "RGB"})
    return shoot


def run(cal, shoot):
    shots = []
    while True:
        s = cal.next_shot()
        if s is None:
            return shots
        shots.append(s)
        cal.landed(shoot(*s))
        assert len(shots) < 40


def solo(c, v=255):
    lv = {k: 0 for k in be.LIGHT_KEYS}
    lv[c] = v
    return lv


TRI = {c: solo(c) for c in be.COLOURS}


def test_trichrome_settles_in_two_shots_a_colour():
    cal = be.Calibration(be.COLOURS, TRI, SPEEDS, ISOS, '1/8')
    shots = run(cal, camera({"r": 2.5, "g": 2.1, "b": 0.83}))
    assert cal.done and cal.error is None
    assert len(shots) == 6
    res = cal.result
    assert (res["r"]["shutter"], res["g"]["shutter"], res["b"]["shutter"]) == ('1/3', '1/2.5', '1s')
    for c, k in (("r", 2.5), ("g", 2.1), ("b", 0.83)):
        assert res[c]["iso"] == '100'
        assert res[c]["base"] <= be.TARGET
        assert res[c]["levels"] == solo(c) and not res[c]["dimmed"]
        nxt = SPEEDS[SPEEDS.index(res[c]["shutter"]) + 1]
        assert k * be.seconds(nxt) > be.TARGET                  # the longest that fits
    assert [s[0] for s in shots] == ["r", "r", "g", "g", "b", "b"]
    assert all(s[2]["iso"] == '100' for s in shots)


def test_a_clipped_first_shot_backs_off_three_stops():
    cal = be.Calibration(["r"], TRI, SPEEDS, ISOS, '4s')
    shots = run(cal, camera({"r": 2.5}))
    assert [s[2]["shutter"] for s in shots] == ['4s', '1/2', '1/20', '1/3']
    assert cal.result["r"]["shutter"] == '1/3'


def test_too_dark_steps_longer_then_says_what_it_would_need():
    cal = be.Calibration(["b"], TRI, SPEEDS, ISOS, '1/8')
    shots = run(cal, camera({"b": 0.01}))                      # dim, but reachable
    assert [s[2]["shutter"] for s in shots] == ['1/8', '1s', '8s', '20s']
    assert cal.result["b"]["shutter"] == '20s' and cal.result["b"]["iso"] == '400'
    cal = be.Calibration(["b"], TRI, SPEEDS, ISOS, '1/8')
    shots = run(cal, camera({"b": 0.001}))
    assert cal.done and cal.result == {}
    assert cal.error.startswith("Blue would need about 4 min at ISO 400")
    assert "blue LED" in cal.error
    cal = be.Calibration(["b"], TRI, SPEEDS, ISOS, '1/8')
    run(cal, camera({"b": 0.0001}))
    assert cal.error.startswith("Blue is still dark at 30 s")


def test_too_bright_at_the_shortest_speed_dims_the_led_and_keeps_it():
    cal = be.Calibration(["r"], TRI, SPEEDS, ISOS, '1/8')
    shots = run(cal, camera({"r": 300.0}))
    assert cal.error is None
    res = cal.result["r"]
    assert res["dimmed"] and 0 < res["levels"]["r"] < 255 and res["base"] <= be.TARGET
    # with the LED down, still the longest speed that fits (here 1/200)
    k = 300.0 * res["levels"]["r"] / 255
    nxt = SPEEDS[SPEEDS.index(res["shutter"]) + 1]
    assert k * be.seconds(res["shutter"]) <= be.TARGET < k * be.seconds(nxt)
    assert shots[-1][1]["r"] == res["levels"]["r"]
    assert "red LED dimmed to" in cal.notes[0]
    cal = be.Calibration(["r"], TRI, SPEEDS, ISOS, '1/8', can_dim=False)
    run(cal, camera({"r": 300.0}))
    assert cal.error.startswith("Even 1/250 is too bright for red")


def test_one_exposure_reads_the_brightest_channel():
    light = {"r": 0, "g": 0, "b": 0, "w": 200, "ir": 0}
    cal = be.Calibration(["w"], {"w": light}, SPEEDS, ISOS, '1/8')
    run(cal, camera({"R": 2.4, "G": 2.1, "B": 0.8}))
    res = cal.result["w"]
    assert cal.error is None and res["levels"] == light
    led = 200 / 255
    assert 2.4 * led * be.seconds(res["shutter"]) <= be.TARGET          # red, the brightest
    nxt = SPEEDS[SPEEDS.index(res["shutter"]) + 1]
    assert 2.4 * led * be.seconds(nxt) > be.TARGET
    # no light connected: levels unknown, still measured
    cal = be.Calibration(["w"], {"w": None}, SPEEDS, ISOS, '1/8', can_dim=False)
    run(cal, camera({"R": 2.4, "G": 2.1, "B": 0.8}))
    assert cal.result["w"]["levels"] is None


def test_an_uneven_patch_stops_it():
    cal = be.Calibration(["r"], TRI, SPEEDS, ISOS, '1/8')
    run(cal, camera({"r": 2.5}, spread=0.2))
    assert cal.done and "isn't even" in cal.error
    # a dim first shot's noise is not judged
    cal = be.Calibration(["r"], TRI, SPEEDS, ISOS, '1/60')
    first = cal.next_shot()
    cal.landed(camera({"r": 2.5}, spread=0.2)(*first))          # 0.04: too dim to judge
    assert not cal.done


def test_cancel_and_failure_end_it():
    cal = be.Calibration(be.COLOURS, TRI, SPEEDS, ISOS, '1/8')
    cal.next_shot()
    cal.cancel()
    assert cal.done and cal.error == "cancelled" and cal.next_shot() is None
    cal.landed(be.Reading(levels={"R": 0.5}))                    # late: ignored
    assert cal.result == {}
    cal = be.Calibration(be.COLOURS, TRI, SPEEDS, ISOS, '1/8')
    cal.next_shot()
    cal.failed("The camera didn't fire.")
    assert cal.done and cal.error == "The camera didn't fire."


def test_no_speed_in_range():
    cal = be.Calibration(["r"], TRI, ['1/8000', '60m'], ISOS, '1/8')
    assert cal.done and "no shutter speed" in cal.error


def test_over_many_bases_it_lands_at_the_longest_speed_that_fits():
    for k in np.geomspace(0.002, 60, 50):
        cal = be.Calibration(["g"], TRI, SPEEDS, ISOS, '1/8')
        shots = run(cal, camera({"g": float(k)}))
        if cal.error:
            assert "would need" in cal.error                    # past 30 s at ISO 400
            assert k * 30 * 4 < be.TARGET * be.SHORT_BY
            continue
        res = cal.result["g"]
        assert res["base"] <= be.TARGET * be.ACCEPT_OVER
        assert len(shots) <= be.MAX_SHOTS
        p = be.plan_exposure(float(k), SPEEDS, ISOS, RULES)
        assert (res["shutter"], res["iso"]) == (p.shutter, p.iso)    # where the plan says


# --------------------------------------------------------------------------- #
# the film-base list
# --------------------------------------------------------------------------- #
def _entry(name="Portra 400"):
    cal = be.Calibration(be.COLOURS, TRI, SPEEDS, ISOS, '1/8')
    run(cal, camera({"r": 2.5, "g": 2.1, "b": 0.83}))
    return be.entry(name, cal.result, "Fujifilm GFX50S II", cal.notes, when="2026-10-30")


def test_the_list_keeps_clean_entries_with_unique_names():
    d = be.normalise(None)
    assert d == {"bases": [], "selected": -1} and be.selected(d) is None
    d = be.add(d, _entry())
    d = be.add(d, _entry())
    assert [b["name"] for b in d["bases"]] == ["Portra 400", "Portra 400 2"]
    assert d["selected"] == 1
    junk = {"bases": [{"name": ""}, {"name": "x", "colours": {"r": {"shutter": "bulb"}}},
                      "nonsense", _entry("Ektar")], "selected": 7}
    d2 = be.normalise(be.dumps(junk))
    assert [b["name"] for b in d2["bases"]] == ["Ektar"] and d2["selected"] == 0
    d = be.rename(d, 1, "portra 400")                          # taken (any case): kept
    assert d["bases"][1]["name"] == "Portra 400 2"
    d = be.rename(d, 1, "Portra 400 · Oct batch")
    assert d["bases"][1]["name"] == "Portra 400 · Oct batch"
    d = be.replace(d, 0, {"w": {"shutter": "1/4", "iso": "100", "base": 0.8, "levels": None}})
    assert set(d["bases"][0]["colours"]) == {"w"} and d["bases"][0]["name"] == "Portra 400"
    d = be.delete(d, 0)
    assert len(d["bases"]) == 1 and d["selected"] == 0
    d = be.delete(d, 0)
    assert d == {"bases": [], "selected": -1}


def test_exposures_and_summary_for_each_scan_type():
    e = be.normalise({"bases": [_entry()]})["bases"][0]
    ex = be.exposures_for(e, trichrome=True)
    assert ex["r"] == {"shutter": "1/3", "iso": "100", "levels": solo("r")}
    assert be.exposures_for(e, trichrome=False) is None
    s = be.summary(e, trichrome=True)
    assert s.startswith("R 1/3 · G 1/2.5 · B 1 s, ISO 100 (base ")
    assert s.endswith(" %)")
    assert be.summary(e, trichrome=False).startswith("Measured for trichrome")
    assert be.summary(None, True).startswith("No film base yet")
    one = be.normalise({"bases": [be.entry("BW", {"w": {"shutter": "1/2", "iso": "100",
                                                       "base": 0.84, "levels": None}})]})
    assert be.summary(one["bases"][0], trichrome=False) == "1/2, ISO 100 (base 84 %)"
    assert be.exposures_for(one["bases"][0], trichrome=False)["w"]["levels"] is None


def test_a_dimmed_led_is_named_in_the_summary():
    cal = be.Calibration(be.COLOURS, TRI, SPEEDS, ISOS, '1/8')
    run(cal, camera({"r": 2.5, "g": 2.1, "b": 300.0}))
    e = be.normalise({"bases": [be.entry("x", cal.result)]})["bases"][0]
    s = be.summary(e, trichrome=True)
    assert "; blue LED " in s


def test_default_name():
    import datetime
    assert be.default_name(datetime.date(2026, 10, 30)) == "Film base · 30 Oct"


def test_math_is_finite_everywhere():
    assert math.isfinite(be.plan_exposure(1e-9, SPEEDS, ISOS, RULES).need_s)


# --------------------------------------------------------------------------- #
# the merge undoes the exposure differences
# --------------------------------------------------------------------------- #
from core import ccr_merge  # noqa: E402


def test_exposure_scales():
    assert ccr_merge.exposure_scales([0.25, 0.25, 0.25]) == (1.0, 1.0, 1.0)
    assert ccr_merge.exposure_scales([1 / 8, 1 / 4, 1 / 2]) == pytest.approx((1.0, 0.5, 0.25))
    assert ccr_merge.exposure_scales([1 / 3, None, 1.0]) == (1.0, 1.0, 1.0)
    assert ccr_merge.exposure_scales([0.0, 1.0, 1.0]) == (1.0, 1.0, 1.0)
    assert max(ccr_merge.exposure_scales([2.0, 0.4, 1.0])) == 1.0


def _old_combine(planes, wls):
    h = min(p.shape[0] for p in planes)
    w = min(p.shape[1] for p in planes)
    out = np.empty((h, w, 3), dtype=np.uint16)
    for i, (plane, wl) in enumerate(zip(planes, wls)):
        out[..., i] = np.clip(plane[:h, :w].astype(np.float32) * (65535.0 / wl),
                              0, 65535).astype(np.uint16)
    return out


def test_equal_exposures_merge_exactly_as_before():
    rng = np.random.default_rng(3)
    planes = [rng.uniform(0, 15000, (40, 50)).astype(np.float32) for _ in range(3)]
    old = _old_combine(planes, [15359.0] * 3)
    assert np.array_equal(ccr_merge.combine_channels(*planes, [15359.0] * 3), old)
    assert np.array_equal(ccr_merge.combine_channels(*planes, [15359.0] * 3, (1.0, 1.0, 1.0)),
                          old)


def test_a_speed_per_colour_merges_like_one_speed():
    """The same scene, R at 1/8, G at 1/4, B at 1/2: after the scales it is the
    merge of the scene shot at 1/8 throughout (within rounding), and nothing is
    scaled up."""
    rng = np.random.default_rng(4)
    scene = [rng.uniform(100, 1800, (40, 50)).astype(np.float32) for _ in range(3)]
    es = [1 / 8, 1 / 4, 1 / 2]
    shot = [s * (e / es[0]) for s, e in zip(scene, es)]          # what each frame recorded
    ref = ccr_merge.combine_channels(*scene, [15359.0] * 3)
    got = ccr_merge.combine_channels(*shot, [15359.0] * 3, ccr_merge.exposure_scales(es))
    assert np.max(np.abs(got.astype(int) - ref.astype(int))) <= 1
    # without the scales blue would be 4x brighter: a colour shift
    raw = ccr_merge.combine_channels(*shot, [15359.0] * 3)
    assert raw[..., 2].mean() > 3.5 * ref[..., 2].mean()


def test_merge_raw_channels_reads_each_frames_exposure(monkeypatch, tmp_path):
    paths = []
    for n in ("r", "g", "b"):
        p = tmp_path / f"{n}.raf"
        p.write_bytes(b"x")
        paths.append(str(p))
    plane = np.full((10, 12), 1000.0, np.float32)
    monkeypatch.setattr(ccr_merge, "_decode_frame_plane",
                        lambda path, pos, preview, demosaic=False, mono=False:
                        (plane * {0: 1, 1: 2, 2: 4}[pos], 16383.0, False, (10, 12)))
    exp = {paths[0]: 0.125, paths[1]: 0.25, paths[2]: 0.5}
    monkeypatch.setattr(ccr_merge, "shot_exposure", lambda p: exp[p])
    merged, _ = ccr_merge.merge_raw_channels(paths)
    assert np.ptp(merged.reshape(-1, 3).mean(0)) <= 1          # all three equal again
    exp[paths[2]] = None                                        # unknown: untouched
    merged, _ = ccr_merge.merge_raw_channels(paths)
    assert merged[0, 0, 2] == 4 * merged[0, 0, 0]


REAL = "/mnt/user-data/uploads/Light balanced film base/DSCF1583.RAF"


@pytest.mark.skipif(not os.path.exists(REAL), reason="the user's sample RAW isn't here")
def test_shot_exposure_reads_a_raf():
    ccr_merge._EXPOSURES.clear()
    assert ccr_merge.shot_exposure(REAL) == pytest.approx(0.5 * 500 / 100)
    assert len(ccr_merge._EXPOSURES) == 1
    ccr_merge.shot_exposure(REAL)
    assert len(ccr_merge._EXPOSURES) == 1                      # cached
    assert ccr_merge.shot_exposure("/no/such/file.raf") is None
