"""The finishing tools - Golden Highlights, Shadow Neutralise and Glow - on the
GPU (OpenCL), straight after the main kernel, without the picture leaving the
GPU (spec/gpu-finishing-tools.md).

The numpy modules (core.golden, core.shadow_neutral, core.glow, core.oklab)
are the reference: every constant and option is read from them, and each step
here is the same maths in the same order - the same OKLab, the same guided
filters, the same fit-in-range bisection, and for Glow the same resampling
and Gaussian kernels OpenCV uses (tables built on the host the way OpenCV
builds them). Tests hold the two to a code or so of 16-bit.

It runs by default on a GPU device only (`enabled_for`); `FREECCR_GPU_FINISH=0`
turns it off, `=1` forces it on any OpenCL device."""
import math
import os
from functools import lru_cache

import cv2
import numpy as np

try:
    import pyopencl as cl
    import pyopencl.array as cla
    import pyopencl.tools as cltools
except Exception:          # pragma: no cover - no OpenCL here
    cl = cla = cltools = None

from core import oklab as ok
from core import golden as gd
from core import shadow_neutral as sn
from core import glow as gw

RUN = 32                    # pixels per work item in the sliding box filter
BIG_RENDER = 8_000_000      # pixels: above this the memory pool is emptied after


def enabled_for(device) -> bool:
    """FREECCR_GPU_FINISH: "0"/off never, "1"/on always (tests use this on
    a CPU OpenCL device), unset/"auto" only on a GPU: an OpenCL CPU device
    runs these many small passes slower than numpy does."""
    v = os.environ.get("FREECCR_GPU_FINISH", "auto").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on", "force"):
        return cl is not None
    if cl is None or device is None:
        return False
    try:
        t = int(device.type)
        return bool(t & (int(cl.device_type.GPU) | int(cl.device_type.ACCELERATOR)))
    except Exception:
        return False


def _f(x) -> str:
    """An exact float32 literal for OpenCL C."""
    return repr(float(np.float32(x))) + "f"


def _mat(name, m):
    m = np.asarray(m, np.float32)
    rows = []
    for i in range(3):
        rows.append(f"{_f(m[i, 0])} * v.x + {_f(m[i, 1])} * v.y + {_f(m[i, 2])} * v.z")
    return (f"inline float3 {name}(float3 v) {{\n"
            f"    return (float3)({rows[0]},\n                    {rows[1]},\n"
            f"                    {rows[2]});\n}}\n")


