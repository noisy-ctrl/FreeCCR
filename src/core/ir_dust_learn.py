"""IR dust learns from the user's corrections (spec/ir-dust-v2.md §4).

Each correction is an example (the candidate's eight features, dust or not,
a weight): Erase Spots over an IR spot says "not dust" (weight 1), a hand
stroke over a candidate that wasn't flagged says "dust I had to find myself"
(weight 1), and Heal Them says "dust" for every flagged spot healed (weight
0.3: he could have erased it, but may not have looked).

Examples are kept per kind of scan ("merged": a trichrome's three shots;
"single": one visible shot), because the colour shots' features differ, in
`<app data>/FreeCCR/ir_dust_learned.json` (FREECCR_IR_DUST_LEARNED overrides),
at most MAX_EXAMPLES per kind, oldest out.

The model is a logistic regression in the defaults' standardised units
(`ir_dust_default`), fitted MAP with a Gaussian prior centred on the default
weights (precision worth PRIOR_N examples), so a few corrections nudge it and
many move it. Until MIN_EXPLICIT weight-1 corrections exist the defaults
stand. Qt-free.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

MAX_EXAMPLES = 3000
PRIOR_N = 60.0             # the defaults count as this many examples
MIN_EXPLICIT = 10          # weight-1 corrections before the model moves
HEAL_WEIGHT = 0.3
KINDS = ("merged", "single")

_lock = threading.Lock()
_state: Dict[str, object] = {"path": None, "data": None, "models": {}}


def store_path() -> str:
    env = os.environ.get("FREECCR_IR_DUST_LEARNED")
    if env:
        return env
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "FreeCCR", "ir_dust_learned.json")


def default_model() -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    from core import ir_dust_default as d
    return (np.asarray(d.MU, np.float64), np.asarray(d.SD, np.float64),
            np.asarray(d.W, np.float64), float(d.B))


def _data() -> dict:
    """The store, read once per path (tests switch the path)."""
    path = store_path()
    if _state["path"] != path or _state["data"] is None:
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        _state.update(path=path, data=data, models={})
    return _state["data"]


def _save(data: dict) -> None:
    path = store_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def add(kind: str, examples: Sequence[Tuple[Sequence[float], int, float]]) -> int:
    """Record examples (features, 1 dust / 0 not, weight) for a kind of scan.
    Returns how many were kept."""
    if kind not in KINDS or not examples:
        return 0
    rows = []
    for feats, label, weight in examples:
        f = [float(v) for v in feats]
        if len(f) != 8 or not all(np.isfinite(f)):
            continue
        rows.append([round(v, 4) for v in f] + [1 if label else 0, float(weight)])
    if not rows:
        return 0
    with _lock:
        data = _data()
        ex = list(data.get(kind, {}).get("examples", [])) + rows
        data[kind] = {"examples": ex[-MAX_EXAMPLES:]}
        _state["models"] = {}
        _save(data)
    return len(rows)


def examples(kind: str) -> List[list]:
    with _lock:
        return list(_data().get(kind, {}).get("examples", []))


def explicit_count(kind: str) -> int:
    """How many weight-1 corrections (erasures, hand-found dust) a kind has."""
    return sum(1 for e in examples(kind) if e[-1] >= 0.999)


def forget(kind: str) -> None:
    with _lock:
        data = _data()
        data.pop(kind, None)
        _state["models"] = {}
        _save(data)


def fit_map(X: np.ndarray, y: np.ndarray, wts: np.ndarray, base=None,
            prior_n: float = PRIOR_N):
    """MAP logistic regression in the base model's standardised units, the
    prior a Gaussian on (w, b) centred on the base's (precision prior_n x
    0.25, a logistic example's information at p = 0.5). Returns a model."""
    mu, sd, w0, b0 = base or default_model()
    Z = np.hstack([(np.asarray(X, np.float64) - mu) / sd, np.ones((len(X), 1))])
    th0 = np.r_[w0, b0]
    th = th0.copy()
    tau = prior_n * 0.25
    y = np.asarray(y, np.float64)
    wts = np.asarray(wts, np.float64)
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ th, -40, 40)))
        g = Z.T @ (wts * (p - y)) + tau * (th - th0)
        H = (Z * (wts * p * (1 - p))[:, None]).T @ Z + tau * np.eye(len(th))
        step = np.linalg.solve(H, g)
        th -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return mu, sd, th[:-1], float(th[-1])


def model_for(kind: str):
    """The model a kind of scan is judged by: the defaults until it has
    MIN_EXPLICIT corrections, then the defaults moved by its examples."""
    with _lock:
        cache = _state["models"]
        data = _data()
        if kind in cache:
            return cache[kind]
        ex = list(data.get(kind, {}).get("examples", []))
    base = default_model()
    if kind not in KINDS or sum(1 for e in ex if e[-1] >= 0.999) < MIN_EXPLICIT:
        model = base
    else:
        A = np.asarray(ex, np.float64)
        model = fit_map(A[:, :8], A[:, 8], A[:, 9], base)
    with _lock:
        _state["models"][kind] = model
    return model


def reset_cache() -> None:
    """Read the store again next time (tests; another window wrote it)."""
    with _lock:
        _state.update(path=None, data=None, models={})
