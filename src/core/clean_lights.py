"""Clean Lights: less colour in the light tones at unchanged lightness, as the
labs render them (spec/clean-lights.md).

On display values (sRGB-encoded 0..1) in OKLab, float32 throughout:

- HOW MUCH is a matte decided on the denoised decision view of the picture
  (core.oklab.decision_lab), never on a pixel's own noisy lightness:
  m = 1 - (1 - r_lab(L))^(amount/50), r_lab(L) = R_MAX . smoothstep((L - L0)
  / SPAN)^POWER, the share of colour three labs take out of each light tone
  (measured, spec table); then finished along the picture's edges
  (core.oklab.finish_matte);
- APPLIED per pixel: the pixel's own (a, b) x (1 - m) at its own L, so luma
  and hue are untouched and colour grain only ever falls;
- a pixel the move would take out of 0..1 gets the largest fraction that
  fits; pixels it doesn't move (below L ~0.70) are returned EXACTLY."""
import numpy as np

from core.oklab import to_oklab, smooth, fit_move, decision_lab, finish_matte

# The labs' measured amount (slider 50), fitted on 33 Portra 400 pairs from
# three labs: 0 below L 0.70, 4 % at 0.83, 16 % at 0.88, 38 % at 0.93.
L0 = 0.70
SPAN = 0.29
POWER = 3.0
R_MAX = 0.574
LAB_AMOUNT = 50.0        # slider value that gives the labs' amount
SMOOTHING = 30           # finish_matte, as the other finishing tools' default
M_EPS = 1e-5             # a smaller strength moves a pixel < 1/1000 code
CHUNK = 1 << 20


def lab_share(L):
    """The share of colour the labs take out at lightness L (0..R_MAX)."""
    return (np.float32(R_MAX) * smooth((L - np.float32(L0)) / np.float32(SPAN))
            ** np.float32(POWER)).astype(np.float32)


def strength(L, amount):
    """m(L) for a slider amount: 1 - (1 - r_lab)^(amount/50), never above 1."""
    e = np.float32(max(float(amount), 0.0) / LAB_AMOUNT)
    return (1.0 - (1.0 - lab_share(L)) ** e).astype(np.float32)


def strength_map(img01, amount, with_full=False):
    smooth_lab, lab = decision_lab(img01)
    m = finish_matte(strength(smooth_lab[..., 0], amount), smooth_lab[..., 0], SMOOTHING)
    return (m, lab) if with_full else m


def _chunk(x, m, lab):
    out = x.copy()
    moved = m > M_EPS
    if not moved.any():
        return out
    lm, f = lab[moved], (1.0 - m[moved])
    new = lm.copy()
    new[:, 1] *= f
    new[:, 2] *= f
    out[moved] = np.clip(fit_move(lm, new - lm), 0.0, 1.0)
    return out


def apply_clean_lights(img01, amount):
    """Clean Lights on an HxWx3 float image in 0..1. A new float32 array;
    the input itself at 0."""
    if not amount or amount <= 0:
        return img01
    amount = min(float(amount), 100.0)
    a = np.asarray(img01, dtype=np.float32)
    flat = a.reshape(-1, 3)
    m, full = strength_map(a, amount, with_full=True)
    m = m.reshape(-1)
    full = full.reshape(-1, 3)
    out = flat.copy()
    for s in range(0, flat.shape[0], CHUNK):
        sl = slice(s, s + CHUNK)
        if not (m[sl] > M_EPS).any():
            continue
        out[sl] = _chunk(flat[sl], m[sl], full[sl])
    return out.reshape(a.shape)
