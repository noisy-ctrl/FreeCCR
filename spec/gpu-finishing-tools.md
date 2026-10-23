# The finishing tools on the GPU

Golden Highlights, Shadow Neutralise and Glow run on the GPU (OpenCL),
straight after the main kernel and without the picture leaving the GPU. The
numpy versions stay as they are: they are the reference, the fallback, and
what runs when OpenCL is off.

## Why

Measured (2-core container, numpy; the rest of the pipeline already runs as
one OpenCL kernel):

| long side | rest of pipeline | Golden | Shadow | Glow |
|---|---|---|---|---|
| 1080 (live preview) | 52 ms | 256 ms | 139 ms | 139 ms |
| 2000 (sharp fitted view on Miles's XDR screen) | 137 ms | 1044 ms | 636 ms | 546 ms |
| 4000 | 638 ms | 4536 ms | 2799 ms | 2635 ms |

The three tools cost 5–8× the whole rest of the pipeline, so every render
with them on crosses the 70 ms draft threshold (half-resolution drafts while
dragging) and the settle/detail render after letting go pauses. The time is
spread over many single-threaded numpy passes (OKLab conversions, the guided
filters of the decision view, weights, the fit), all of which are per-pixel
maths or box sums: exactly what a GPU does well.

## Goals / non-goals

- Same picture: the GPU versions implement the same maths as the numpy
  reference, step for step (same constants, imported from the CPU modules;
  same guided filters; same fit-in-range bisection; same Glow resampling and
  Gaussian kernels). Parity is tested; quality rules (no-op at 0, untouched
  pixels exact, never clips, grain not stretched, no blocks) hold on the GPU
  path and are tested there too.
- The picture stays on the GPU from the main kernel to the end of the
  finishing chain: one upload, one download.
- Any GPU error falls back to the numpy finishing on the main kernel's result
  (the GPU writes the main buffer only in its last step, so a failure part-way
  leaves it untouched).
- `FREECCR_GPU_FINISH=0` forces the numpy finishing (to compare, or if a
  driver misbehaves).
- Non-goals: the canvas overlays (`weight_map`, only while a popup is open)
  stay numpy; the numpy path itself is not changed; no Metal port (the app's
  GPU path is OpenCL, which runs on Apple Silicon).

## Design (`core/finish_gpu.py`)

One OpenCL program, built once per context with the OKLab matrices and
constants substituted as exact float32 literals. Host code mirrors the CPU
functions and takes every constant and option from them.

Buffers (float32): the working picture `rgb` (interleaved, 0..1), a `moved`
byte per pixel, planes for the decision view (L, a, b), the matte, and
guided-filter temporaries; a memory pool avoids re-allocation per render.

Building blocks (kernels):

- `unpack01`: `rgb = clamp(v / 65535, 0, 1)` from the main kernel's buffer.
- `lab_planes`, `L_plane`: OKLab (or its L only) of `rgb` into planes.
- Box filter (normalised, `BORDER_REFLECT`, as `cv2.boxFilter` in
  `oklab._box`), separable: each work item slides a window over a run of 32
  pixels (two reads per step after the first window), so cost does not grow
  with the radius (the hold's radius reaches ~120 px at export).
- Guided filters exactly as `oklab.guided_smooth` / `guided_filter`
  (products, box means, a/b, output), and `finish_matte` (max, clamp to
  [0, max], zero below 1e-7).
- Golden: weight on the decision view (`golden.gold_weight`, with numpy's
  floor-modulo for the hue distance), matte, then per pixel: own OKLab, the
  gold along the hue, `fit_move`'s 12-step bisection in linear light,
  encode, clamp; pixels with `k <= 1e-6` untouched.
- Shadow: weight (`tone_weight × colour_gate × k`), matte, then per pixel
  (own OKLab, a/b × (1 − m), Depth's toe on its own `depth_weight`,
  `fit_move`), untouched below 1e-5 (and Depth's `s == 1`).
- Glow: L from linear RGB, guided-smoothed; sources `lin × smoothstep`
  computed on the fly inside an AREA downsample whose weight tables are
  built on the host exactly as OpenCV's `INTER_AREA` builds them (so no
  full-size source buffer); two Gaussian blurs with OpenCV's own kernels
  (`cv2.getGaussianKernel`, ksize as `GaussianBlur` picks it for float
  images, `BORDER_REFLECT` with repeated reflection for tiny images); the
  core/tail mix on the small field; the final kernel samples it bilinearly
  with OpenCV's `INTER_LINEAR` coordinates (tables from the host), applies
  the hold (guided filter at 0.5 σ) and screens in linear light; untouched
  where `max(g) < 1e-6`.
- `pack16`: writes `rgb × 65535` back into the main buffer ONLY where a
  stage moved the pixel, so every untouched pixel is bit-for-bit the main
  kernel's output.

## Integration

`adjust_image_opencl`, inside the GPU lock, right after the main kernel: if
any of the three is on and GPU finishing is available, run them on
`img_buf`, then `get()` once. Otherwise / on failure, the existing numpy
calls. `adjust_image` (numpy) is untouched.

## Tests (`tests/test_finish_gpu.py`)

Run against the OpenCL device the tests already use (pocl on CPU in CI). Per
tool and all three chained, on a real-looking frame and synthetic ones, at
preview size and at 2–3× (radii scale), with default and non-default options
(Golden hue/clean/neutral/smooth; Shadow range/reach/depth/smooth; Glow
size/start incl. f = 1 and a tiny strip that needs repeated reflection):

- GPU vs numpy finishing: max ≤ 3 codes (16-bit), 99.9 % ≤ 1 code.
- 0 is a no-op on the GPU path; untouched pixels exactly equal the main
  kernel's output.
- The quality tests' GPU twins: in range, never darker (Glow), grain not
  stretched, ramp monotone.
- A forced GPU error falls back to the numpy result.
- `FREECCR_GPU_FINISH=0` takes the numpy path.
- Building blocks against their references: box filter vs `cv2.boxFilter`
  at radii 1–150, guided filters, area resize vs `cv2.resize(INTER_AREA)` at
  integer and fractional ratios, bilinear vs `INTER_LINEAR`, Gaussian vs
  `cv2.GaussianBlur`.

`tools/bench_render.py` gains the finishing tools, numpy vs GPU, at 1080 /
2000 / 4000, so the speed can be measured on the Mac itself.

## Refinement (resolved before implementing)

- Where the CPU code uses Python's `round()` for sizes and radii (banker's
  rounding), the host code calls the same helpers (`scaled_radius`,
  `matte_radius`, `round(W / f)`), never its own rounding.
- `fit_move`'s `outside` thresholds (−1e-6, 1 + 1e-5) and 12 steps are
  copied, and the bisection runs per pixel: same fraction to 1/4096.
- `cv2.resize(INTER_AREA)` takes a fast path at exact integer ratios (block
  mean); the table method gives the same weights there, so one kernel covers
  both (differences ~1e-7).
- Glow's guards (`guided_filter` returns its input when the picture is
  smaller than the window; `f > 1` decides the resize) are evaluated on the
  host with the same expressions.