def _source() -> str:
    m2_0 = np.asarray(ok._M2, np.float32)[0]
    return (
        _mat("m1", ok._M1) + _mat("m2", ok._M2) + _mat("m1i", ok._M1i) + _mat("m2i", ok._M2i)
        + f"""
#define RUN {RUN}
#define FIT_STEPS {ok.FIT_STEPS}
#define OUT_LO {_f(-1e-6)}
#define OUT_HI {_f(1.0 + 1e-5)}

inline float srgb_lin(float c) {{
    c = fmax(c, 0.0f);
    return c <= {_f(0.04045)} ? c / {_f(12.92)}
                             : powr((c + {_f(0.055)}) / {_f(1.055)}, {_f(2.4)});
}}

inline float lin_srgb(float l) {{
    l = fmax(l, 0.0f);
    return l <= {_f(0.0031308)} ? l * {_f(12.92)}
                               : {_f(1.055)} * powr(l, {_f(1 / 2.4)}) - {_f(0.055)};
}}

inline float3 to_lin3(float3 c) {{
    return (float3)(srgb_lin(c.x), srgb_lin(c.y), srgb_lin(c.z));
}}

inline float3 rgb_lab(float3 c) {{
    float3 l = m1(to_lin3(c));
    return m2((float3)(cbrt(l.x), cbrt(l.y), cbrt(l.z)));
}}

inline float lin_L(float3 lin) {{
    float3 l = m1(lin);
    return {_f(m2_0[0])} * cbrt(l.x) + {_f(m2_0[1])} * cbrt(l.y) + {_f(m2_0[2])} * cbrt(l.z);
}}

inline float3 lab_lin(float3 lab) {{
    float3 l = m2i(lab);
    return m1i(l * l * l);
}}

inline int outside3(float3 lin) {{
    return lin.x < OUT_LO || lin.y < OUT_LO || lin.z < OUT_LO ||
           lin.x > OUT_HI || lin.y > OUT_HI || lin.z > OUT_HI;
}}

/* core.oklab.fit_move: lab + d, or the largest fraction of d (12-step
   bisection in LINEAR light) that stays in 0..1; then encoded. */
inline float3 fit_move(float3 lab, float3 d) {{
    float3 lin = lab_lin(lab + d);
    if (outside3(lin)) {{
        float lo = 0.0f, hi = 1.0f;
        for (int i = 0; i < FIT_STEPS; i++) {{
            float mid = 0.5f * (lo + hi);
            if (!outside3(lab_lin(lab + mid * d))) lo = mid; else hi = mid;
        }}
        lin = lab_lin(lab + lo * d);
    }}
    return (float3)(lin_srgb(lin.x), lin_srgb(lin.y), lin_srgb(lin.z));
}}

inline float sstep(float x) {{
    x = clamp(x, 0.0f, 1.0f);
    return x * x * (3.0f - 2.0f * x);
}}

/* cv2.BORDER_REFLECT (fedcba|abcdefgh|hgfedcb), repeated for tiny sizes. */
inline int refl(int p, int n) {{
    if ((uint)p < (uint)n) return p;
    if (n == 1) return 0;
    while (p < 0 || p >= n) {{
        if (p < 0) p = -p - 1; else p = 2 * n - p - 1;
    }}
    return p;
}}

/* numpy's floor modulo (Python %) for floats */
inline float pymod(float x, float m) {{
    float r = fmod(x, m);
    if (r != 0.0f && ((r < 0.0f) != (m < 0.0f))) r += m;
    return r;
}}

__kernel void zero_u8(__global uchar* p, int n) {{
    int i = get_global_id(0);
    if (i < n) p[i] = 0;
}}

/* max over a chunk per work item (the host takes the max of the few) */
__kernel void chunk_max(__global const float* x, __global float* out, int n,
                        int chunk) {{
    int g = get_global_id(0);
    int s = g * chunk;
    if (s >= n) return;
    float m = x[s];
    int e = min(s + chunk, n);
    for (int i = s + 1; i < e; i++) m = fmax(m, x[i]);
    out[g] = m;
}}

__kernel void unpack01(__global const float* src, __global float* rgb, int n3) {{
    int i = get_global_id(0);
    if (i >= n3) return;
    rgb[i] = clamp(src[i] / {_f(65535.0)}, 0.0f, 1.0f);
}}

__kernel void pack16(__global const float* rgb, __global const uchar* moved,
                     __global float* dst, int n) {{
    int i = get_global_id(0);
    if (i >= n || !moved[i]) return;
    dst[3 * i] = rgb[3 * i] * {_f(65535.0)};
    dst[3 * i + 1] = rgb[3 * i + 1] * {_f(65535.0)};
    dst[3 * i + 2] = rgb[3 * i + 2] * {_f(65535.0)};
}}

__kernel void lab_planes(__global const float* rgb, __global float* L,
                         __global float* A, __global float* B, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float3 lab = rgb_lab(vload3(i, rgb));
    L[i] = lab.x; A[i] = lab.y; B[i] = lab.z;
}}

__kernel void glow_L(__global const float* rgb, __global float* L,
                     __global float* lin, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float3 l = to_lin3(vload3(i, rgb));
    vstore3(l, i, lin);
    L[i] = lin_L(l);
}}

/* Normalised box filter, horizontal pass: each work item slides the window
   over a run of RUN pixels (cost independent of the radius). */
__kernel void box_h(__global const float* src, __global float* dst,
                    int W, int H, int r) {{
    int x0 = get_global_id(0) * RUN;
    int y = get_global_id(1);
    if (y >= H || x0 >= W) return;
    __global const float* row = src + (size_t)y * W;
    __global float* out = dst + (size_t)y * W;
    float inv = 1.0f / (float)(2 * r + 1);
    float s = 0.0f;
    for (int i = -r; i <= r; i++) s += row[refl(x0 + i, W)];
    int x1 = min(x0 + RUN, W);
    for (int x = x0; x < x1; x++) {{
        out[x] = s * inv;
        s += row[refl(x + r + 1, W)] - row[refl(x - r, W)];
    }}
}}

__kernel void box_v(__global const float* src, __global float* dst,
                    int W, int H, int r) {{
    int x = get_global_id(0);
    int y0 = get_global_id(1) * RUN;
    if (x >= W || y0 >= H) return;
    float inv = 1.0f / (float)(2 * r + 1);
    float s = 0.0f;
    for (int i = -r; i <= r; i++) s += src[(size_t)refl(y0 + i, H) * W + x];
    int y1 = min(y0 + RUN, H);
    for (int y = y0; y < y1; y++) {{
        dst[(size_t)y * W + x] = s * inv;
        s += src[(size_t)refl(y + r + 1, H) * W + x] - src[(size_t)refl(y - r, H) * W + x];
    }}
}}

__kernel void mul(__global const float* a, __global const float* b,
                  __global float* out, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    out[i] = a[i] * b[i];
}}

/* guided_smooth: var = box(x^2) - m^2, a = var / (var + eps), b = m - a m */
__kernel void gf_self_ab(__global const float* m, __global const float* mm,
                         __global float* a, __global float* b, float eps, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float mi = m[i];
    float var = mm[i] - mi * mi;
    float ai = var / (var + eps);
    a[i] = ai;
    b[i] = mi - ai * mi;
}}

/* guided_filter: a = (box(Ip) - mI mp) / (box(II) - mI^2 + eps), b = mp - a mI */
__kernel void gf_ab(__global const float* mI, __global const float* mp,
                    __global const float* mIp, __global const float* mII,
                    __global float* a, __global float* b, float eps, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float I = mI[i], p = mp[i];
    float ai = (mIp[i] - I * p) / (mII[i] - I * I + eps);
    a[i] = ai;
    b[i] = p - ai * I;
}}

__kernel void gf_out(__global const float* ma, __global const float* mb,
                     __global const float* guide, __global float* out, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    out[i] = ma[i] * guide[i] + mb[i];
}}

/* finish_matte's tail: clamp to [0, top], zero below 1e-7 */
__kernel void matte_tail(__global float* q, float top, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float v = clamp(q[i], 0.0f, top);
    q[i] = v < {_f(1e-7)} ? 0.0f : v;
}}

/* core.golden.gold_weight x scale, on the decision view */
__kernel void golden_weight(__global const float* Ls, __global const float* As,
                            __global const float* Bs, __global float* k,
                            float start, float rise, float clean, float top,
                            float one_m_top, float hue_c, float width,
                            float chroma0, float neutral, float nb, float nspan,
                            float gold, float scale, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float L = Ls[i], a = As[i], b = Bs[i];
    float w = sstep((L - start) / rise);
    w = w * (1.0f - clean * sstep((L - top) / one_m_top));
    float C = hypot(a, b);
    float h = atan2(b, a) * {_f(180.0 / math.pi)};
    float d = fabs(pymod(h - hue_c + 180.0f, 360.0f) - 180.0f);
    float g_hue = d < width ? 0.5f + 0.5f * cos({_f(math.pi)} * d / width) : 0.0f;
    float q = C / chroma0;
    float g_chroma = exp(-(q * q));
    float t = sstep((C - nb) / nspan);
    float col = (1.0f - t) * neutral + t * g_hue * g_chroma;
    k[i] = gold * w * col * scale;
}}

__kernel void golden_apply(__global float* rgb, __global const float* k,
                           __global const float* L, __global const float* A,
                           __global const float* B,
                           __global uchar* moved, float ch, float sh, float keps,
                           int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float ki = k[i];
    if (!(ki > keps)) return;
    float3 lab = (float3)(L[i], A[i], B[i]);
    float3 d = (float3)(0.0f, ki * ch, ki * sh);
    vstore3(clamp(fit_move(lab, d), 0.0f, 1.0f), i, rgb);
    moved[i] = 1;
}}

/* core.shadow_neutral: k . tone_weight . colour_gate on the decision view */
__kernel void shadow_weight(__global const float* Ls, __global const float* As,
                            __global const float* Bs, __global float* m,
                            float kk, float t_lo, float t_span, float c0, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float t = 1.0f - sstep((Ls[i] - t_lo) / t_span);
    float q = hypot(As[i], Bs[i]) / c0;
    m[i] = kk * t * exp(-(q * q));
}}

__kernel void shadow_apply(__global float* rgb, __global const float* m,
                           __global const float* L, __global const float* A,
                           __global const float* B,
                           __global uchar* moved, int depth_on, float kd,
                           float end, float meps, int n) {{
    int i = get_global_id(0);
    if (i >= n) return;
    float mi = m[i];
    if (!depth_on && !(mi > meps)) return;
    float3 lab = (float3)(L[i], A[i], B[i]);
    float f = 1.0f - mi;
    float s = 1.0f;
    int mv;
    if (depth_on) {{
        s = 1.0f - kd * (1.0f - sstep(lab.x / end));
        f = f * s;
        mv = (mi > meps) || (s < 1.0f);
    }} else {{
        mv = mi > meps;
    }}
    if (!mv) return;
    float3 nw = (float3)(depth_on ? lab.x * s : lab.x, lab.y * f, lab.z * f);
    vstore3(clamp(fit_move(lab, nw - lab), 0.0f, 1.0f), i, rgb);
    moved[i] = 1;
}}

/* Glow: the AREA downsample of the sources lin x smoothstep(L), the sources
   computed on the fly. Horizontal pass: every source row, Ws outputs. */
__kernel void glow_area_h(__global const float* lin_rgb, __global const float* Ls,
                          __global float* hbuf, __global const int* xoff,
                          __global const int* xsrc, __global const float* xw,
                          float s_lo, float s_span, int W, int H, int Ws) {{
    int dx = get_global_id(0);
    int y = get_global_id(1);
    if (dx >= Ws || y >= H) return;
    float3 acc = (float3)(0.0f);
    for (int k = xoff[dx]; k < xoff[dx + 1]; k++) {{
        size_t p = (size_t)y * W + xsrc[k];
        float src = sstep((Ls[p] - s_lo) / s_span);
        acc += (vload3(p, lin_rgb) * src) * xw[k];
    }}
    vstore3(acc, (size_t)y * Ws + dx, hbuf);
}}

__kernel void glow_area_v(__global const float* hbuf, __global float* small,
                          __global const int* yoff, __global const int* ysrc,
                          __global const float* yw, int Ws, int Hs) {{
    int dx = get_global_id(0);
    int dy = get_global_id(1);
    if (dx >= Ws || dy >= Hs) return;
    float3 acc = (float3)(0.0f);
    for (int k = yoff[dy]; k < yoff[dy + 1]; k++)
        acc += yw[k] * vload3((size_t)ysrc[k] * Ws + dx, hbuf);
    vstore3(acc, (size_t)dy * Ws + dx, small);
}}

/* Separable Gaussian with OpenCV's kernel, BORDER_REFLECT, 3 channels. */
__kernel void gauss_h(__global const float* src, __global float* dst,
                      __global const float* kern, int ks, int Wd, int Hd) {{
    int x = get_global_id(0);
    int y = get_global_id(1);
    if (x >= Wd || y >= Hd) return;
    int anchor = ks / 2;
    float3 acc = (float3)(0.0f);
    for (int k = 0; k < ks; k++)
        acc += kern[k] * vload3((size_t)y * Wd + refl(x + k - anchor, Wd), src);
    vstore3(acc, (size_t)y * Wd + x, dst);
}}

__kernel void gauss_v(__global const float* src, __global float* dst,
                      __global const float* kern, int ks, int Wd, int Hd) {{
    int x = get_global_id(0);
    int y = get_global_id(1);
    if (x >= Wd || y >= Hd) return;
    int anchor = ks / 2;
    float3 acc = (float3)(0.0f);
    for (int k = 0; k < ks; k++)
        acc += kern[k] * vload3((size_t)refl(y + k - anchor, Hd) * Wd + x, src);
    vstore3(acc, (size_t)y * Wd + x, dst);
}}

__kernel void glow_mix(__global const float* core, __global const float* tail,
                       __global float* out, float cw, float tw, int n3) {{
    int i = get_global_id(0);
    if (i >= n3) return;
    out[i] = cw * core[i] + tw * tail[i];
}}

/* The field at full size (bilinear, OpenCV's INTER_LINEAR tables, or the
   field itself when it was not reduced), the hold, then the screen in linear
   light. */
__kernel void glow_apply(__global float* rgb, __global const float* lin_rgb,
                         __global uchar* moved,
                         __global const float* Lh, __global const float* field,
                         int reduced, __global const int* xs0, __global const int* xs1,
                         __global const float* xa, __global const int* ys0,
                         __global const int* ys1, __global const float* yb,
                         int W, int H, int Ws, float kamt, float hold_c,
                         float h_lo, float h_span, float geps) {{
    int x = get_global_id(0);
    int y = get_global_id(1);
    if (x >= W || y >= H) return;
    size_t i = (size_t)y * W + x;
    float3 g;
    if (reduced) {{
        float a1 = xa[x], a0 = 1.0f - a1, b1 = yb[y], b0 = 1.0f - b1;
        float3 r0 = vload3((size_t)ys0[y] * Ws + xs0[x], field) * a0
                  + vload3((size_t)ys0[y] * Ws + xs1[x], field) * a1;
        float3 r1 = vload3((size_t)ys1[y] * Ws + xs0[x], field) * a0
                  + vload3((size_t)ys1[y] * Ws + xs1[x], field) * a1;
        g = r0 * b0 + r1 * b1;
    }} else {{
        g = vload3(i, field);
    }}
    g = fmax(g, (float3)(0.0f));
    float hold = 1.0f - hold_c * sstep((Lh[i] - h_lo) / h_span);
    g = g * (kamt * hold);
    g = clamp(g, 0.0f, 1.0f);
    if (fmax(fmax(g.x, g.y), g.z) < geps) return;
    float3 lin = vload3(i, lin_rgb);
    float3 lit = (1.0f - lin) * g + lin;
    float3 o = (float3)(lin_srgb(lit.x), lin_srgb(lit.y), lin_srgb(lit.z));
    vstore3(clamp(o, 0.0f, 1.0f), i, rgb);
    moved[i] = 1;
}}
""")


