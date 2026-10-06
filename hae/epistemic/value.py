"""Value: how close a ledger is to resolved, and how to learn that from data.

AlphaGo's value network estimates the probability of winning from a board.
The analogue here is the probability that a ledger in this state ends with
its questions certified by the oracle within the remaining budget. We do not
have a learned estimate of that yet (V7 will train one on certified
trajectories), so V6 ships:

  * `epistemic_features` -- a fixed, documented feature vector computed from
    the ledger alone, which is what both heads consume and what every
    `MoveRecord` stores for later training;
  * `EpistemicValueFunction` -- `alpha * resolved_fraction + (1 - alpha) * head`,
    where `resolved_fraction` is uncertainty *actually* resolved by evidence
    (the part of the value that cannot be faked) and `head` is either the
    heuristic below or a `LearnedValueHead`;
  * `LearnedValueHead` -- logistic regression in pure Python, with JSON
    persistence, so a head trained offline can be shipped inside a genome's
    `extra` without a dependency;
  * `brier_score` -- the calibration metric the audit uses on System 1 priors.

None of this reads a hypothesis' text. Value is a function of what evidence
has established, not of how persuasive the claims sound.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_CERTIFIED, Q_RESOLVED, SUPPORTED, UNVERIFIED, EpistemicState,
)

FEATURE_NAMES: Tuple[str, ...] = (
    "resolved_fraction",      # 1 - U_now / U_initial
    "certified_ratio",        # certified questions / questions
    "resolved_ratio",         # RESOLVED (patched, awaiting oracle) / questions
    "supported_ratio",        # hypotheses SUPPORTED or CERTIFIED / hypotheses
    "ruled_out_ratio",        # FALSIFIED / hypotheses
    "untested_ratio",         # UNVERIFIED / hypotheses
    "evidence_density",       # evidence / hypotheses
    "stagnation",             # trailing zero-progress moves / 10, capped at 1
    "open_without_options",   # open questions with no live hypothesis / open questions
    "module_isolation",       # 1 / distinct modules among open questions
    "mean_open_uncertainty",  # mean uncertainty of open questions
)

STAGNATION_SCALE = 10.0


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def epistemic_features(state: EpistemicState) -> Dict[str, float]:
    """A fixed feature vector over the ledger. Every value is in [0, 1]."""
    questions = list(state.questions.values())
    hyps = list(state.hypotheses.values())
    open_qs = [q for q in questions if q.is_open]
    n_q, n_h = len(questions), len(hyps)

    u_init = state.initial_uncertainty()
    u_now = state.total_uncertainty()
    resolved_fraction = 1.0 - _safe_div(u_now, u_init) if u_init > 0 else 0.0

    live_by_q = {q.question_id: any(h.status in (UNVERIFIED, SUPPORTED, CERTIFIED)
                                    for h in state.hypotheses_for(q.question_id))
                 for q in open_qs}
    without_options = sum(1 for v in live_by_q.values() if not v)
    modules = {q.module for q in open_qs}

    feats = {
        "resolved_fraction": max(0.0, min(1.0, resolved_fraction)),
        "certified_ratio": _safe_div(sum(1 for q in questions if q.status == Q_CERTIFIED), n_q),
        "resolved_ratio": _safe_div(sum(1 for q in questions if q.status == Q_RESOLVED), n_q),
        "supported_ratio": _safe_div(sum(1 for h in hyps if h.status in (SUPPORTED, CERTIFIED)), n_h),
        "ruled_out_ratio": _safe_div(sum(1 for h in hyps if h.status == FALSIFIED), n_h),
        "untested_ratio": _safe_div(sum(1 for h in hyps if h.status == UNVERIFIED), n_h),
        "evidence_density": min(1.0, _safe_div(len(state.evidence_log), max(1, n_h))),
        "stagnation": min(1.0, state.stagnation_depth() / STAGNATION_SCALE),
        "open_without_options": _safe_div(without_options, len(open_qs)),
        "module_isolation": (1.0 / len(modules)) if modules else 0.0,
        "mean_open_uncertainty": _safe_div(sum(q.uncertainty for q in open_qs), len(open_qs)),
    }
    return {k: round(float(feats[k]), 6) for k in FEATURE_NAMES}


def heuristic_value(features: Mapping[str, float]) -> float:
    """A hand-set head for before any trajectories exist. In [0, 1].

    Rewards certified and resolved questions most, supported hypotheses next,
    then narrowing by elimination; penalises stagnation and open questions
    that have run out of live hypotheses.
    """
    f = features
    v = (0.35 * f.get("certified_ratio", 0.0)
         + 0.20 * f.get("resolved_ratio", 0.0)
         + 0.15 * f.get("supported_ratio", 0.0)
         + 0.10 * f.get("ruled_out_ratio", 0.0)
         + 0.10 * (1.0 - f.get("stagnation", 0.0))
         + 0.05 * f.get("module_isolation", 0.0)
         + 0.05 * (1.0 - f.get("open_without_options", 0.0)))
    return max(0.0, min(1.0, v))


class LearnedValueHead:
    """Logistic regression over `FEATURE_NAMES`, pure Python, JSON-portable.

    Trained offline on `(features, outcome)` pairs where outcome is 1 if the
    trajectory the features were recorded on ended with that question
    certified by the oracle. Small on purpose: with tens of firms per
    generation a larger model would memorise, and a head nobody can inspect
    is the thing this whole design is trying not to be.
    """

    def __init__(self, weights: Optional[Mapping[str, float]] = None, bias: float = 0.0) -> None:
        self.weights: Dict[str, float] = {k: 0.0 for k in FEATURE_NAMES}
        if weights:
            for k, v in weights.items():
                if k in self.weights:
                    self.weights[k] = float(v)
        self.bias = float(bias)
        self.trained_on = 0

    def predict(self, features: Mapping[str, float]) -> float:
        z = self.bias + sum(self.weights[k] * float(features.get(k, 0.0)) for k in FEATURE_NAMES)
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def fit(self, samples: Sequence[Tuple[Mapping[str, float], float]],
            epochs: int = 300, lr: float = 0.1, l2: float = 1e-3) -> "LearnedValueHead":
        data = [(dict(f), 1.0 if float(y) >= 0.5 else 0.0) for f, y in samples]
        if not data:
            return self
        for _ in range(max(1, int(epochs))):
            grad = {k: 0.0 for k in FEATURE_NAMES}
            grad_b = 0.0
            for feats, y in data:
                err = self.predict(feats) - y
                for k in FEATURE_NAMES:
                    grad[k] += err * float(feats.get(k, 0.0))
                grad_b += err
            n = float(len(data))
            for k in FEATURE_NAMES:
                self.weights[k] -= lr * (grad[k] / n + l2 * self.weights[k])
            self.bias -= lr * grad_b / n
        self.trained_on = len(data)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {"weights": dict(self.weights), "bias": self.bias,
                "trained_on": self.trained_on, "features": list(FEATURE_NAMES)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LearnedValueHead":
        head = cls(weights=data.get("weights"), bias=float(data.get("bias", 0.0)))
        head.trained_on = int(data.get("trained_on", 0))
        return head

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "LearnedValueHead":
        return cls.from_dict(json.loads(text))


class EpistemicValueFunction:
    """`alpha * resolved_fraction + (1 - alpha) * head(features)`.

    `alpha` is a gene (`EpistemicPolicyGene.value_alpha`). At 1.0 the value
    is purely what evidence has resolved; at 0.0 it is purely the head's
    estimate of what is *likely* to resolve. Both are legitimate; the gene
    lets selection find the mix.
    """

    def __init__(self, alpha: float = 0.6, head: Optional[LearnedValueHead] = None) -> None:
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self.head = head

    def head_value(self, features: Mapping[str, float]) -> float:
        if self.head is not None:
            return self.head.predict(features)
        return heuristic_value(features)

    def value_from_features(self, features: Mapping[str, float]) -> float:
        v = self.alpha * float(features.get("resolved_fraction", 0.0)) + (1.0 - self.alpha) * self.head_value(features)
        return round(max(0.0, min(1.0, v)), 6)

    def value(self, state: EpistemicState) -> float:
        return self.value_from_features(epistemic_features(state))


def brier_score(predictions: Iterable[float], outcomes: Iterable[float]) -> float:
    """Mean squared error between stated probabilities and binary outcomes. 0 is perfect."""
    pairs = list(zip(list(predictions), list(outcomes)))
    if not pairs:
        return 0.0
    return round(sum((float(p) - float(o)) ** 2 for p, o in pairs) / len(pairs), 6)


def calibration_report(state: EpistemicState) -> Dict[str, Any]:
    """How honest System 1's priors were, judged against the gatekeeper's verdicts."""
    tested = [h for h in state.hypotheses.values() if h.status in (SUPPORTED, FALSIFIED, CERTIFIED)]
    preds = [h.prior for h in tested]
    outs = [1.0 if h.status in (SUPPORTED, CERTIFIED) else 0.0 for h in tested]
    overconfident = sum(1 for h in tested if h.status == FALSIFIED and h.prior >= 0.7)
    low_prior_wins = sum(1 for h in tested if h.status in (SUPPORTED, CERTIFIED) and h.prior <= 0.35)
    return {
        "tested": len(tested),
        "brier": brier_score(preds, outs),
        "base_rate": round(sum(outs) / len(outs), 4) if outs else 0.0,
        "overconfident_falsified": overconfident,
        "low_prior_wins": low_prior_wins,
    }
