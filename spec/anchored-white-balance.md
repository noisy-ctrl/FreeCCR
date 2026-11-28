# Anchored Temperature and Tint

The user, 2026-11-28, comparing white balance with the reference converter: "White balance seems to work much nicer in [it] too. It feels more corrective than additive." Then: "Please go ahead with these and wb changes."

## 1. What was measured

The reference converter's export of the street frame was compared at three settings: Temperature 0, Temperature 13.24, and Temperature 11.47 with Tint −2.65. For each channel, the output was plotted against the scan's density and fitted four ways:

- a density offset before everything;
- a multiply in linear light;
- a multiply on density;
- a power (gamma) on the output.

The output gamma fits within about 2 levels, and the others don't. Its white balance is a per-channel gamma with black and white pinned. Black stays black and white stays white, and the move is largest in the middle tones. That is why it reads as corrective. A cast in the mids goes, and the ends don't take on a colour.

Per unit of the reference converter's slider, as ln γ per channel (R, G, B):

| | R | G | B |
| --- | --- | --- | --- |
| Temperature +1 | −0.001526 | +0.000377 | +0.013394 |
| Tint +1 | −0.008862 | +0.011186 | −0.001596 |

Temperature is mostly blue: + darkens blue in the mids, which warms. Tint is mostly green against red: + darkens green, which goes magenta. These are the same directions as FreeCCR's sliders.

FreeCCR's Temperature, by contrast, multiplies each channel flat, after Master Gain. On a negative's density base that is neither: highlights move ±18 levels and shadows ±3–6, so it adds a colour to the brightest parts rather than correcting the middle.

## 2. Goals

- On a frame converted with **levels from the picture** whose decode is the print curve (`spec/picture-levels-conversion.md`), Temperature and Tint become the reference converter's anchored gammas, unit for unit. The same numbers give the same correction.
- Every other frame keeps today's flat multiply byte for byte:
  - film base conversions;
  - reference-frame conversions;
  - Positive mode;
  - a picture-levels frame with Cineon or a Film Look taking the decode.
- The WB Picker, AWB and the Skin pull keep working. They are closed loops on the real render and need nothing but a monotone knob.
- The AWB button's roll guard keeps its meaning. Its caps are in density; the anchored pair is put into the same units.

## 3. Non-goals

- Changing the slider ranges or labels. Both stay ±100, whole steps; one step is one of the reference converter's units.
- The roll guard as such (whether picture-levels frames should be checked against the roll): that is tomorrow's decision.

## 4. The maths (core/anchored_wb.py)

```
k_c = T·A_c + N·B_c                 A, B the table above; T, N the sliders
γ_c = exp(k_c)
g_c(v) = ((v + ε)^γ − ε^γ) / ((1 + ε)^γ − ε^γ)        v ≥ 0,  ε = 0.01
g_c(v) = g_c'(0) · v                                   v < 0
```

- **Pinned.** `g(0) = 0` and `g(1) = 1` exactly.
- **Bounded slope.** A pure power has an infinite slope at black for γ < 1 (it would multiply deep-shadow grain without bound) and a zero one for γ > 1 (it would crush). The ε offset makes the slope at black `γ·ε^(γ−1)` over the normaliser. That is 2.0 at γ 0.8, 0.48 at γ 1.2 and 0.39 at γ 1.25: what the pure power reaches at 1 % above black, held there instead of running off. From 0.05 up (13/255) the shape is within 1.0 level of the pure gamma for γ 1.0–1.25, 1.3 at 1.6, and 2.6 at γ 0.8.
- **Smooth and monotone.** C∞ on v ≥ 0, C¹ at 0, strictly increasing everywhere.
- **The margin and headroom carry on.**
  - Below black (v < 0, the shadow margin) the stage carries on as a straight line, so the margin stays recoverable and the window clamp floors it as before.
  - Above white (v > 1, the headroom) the same formula carries on, smooth and increasing, so the White Point recovery still works.
- **No-op at zero.** γ = 1 is the identity, and the stage is skipped, so 0/0 is byte for byte.

## 5. Where it runs

It runs in `_apply_working_space_recovery`, in White Balance's own slot: after Master Gain and before the White Point recovery. The flat multiply is replaced only when the decode is a `PrintCurve`, which carries `anchored_wb = True`. It is read at the top of the function, before the decode argument is consumed. The windowed pre-stage is shared by the CPU path and the OpenCL path, so they agree with no kernel change. `tint_balance_factor` isn't used here.

## 6. The AWB roll guard

`awb.guard_wb(full, roll, anchored=False)`. With `anchored`, the pair is put into density as the colour difference it makes at mid grey (display 0.5), in log10:
- temperature: `log10(0.5)·(γ_R − γ_B)`;
- tint: `log10(0.5)·(γ_G − (γ_R + γ_B)/2)`.

Each with the other knob at 0. These are the same quantities the flat formulas give for their own multiply.

The slider is found back from that density by interpolating a table over −100..100, which is monotone on both. The caps (0.06 temperature, 0.15 tint, warm/cool) are unchanged.

`CCRBackend.awb_for` passes `anchored` for the frame, from the decode it actually renders with.

## 7. Tests (tests/test_anchored_wb.py)

**The gammas**
- The gammas at (13.24, 0) and (11.47, −2.65) are the measured ones, to 1e-4.

**The curve's shape**
- g(0) = 0 and g(1) = 1.
- It's monotone on [−0.5, 2].
- Its slope at black is finite: γ 0.6 gives about 4.0, while a pure power's is infinite.
- It's C¹ at 0.
- For γ 1.0–1.25 it's within 1.1 levels of v^γ from 0.05 up.
- (0, 0) is a no-op byte for byte.

**In the render**
- A picture-levels frame with Temperature +20:
  - its black and white patches don't move (≤ 1 level);
  - its mid patch's blue falls as `g_B`;
  - a film base frame with the same Temperature renders exactly as before.
- A picture-levels frame with Cineon ticked takes the flat multiply.
- CPU and OpenCL agree.

**The solves and the guard**
- `solve_neutral_wb` on a picture-levels frame with a cast lands the patch grey.
- `guard_wb(anchored=True)` at the caps returns the slider whose density is the cap.
- Its density is monotone in the slider.

## As built (2026-11-28)

- **Where the code is.** `core/anchored_wb.py` holds `gammas`, `curve`, `apply`, the guard units and their inverses. `PrintCurve.anchored_wb = True` switches the render: `_apply_working_space_recovery` reads it before the decode. `picture_levels.frame_print_curve(img)` mirrors `apply_adjustments`' choice of decode, and `CCRBackend.anchored_wb(img)` uses it for `awb_for` → `guard_wb(..., anchored=)`.
- **Measured on the street frame.** Each change from Temperature / Tint 0 is compared at the 5–95th percentiles of each channel inside the picture: FreeCCR (levels from the picture) against the reference converter's exports.

  | Setting | Agreement |
  | --- | --- |
  | Temperature 13.24 | 0.76 levels RMS. Blue −15.7 vs −16 at the median, −0.9 vs −1 at the 95th; red +1.7 vs +2. |
  | Temperature 11.47, Tint −2.65 | 0.88 levels RMS. Blue −13.9 vs −14 and green +2.2 vs +3 at the median. |

- **Black.** The picture's black renders at F(0) = 1.8/255, not 0, so Temperature +20 takes a black patch's blue to 0.6/255: 1.2 levels, the only move at the ends.