# --------------------------------------------------------------------------- #
# Host-side tables, built the way OpenCV builds them
# --------------------------------------------------------------------------- #

def _frozen(*arrays):
    for a in arrays:
        a.setflags(write=False)
    return arrays


@lru_cache(maxsize=64)
def area_table(ssize, dsize):
    """cv2.resize(INTER_AREA) weights along one axis (computeResizeAreaTab):
    (offsets[dsize+1], source index per entry, weight per entry)."""
    scale = 1.0 / (float(dsize) / float(ssize))
    off, src, wt = [0], [], []
    for dx in range(dsize):
        fsx1 = dx * scale
        fsx2 = fsx1 + scale
        cell = min(scale, ssize - fsx1)
        sx1, sx2 = math.ceil(fsx1), math.floor(fsx2)
        sx2 = min(sx2, ssize - 1)
        sx1 = min(sx1, sx2)
        if sx1 - fsx1 > 1e-3:
            src.append(sx1 - 1)
            wt.append((sx1 - fsx1) / cell)
        for sx in range(sx1, sx2):
            src.append(sx)
            wt.append(1.0 / cell)
        if fsx2 - sx2 > 1e-3:
            src.append(sx2)
            wt.append(min(min(fsx2 - sx2, 1.0), cell) / cell)
        off.append(len(src))
    return _frozen(np.asarray(off, np.int32), np.asarray(src, np.int32),
                   np.asarray(wt, np.float64).astype(np.float32))