- Apple's OpenCL has no doubles: everything is float32, tables are built in
  float64 on the host and passed as float32, as OpenCV casts them.

## Measured (implementation)

- Parity, GPU vs numpy, 16-bit codes (pocl, `tests/test_finish_gpu.py`):
  Golden max 0.42, Shadow 0.09, Glow 0.49 at 1080 px on a real lab scan;
  every option set and size in the tests ≤ 3 (the bar), 99.9 % ≤ 1. Box
  filter vs `cv2.boxFilter` < 2e-6 at radii 1–150, guided filters < 2e-5,
  Gaussian vs `cv2.GaussianBlur` < 2e-6 (incl. a strip thin enough to
  reflect twice), the INTER_AREA / INTER_LINEAR tables reproduce cv2 to
  < 2e-6.
- Untouched pixels are bit-for-bit the main kernel's output (written back
  from the `moved` mask only).
- **CPU OpenCL is slower than numpy.** On pocl (2 cores here), 1080 px, all
  three: 1047 ms vs numpy 371 ms; per kernel the time is the transcendentals
  (`lab_planes`/`glow_L`: cbrt and pow per channel, ~220 ms each), where
  numpy's vectorised maths wins on a CPU. So the default ("auto") runs it on
  a GPU device only; `FREECCR_GPU_FINISH=1` forces it (tests). Two
  reductions came from that profile and help a GPU too: the apply kernels
  read the OKLab planes already computed for the decision view instead of
  converting again (Golden apply 352 → 126 ms on pocl), and Glow keeps the
  linear RGB it computes once (glow_apply 430 → 140 ms).
- On a real GPU the work is ~60 kernel launches per tool and some tens of
  full-size passes; it could not be timed here (no GPU in the container):
  `tools/bench_finishing.py` measures it on the machine that has one.


## Revision (2026-10-23): Shadow Lift

Shadow Lift (spec/shadow-lift.md) joins the chain between Shadow Neutralise
and Glow as one per-pixel kernel (`lift_apply`: the OKLab tone curve and
`fit_move`'s 12-step bisection). Measured against numpy: ≤ 0.14 codes on a
real picture; on uniformly random colours ≤ 33 codes on 0.001 % of pixels,
all already on a face of the gamut, where float32 rounding picks a different
bisection step (0.13 of an 8-bit level).
