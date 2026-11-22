"""The colour shots' marks learn from corrections (spec/marks-learning.md).

The rule (`film_marks.passes`) stands until a kind of mark has
`ir_dust_learn.MIN_EXPLICIT` corrections; then a logistic model fitted to
them, starting from a fit of the rule itself (`marks_default`), decides
among the marks the structural gate lets through. Sensitivity keeps the
rule's meaning: the line moves by the default model's logit between the
rule's thresholds at that Sensitivity and at 50. Qt-free.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

import numpy as np

KINDS = {"light": "marks-light", "dark": "marks-dark"}


def features(f: Sequence[float]) -> np.ndarray:
    """A mark's stored features (`film_marks.FEATURES`) in model units: log
    depth, log stands-out, ring step, log area, log elongation, IR support,
    colour. Works on one row or many."""
    a = np.atleast_2d(np.asarray(f, np.float64)).copy()
    a[:, 0] = np.log(np.maximum(a[:, 0], 1e-3))
    a[:, 1] = np.log(np.maximum(a[:, 1], 1e-3))
    a[:, 4] = np.log(np.maximum(a[:, 4], 1.0))
    return a if np.ndim(f) == 2 else a[0]


def default(kind: str):
    """(mu, sd, w, b) of the rule's fit for "light" / "dark"."""
    from core import marks_default as d
    m = d.MODELS[kind]
    return (np.asarray(m["MU"], np.float64), np.asarray(m["SD"], np.float64),
            np.asarray(m["W"], np.float64), float(m["B"]))


def logit(model, f) -> float:
    mu, sd, w, b = model
    return float(((features(f) - mu) / sd) @ w + b)


def corner(kind: str, sensitivity: float) -> float:
    """The default model's logit at the rule's thresholds for a Sensitivity
    (the other features at their means)."""
    from core import film_marks
    mu, sd, w, b = default(kind)
    d, o = film_marks.thresholds(sensitivity)
    v = mu.copy()
    v[0], v[1] = math.log(d), math.log(o)
    return float(((v - mu) / sd) @ w + b)


def shift(kind: str, sensitivity: float) -> float:
    """How far the line moves from Sensitivity 50, in logit."""
    return corner(kind, sensitivity) - corner(kind, 50)


def learned(kind: str):
    """The learned model for "light" / "dark", or None while the rule stands."""
    from core import ir_dust_learn
    return ir_dust_learn.model_for(KINDS[kind])


def decide(kind: str, f, sensitivity: float) -> Optional[bool]:
    """True / False by the learned model; None when there is none."""
    model = learned(kind)
    if model is None:
        return None
    return logit(model, f) >= shift(kind, sensitivity)