def identity_table(n):
    return (np.arange(n + 1, dtype=np.int32), np.arange(n, dtype=np.int32),
            np.ones(n, np.float32))


@lru_cache(maxsize=64)
def linear_table_x(ssize, dsize):
    """cv2.resize(INTER_LINEAR) along x: (s0, s1, alpha1) per output."""
    scale = 1.0 / (float(dsize) / float(ssize))
    s0 = np.empty(dsize, np.int32)
    a1 = np.empty(dsize, np.float32)
    for d in range(dsize):
        fx = np.float32((d + 0.5) * scale - 0.5)
        sx = int(math.floor(fx))
        fx = np.float32(fx - np.float32(sx))
        if sx < 0:
            fx, sx = np.float32(0.0), 0
        if sx >= ssize - 1:
            fx, sx = np.float32(0.0), ssize - 1
        s0[d], a1[d] = sx, fx
    s1 = np.minimum(s0 + 1, ssize - 1).astype(np.int32)
    return _frozen(s0, s1, a1)


@lru_cache(maxsize=64)
def linear_table_y(ssize, dsize):
    """cv2.resize(INTER_LINEAR) along y: rows clamped, weight NOT reset at
    the edges (OpenCV's vertical pass)."""
    scale = 1.0 / (float(dsize) / float(ssize))
    s0 = np.empty(dsize, np.int32)
    s1 = np.empty(dsize, np.int32)
    b1 = np.empty(dsize, np.float32)
    for d in range(dsize):
        fy = np.float32((d + 0.5) * scale - 0.5)
        sy = int(math.floor(fy))
        b1[d] = np.float32(fy - np.float32(sy))
        s0[d] = min(max(sy, 0), ssize - 1)
        s1[d] = min(max(sy + 1, 0), ssize - 1)
    return _frozen(s0, s1, b1)


def gaussian_kernel(sigma):
    """OpenCV's kernel for GaussianBlur(ksize=(0, 0), sigma) on float images."""
    ks = int(np.rint(sigma * 4 * 2 + 1)) | 1
    return cv2.getGaussianKernel(ks, sigma, cv2.CV_32F).ravel().astype(np.float32)


# --------------------------------------------------------------------------- #
# The GPU side
# --------------------------------------------------------------------------- #

_KERNELS = ("zero_u8", "chunk_max", "unpack01", "pack16", "lab_planes", "glow_L",
            "box_h", "box_v", "mul", "gf_self_ab", "gf_ab", "gf_out", "matte_tail", "golden_weight",
            "golden_apply", "shadow_weight", "shadow_apply", "glow_area_h",
            "glow_area_v", "gauss_h", "gauss_v", "glow_mix", "glow_apply")

_last = None


def for_queue(ctx, queue):
    """The GPU finisher for an OpenCL context and queue, built once and kept
    for the latest pair (a new context - cleanup_opencl, another device -
    builds a new one and lets the old one go)."""
    global _last
    if _last is None or _last.ctx is not ctx or _last.queue is not queue:
        _last = FinishGPU(ctx, queue)
    return _last


def _i(x):
    return np.int32(x)


def _fl(x):
    return np.float32(x)


class FinishGPU:
    def __init__(self, ctx, queue):
        self.ctx, self.queue = ctx, queue
        prg = cl.Program(ctx, _source()).build()
        self.k = {name: getattr(prg, name) for name in _KERNELS}
        self.pool = cltools.MemoryPool(cltools.ImmediateAllocator(queue))

    # -- buffers -----------------------------------------------------------
    def empty(self, n, dtype=np.float32):
        return cla.empty(self.queue, int(n), dtype, allocator=self.pool)

    def upload(self, a):
        return cla.to_device(self.queue, np.ascontiguousarray(a), allocator=self.pool)

    def _run1(self, name, n, *args):
        self.k[name](self.queue, (int(n),), None, *args)

    def max(self, x, n, chunk=4096):
        """max(x) (pyopencl's own reduction needs Mako; this doesn't)."""
        parts = (n + chunk - 1) // chunk
        out = self.empty(parts)
        self._run1("chunk_max", parts, x.data, out.data, _i(n), _i(chunk))
        return float(out.get().max())

    # -- building blocks ---------------------------------------------------
    def box(self, src, dst, tmp, W, H, r):
        q = self.queue
        self.k["box_h"](q, ((W + RUN - 1) // RUN, H), None, src.data, tmp.data,
                        _i(W), _i(H), _i(r))
        self.k["box_v"](q, (W, (H + RUN - 1) // RUN), None, tmp.data, dst.data,
                        _i(W), _i(H), _i(r))

    def guided_smooth(self, x, W, H, r, eps):
        """oklab.guided_smooth on a W x H plane (a new array, or `x` itself
        when the window doesn't fit, as there)."""
        if r < 1 or min(H, W) < 2 * r + 1:
            return x
        n = W * H
        t, m, mm, a, b = (self.empty(n) for _ in range(5))
        self._run1("mul", n, x.data, x.data, t.data, _i(n))
        self.box(x, m, a, W, H, r)
        self.box(t, mm, a, W, H, r)
        self._run1("gf_self_ab", n, m.data, mm.data, a.data, b.data, _fl(eps), _i(n))
        self.box(a, m, t, W, H, r)
        self.box(b, mm, t, W, H, r)
        out = self.empty(n)
        self._run1("gf_out", n, m.data, mm.data, x.data, out.data, _i(n))
        return out

    def guided_filter(self, p, I, W, H, r, eps):
        """oklab.guided_filter (p smoothed along I)."""
        if r < 1 or min(H, W) < 2 * r + 1:
            return p
        n = W * H
        mI, mp, mIp, mII, t, a, b = (self.empty(n) for _ in range(7))
        self.box(I, mI, t, W, H, r)
        self.box(p, mp, t, W, H, r)
        self._run1("mul", n, I.data, p.data, a.data, _i(n))
        self.box(a, mIp, t, W, H, r)
        self._run1("mul", n, I.data, I.data, a.data, _i(n))
        self.box(a, mII, t, W, H, r)
        self._run1("gf_ab", n, mI.data, mp.data, mIp.data, mII.data, a.data, b.data,
                   _fl(eps), _i(n))
        self.box(a, mI, t, W, H, r)
        self.box(b, mp, t, W, H, r)
        out = self.empty(n)
        self._run1("gf_out", n, mI.data, mp.data, I.data, out.data, _i(n))
        return out

    def finish_matte(self, m, guide_L, smoothing, W, H):
        """oklab.finish_matte. Returns (matte, top)."""
        top = self.max(m, W * H)
        if top <= 0.0:
            return m, top
        q = self.guided_filter(m, guide_L, W, H,
                               ok.matte_radius(smoothing, (H, W)), ok.GUIDE_EPS)
        if q is m:                       # window didn't fit: clip a copy
            q = m.copy()
        self._run1("matte_tail", W * H, q.data, _fl(top), _i(W * H))
        return q, top

    def decision_lab(self, rgb, W, H, radius):
        """oklab.decision_lab: (denoised planes, the picture's own OKLab
        planes), each (L, a, b)."""
        n = W * H
        L, A, B = self.empty(n), self.empty(n), self.empty(n)
        self._run1("lab_planes", n, rgb.data, L.data, A.data, B.data, _i(n))
        r = ok.scaled_radius(radius, (H, W))
        smooth = tuple(self.guided_smooth(p, W, H, r, ok.DENOISE_EPS[c])
                       for c, p in enumerate((L, A, B)))
        return smooth, (L, A, B)

    def gauss3(self, src, W, H, sigma):
        """cv2.GaussianBlur(src, (0, 0), sigma, borderType=BORDER_REFLECT) on
        an H x W x 3 interleaved image."""
        q = self.queue
        kern = self.upload(gaussian_kernel(sigma))
        tmp, out = self.empty(H * W * 3), self.empty(H * W * 3)
        self.k["gauss_h"](q, (W, H), None, src.data, tmp.data, kern.data,
                          _i(kern.size), _i(W), _i(H))
        self.k["gauss_v"](q, (W, H), None, tmp.data, out.data, kern.data,
                          _i(kern.size), _i(W), _i(H))
        return out

    # -- the tools ---------------------------------------------------------
    def golden(self, rgb, moved, W, H, amount, shape):
        shape = gd.normalize_golden_shape(shape) or dict(gd.GOLDEN_SHAPE_DEFAULT)
        n = W * H
        (Ls, As, Bs), (L, A, B) = self.decision_lab(rgb, W, H, gd.DECISION_R)
        k = self.empty(n)
        self._run1("golden_weight", n, Ls.data, As.data, Bs.data, k.data,
                   _fl(shape["start"] / 100.0), _fl(gd.RISE), _fl(shape["clean"] / 100.0),
                   _fl(gd.TOP), _fl(1.0 - gd.TOP), _fl(gd.SUNLIT_HUE),
                   _fl(gd.SUNLIT_WIDTH), _fl(gd.SUNLIT_CHROMA),
                   _fl(shape["neutral"] / 100.0), _fl(gd.NEUTRAL_BELOW),
                   _fl(gd.NEUTRAL_ABOVE - gd.NEUTRAL_BELOW), _fl(gd.GOLD_CHROMA),
                   _fl(amount / 100.0), _i(n))
        k, top = self.finish_matte(k, Ls, shape["smooth"], W, H)
        if top <= 0.0:
            return
        hg = np.radians(shape["hue"])
        self._run1("golden_apply", n, rgb.data, k.data, L.data, A.data, B.data,
                   moved.data,
                   _fl(np.cos(hg)), _fl(np.sin(hg)), _fl(gd.K_EPS), _i(n))

    def shadow(self, rgb, moved, W, H, amount, shape):
        shape = sn.normalize_shadow_shape(shape) or dict(sn.SHADOW_SHAPE_DEFAULT)
        n = W * H
        kk = min(float(amount), 100.0) / 100.0
        end = shape["range"] / 100.0
        (Ls, As, Bs), (L, A, B) = self.decision_lab(rgb, W, H, ok.DENOISE_R)
        m = self.empty(n)
        self._run1("shadow_weight", n, Ls.data, As.data, Bs.data, m.data, _fl(kk),
                   _fl(sn.FULL_FRAC * end), _fl((1.0 - sn.FULL_FRAC) * end),
                   _fl(shape["reach"] * sn.REACH_UNIT), _i(n))
        m, top = self.finish_matte(m, Ls, shape["smooth"], W, H)
        if top <= 0.0 and not shape["depth"]:
            return
        self._run1("shadow_apply", n, rgb.data, m.data, L.data, A.data, B.data,
                   moved.data,
                   _i(1 if shape["depth"] else 0),
                   _fl(kk * sn.DEPTH_MAX * shape["depth"] / 100.0), _fl(end),
                   _fl(sn.M_EPS), _i(n))

    def glow(self, rgb, moved, W, H, amount, shape):
        shape = gw.normalize_glow_shape(shape) or dict(gw.GLOW_SHAPE_DEFAULT)
        q = self.queue
        n = W * H
        L, lin = self.empty(n), self.empty(n * 3)
        self._run1("glow_L", n, rgb.data, L.data, lin.data, _i(n))
        Ls = self.guided_smooth(L, W, H, ok.scaled_radius(gw.DENOISE_R, (H, W)),
                                gw.DENOISE_EPS[0])
        start = shape["start"] / 100.0
        sigma_px = gw.sigma_frac(shape["size"]) * max(H, W)
        f = max(1, int(sigma_px // gw.MIN_SIGMA_PX))
        if f > 1:
            Ws, Hs = max(1, round(W / f)), max(1, round(H / f))
            xt, yt = area_table(W, Ws), area_table(H, Hs)
        else:
            Ws, Hs = W, H
            xt, yt = identity_table(W), identity_table(H)
        xoff, xsrc, xw = (self.upload(a) for a in xt)
        yoff, ysrc, yw = (self.upload(a) for a in yt)
        hbuf = self.empty(H * Ws * 3)
        self.k["glow_area_h"](q, (Ws, H), None, lin.data, Ls.data, hbuf.data,
                              xoff.data, xsrc.data, xw.data,
                              _fl(start - gw.KNEE), _fl(2 * gw.KNEE),
                              _i(W), _i(H), _i(Ws))
        small = self.empty(Hs * Ws * 3)
        self.k["glow_area_v"](q, (Ws, Hs), None, hbuf.data, small.data,
                              yoff.data, ysrc.data, yw.data, _i(Ws), _i(Hs))
        s = sigma_px * Ws / float(W)
        blurred = [self.gauss3(small, Ws, Hs, sig) for sig in (s, s * gw.TAIL_X)]
        field = self.empty(Hs * Ws * 3)
        self._run1("glow_mix", Hs * Ws * 3, blurred[0].data, blurred[1].data,
                   field.data, _fl(gw.CORE_W), _fl(gw.TAIL_W), _i(Hs * Ws * 3))
        Lh = self.guided_filter(Ls, Ls, W, H, max(1, int(round(gw.HOLD_R * sigma_px))),
                                gw.HOLD_EPS)
        if f > 1:
            xs0, xs1, xa = linear_table_x(Ws, W)
            ys0, ys1, yb = linear_table_y(Hs, H)
        else:
            xs0 = xs1 = ys0 = ys1 = np.zeros(1, np.int32)
            xa = yb = np.zeros(1, np.float32)
        bufs = [self.upload(a) for a in (xs0, xs1, xa, ys0, ys1, yb)]
        self.k["glow_apply"](q, (W, H), None, rgb.data, lin.data, moved.data, Lh.data,
                             field.data,
                             _i(1 if f > 1 else 0), *[b.data for b in bufs],
                             _i(W), _i(H), _i(Ws),
                             _fl(min(float(amount), 100.0) / 100.0 * gw.GAIN),
                             _fl(gw.HOLD), _fl(start - gw.HOLD_KNEE),
                             _fl(2 * gw.HOLD_KNEE), _fl(gw.G_EPS))

    # -- the chain ---------------------------------------------------------
    def finish(self, img_buf, H, W, golden=0.0, golden_shape=None, shadow=0.0,
               shadow_shape=None, glow=0.0, glow_shape=None):
        """Run the active tools on the main kernel's buffer (H*W x 3 float,
        0..65535), in place. Pixels no tool moved keep their exact value; on
        any error before the last step the buffer is untouched."""
        n = W * H
        rgb = self.empty(n * 3)
        self._run1("unpack01", n * 3, img_buf.data, rgb.data, _i(n * 3))
        moved = self.empty(n, np.uint8)
        self._run1("zero_u8", n, moved.data, _i(n))
        if golden and golden > 0:
            self.golden(rgb, moved, W, H, float(golden), golden_shape)
        if shadow and shadow > 0:
            self.shadow(rgb, moved, W, H, float(shadow), shadow_shape)
        if glow and glow > 0:
            self.glow(rgb, moved, W, H, float(glow), glow_shape)
        self._run1("pack16", n, rgb.data, moved.data, img_buf.data, _i(n))
        del rgb, moved
        if n > BIG_RENDER:
            self.queue.finish()
            self.pool.free_held()      # don't sit on an export's worth of memory


def finish_numpy_image(ctx, queue, img01, golden=0.0, golden_shape=None, shadow=0.0,
                       shadow_shape=None, glow=0.0, glow_shape=None):
    """Convenience for tests and the bench: the GPU chain on an HxWx3 float
    image in 0..1 (as the numpy tools take it). Returns HxWx3 float32."""
    a = np.ascontiguousarray(np.asarray(img01, np.float32))
    H, W = a.shape[:2]
    g = for_queue(ctx, queue)
    buf = cla.to_device(queue, (a * np.float32(65535.0)).reshape(-1, 3))
    g.finish(buf, H, W, golden, golden_shape, shadow, shadow_shape, glow, glow_shape)
    return buf.get().reshape(H, W, 3) / np.float32(65535.0)
