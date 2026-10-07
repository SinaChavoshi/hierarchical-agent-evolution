"""Value: how close a ledger is to resolved, and how to learn that from data.

AlphaGo's value network estimates the probability of winning from a board.
The analogue here is the probability that a ledger in this state ends with
its questions certified by the oracle within the remaining budget. V6 ships:

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

V7 adds the first *learned* heads, trained offline by
`scripts/train_value_heads.py` on the Gen 16 trees:

  * `hypothesis_features` / `HypothesisPriorHead` -- a per-hypothesis feature
    vector and a logistic head over it. This is the head that can change a
    decision: the PUCT prior `P` over a question's untested hypotheses is
    today the proposer's stated prior, which the Gen 16 cohort showed to be
    uninformative (hit rate ~0.10 in every prior bin). The loop mixes
    `(1 - w) * stated_prior + w * p_head` with `w = 0` by default, so a loaded
    head only *shadows* (its scores are recorded on every experiment move)
    until a run shows it beats the stated prior on fresh firms;
  * `load_policy_heads` -- reads the shadow-mode knobs from
    `EpistemicPolicyGene.extra` and loads the JSON heads; never raises.

Both heads share `LogisticHead`: full-batch gradient descent (what
`LearnedValueHead.fit` always did) plus a damped Newton solver the trainer
uses to reach the regularised optimum exactly.

None of the *state* value reads a hypothesis' text. The hypothesis head reads
the *shape* of a proposal (probe length, whether it imports the module under
question, whether the prediction is checkable) and the proposer's prior, never
the persuasiveness of its claim; nothing here can set a belief.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_CERTIFIED, Q_RESOLVED, SUPPORTED, UNVERIFIED, EpistemicState,
    Hypothesis, Question,
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


# ---------------------------------------------------------------------------
# Hypothesis-level features (V7 prior head)
# ---------------------------------------------------------------------------

# The two one-hot groups below are benchmark-specific: the oracle failure
# classes and the four graded modules of `configs/tasks/full_stack_hae.json`.
# They are documented as such so that a head trained on Gen 16 is not
# mistaken for a general one; on another task family they are all zero and
# the head falls back to the structural features.
FAILURE_CLASSES: Tuple[str, ...] = ("fail_import", "fail_name", "fail_assertion", "fail_other")
BENCHMARK_MODULES: Tuple[str, ...] = ("harness", "verification_loop", "artifacts", "morphogenesis")

PROBE_LINES_SCALE = 40.0        # `EpistemicPolicyGene.max_probe_lines` default
CLAIM_LENGTH_SCALE = 200.0
QUESTION_ROUNDS_SCALE = 3.0

HYPOTHESIS_FEATURE_NAMES: Tuple[str, ...] = (
    "stated_prior",            # the proposer's prior as declared
    "prior_rank",              # rank of the prior among siblings: 0 lowest .. 1 highest; 0.5 alone
    "sibling_share",           # 1 / number of siblings (incl. this hypothesis)
    "is_lowest_prior",         # no sibling has a strictly lower prior (0 when alone)
    "probe_lines",             # non-blank probe lines / 40, capped
    "probe_guarded_import",    # probe contains `try:` and `ImportError`
    "probe_imports_module",    # probe imports the question's module
    "prediction_exit_code",    # prediction states an exit code
    "prediction_marker",       # prediction states a stdout/stderr marker
    "claim_length",            # claim characters / 200, capped
    "question_uncertainty",    # the question's uncertainty when the hypothesis was selected
    "question_rounds",         # proposal rounds spent on the question this run / 3, capped
    "oracle_seeded",           # the question came from an oracle failure
) + FAILURE_CLASSES + tuple(f"mod_{m}" for m in BENCHMARK_MODULES) + tuple(f"s_{n}" for n in FEATURE_NAMES)

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([\w\.]+)(?:\s+import\s+([\w\.\*,\s\(\)]+))?", re.M)


def failure_class(source_failure: str) -> str:
    """Coarse class of an oracle failure line; '' when there is no text."""
    text = str(source_failure or "")
    if not text.strip():
        return ""
    tail = text.split("->", 1)[1] if "->" in text else text
    if re.search(r"ImportError|ModuleNotFoundError|No module named|failed to import|suite_import", tail):
        return "fail_import"
    if re.search(r"NameError|AttributeError|UnboundLocalError", tail):
        return "fail_name"
    if re.search(r"AssertionError|\bassert\b", tail):
        return "fail_assertion"
    return "fail_other"


def module_key(module: str) -> str:
    """`hae/evaluation/harness.py` -> `harness`."""
    base = str(module or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return base[:-3] if base.endswith(".py") else base


def probe_imports_module(probe_code: str, module: str) -> bool:
    """Does the probe import the question's module (`import a.b.c`, `from a.b.c import`,
    or `from a.b import c`)?"""
    rel = str(module or "").replace("\\", "/").lstrip("./")
    if not rel or not probe_code:
        return False
    dotted = rel[:-3] if rel.endswith(".py") else rel
    dotted = dotted.replace("/", ".")
    parent, _, leaf = dotted.rpartition(".")
    for m in _IMPORT_RE.finditer(str(probe_code)):
        target, names = m.group(1), m.group(2) or ""
        if target == dotted or target.startswith(dotted + "."):
            return True
        if parent and target == parent and leaf and re.search(rf"\b{re.escape(leaf)}\b", names):
            return True
    return False


def rank_among(value: float, others: Sequence[float]) -> float:
    """0 = lowest .. 1 = highest among `others + [value]`; ties share rank; 0.5 if alone."""
    if not others:
        return 0.5
    below = sum(1 for o in others if o < value)
    equal = sum(1 for o in others if o == value)
    return (below + 0.5 * equal) / float(len(others))


def hypothesis_features(question: Question, hypothesis: Hypothesis,
                        siblings: Sequence[Hypothesis],
                        state_features: Mapping[str, float]) -> Dict[str, float]:
    """A fixed, deterministic feature vector for one hypothesis. Every value in [0, 1].

    `siblings` are the question's hypotheses *at selection time*, any status
    (the trainer reconstructs this set from `created_at`); `hypothesis` need
    not be in it. `state_features` is `epistemic_features(state)` before the
    move, which `MoveRecord.features` already stores. No LLM call, no side
    effects, nothing read from the hypothesis beyond its own fields.
    """
    others = [s.prior for s in siblings if s.hypothesis_id != hypothesis.hypothesis_id]
    n_sib = len(others) + 1
    probe = str(hypothesis.probe_code or "")
    probe_lines = sum(1 for line in probe.splitlines() if line.strip())
    pred = dict(hypothesis.prediction or {})
    marker = any(str(pred.get(k) or "").strip() for k in ("expect_stdout_contains", "expect_stderr_contains"))
    fclass = failure_class(question.source_failure)
    mkey = module_key(question.module)
    feats: Dict[str, float] = {
        "stated_prior": float(hypothesis.prior),
        "prior_rank": rank_among(float(hypothesis.prior), others),
        "sibling_share": 1.0 / n_sib,
        "is_lowest_prior": 1.0 if others and all(o >= hypothesis.prior for o in others) else 0.0,
        "probe_lines": min(1.0, probe_lines / PROBE_LINES_SCALE),
        "probe_guarded_import": 1.0 if ("try:" in probe and "ImportError" in probe) else 0.0,
        "probe_imports_module": 1.0 if probe_imports_module(probe, question.module) else 0.0,
        "prediction_exit_code": 1.0 if pred.get("expect_exit_code") is not None else 0.0,
        "prediction_marker": 1.0 if marker else 0.0,
        "claim_length": min(1.0, len(str(hypothesis.claim or "")) / CLAIM_LENGTH_SCALE),
        "question_uncertainty": max(0.0, min(1.0, float(question.uncertainty))),
        "question_rounds": min(1.0, max(0, int(question.hypothesis_rounds)) / QUESTION_ROUNDS_SCALE),
        "oracle_seeded": 1.0 if str(question.source_failure_key or "").strip() else 0.0,
    }
    for name in FAILURE_CLASSES:
        feats[name] = 1.0 if fclass == name else 0.0
    for m in BENCHMARK_MODULES:
        feats[f"mod_{m}"] = 1.0 if mkey == m else 0.0
    for name in FEATURE_NAMES:
        feats[f"s_{name}"] = max(0.0, min(1.0, float(state_features.get(name, 0.0))))
    return {k: round(float(feats[k]), 6) for k in HYPOTHESIS_FEATURE_NAMES}


# ---------------------------------------------------------------------------
# Logistic heads
# ---------------------------------------------------------------------------

def _solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    """Gaussian elimination with partial pivoting (the systems here are <= 40x40)."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-18:
            raise ArithmeticError("singular system")
        a[col], a[pivot] = a[pivot], a[col]
        inv = 1.0 / a[col][col]
        for r in range(n):
            if r != col and a[r][col] != 0.0:
                f = a[r][col] * inv
                row_r, row_c = a[r], a[col]
                for c in range(col, n + 1):
                    row_r[c] -= f * row_c[c]
    return [a[i][n] / a[i][i] for i in range(n)]


class LogisticHead:
    """Logistic regression over a fixed feature tuple, pure Python, JSON-portable.

    Subclasses set `FEATURES`. Two fitters share the model: `fit` is
    full-batch gradient descent on mean log-loss + `l2/2 * ||w||^2` (bias
    unpenalised), `fit_newton` is damped Newton on the same objective and
    reaches its optimum in a few iterations, which is what makes an honest
    out-of-fold comparison of `l2` values possible without numpy.
    """

    FEATURES: Tuple[str, ...] = ()

    def __init__(self, weights: Optional[Mapping[str, float]] = None, bias: float = 0.0) -> None:
        self.weights: Dict[str, float] = {k: 0.0 for k in self.FEATURES}
        if weights:
            for k, v in weights.items():
                if k in self.weights:
                    self.weights[k] = float(v)
        self.bias = float(bias)
        self.trained_on = 0
        self.metadata: Dict[str, Any] = {}

    # ------------------------------------------------------------ predict

    def predict(self, features: Mapping[str, float]) -> float:
        z = self.bias + sum(self.weights[k] * float(features.get(k, 0.0)) for k in self.FEATURES)
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def _vectorise(self, samples: Sequence[Tuple[Mapping[str, float], float]]) -> List[Tuple[List[float], float]]:
        return [([float(f.get(k, 0.0)) for k in self.FEATURES], 1.0 if float(y) >= 0.5 else 0.0)
                for f, y in samples]

    def _predict_vec(self, x: Sequence[float]) -> float:
        w = self.weights
        z = self.bias + sum(w[k] * x[i] for i, k in enumerate(self.FEATURES))
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def _objective(self, data: Sequence[Tuple[Sequence[float], float]], l2: float) -> float:
        loss = 0.0
        for x, y in data:
            p = min(1.0 - 1e-12, max(1e-12, self._predict_vec(x)))
            loss -= y * math.log(p) + (1.0 - y) * math.log(1.0 - p)
        return loss / len(data) + 0.5 * l2 * sum(v * v for v in self.weights.values())

    # ---------------------------------------------------------------- fit

    def fit(self, samples: Sequence[Tuple[Mapping[str, float], float]],
            epochs: int = 300, lr: float = 0.1, l2: float = 1e-3) -> "LogisticHead":
        """Full-batch gradient descent. Unchanged from the V6 `LearnedValueHead.fit`."""
        data = self._vectorise(samples)
        if not data:
            return self
        names = self.FEATURES
        for _ in range(max(1, int(epochs))):
            grad = [0.0] * len(names)
            grad_b = 0.0
            for x, y in data:
                err = self._predict_vec(x) - y
                for i in range(len(names)):
                    grad[i] += err * x[i]
                grad_b += err
            n = float(len(data))
            for i, k in enumerate(names):
                self.weights[k] -= lr * (grad[i] / n + l2 * self.weights[k])
            self.bias -= lr * grad_b / n
        self.trained_on = len(data)
        return self

    def fit_newton(self, samples: Sequence[Tuple[Mapping[str, float], float]],
                   l2: float = 1e-2, iterations: int = 30, tol: float = 1e-9) -> "LogisticHead":
        """Damped Newton (IRLS) on the same objective as `fit`; converges in ~10 steps."""
        data = self._vectorise(samples)
        if not data:
            return self
        names = self.FEATURES
        d = len(names)
        n = float(len(data))
        params = [self.weights[k] for k in names] + [self.bias]
        last = self._objective(data, l2)
        for _ in range(max(1, int(iterations))):
            grad = [0.0] * (d + 1)
            hess = [[0.0] * (d + 1) for _ in range(d + 1)]
            for x, y in data:
                p = self._predict_vec(x)
                err = p - y
                s = p * (1.0 - p)
                xb = list(x) + [1.0]
                for i in range(d + 1):
                    xi = xb[i]
                    if xi == 0.0:
                        continue
                    grad[i] += err * xi
                    sx = s * xi
                    row = hess[i]
                    for j in range(d + 1):
                        row[j] += sx * xb[j]
            for i in range(d + 1):
                grad[i] /= n
                for j in range(d + 1):
                    hess[i][j] /= n
            for i in range(d):            # L2 on weights only, never on the bias
                grad[i] += l2 * params[i]
                hess[i][i] += l2
            hess[d][d] += 1e-12
            try:
                step = _solve(hess, grad)
            except ArithmeticError:
                break
            improved = False
            scale = 1.0
            for _half in range(12):       # backtracking keeps the objective monotone
                trial = [params[i] - scale * step[i] for i in range(d + 1)]
                for i, k in enumerate(names):
                    self.weights[k] = trial[i]
                self.bias = trial[d]
                obj = self._objective(data, l2)
                if obj <= last + 1e-15:
                    improved = True
                    break
                scale *= 0.5
            if not improved:
                for i, k in enumerate(names):
                    self.weights[k] = params[i]
                self.bias = params[d]
                break
            converged = last - obj < tol
            params, last = trial, obj
            if converged:
                break
        self.trained_on = len(data)
        return self

    # ---------------------------------------------------------- persist

    def to_dict(self) -> Dict[str, Any]:
        out = {"weights": dict(self.weights), "bias": self.bias,
               "trained_on": self.trained_on, "features": list(self.FEATURES)}
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogisticHead":
        head = cls(weights=data.get("weights"), bias=float(data.get("bias", 0.0)))
        head.trained_on = int(data.get("trained_on", 0))
        meta = data.get("metadata")
        head.metadata = dict(meta) if isinstance(meta, Mapping) else {}
        return head

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "LogisticHead":
        return cls.from_dict(json.loads(text))


class LearnedValueHead(LogisticHead):
    """Logistic regression over `FEATURE_NAMES`, pure Python, JSON-portable.

    Trained offline on `(features, outcome)` pairs where outcome is 1 if the
    trajectory the features were recorded on ended with that question
    certified by the oracle. Small on purpose: with tens of firms per
    generation a larger model would memorise, and a head nobody can inspect
    is the thing this whole design is trying not to be.
    """

    FEATURES = FEATURE_NAMES


class HypothesisPriorHead(LogisticHead):
    """Logistic regression over `HYPOTHESIS_FEATURE_NAMES`: P(hypothesis survives its probe).

    Trained on experiment moves whose hypothesis ended with a verdict
    (SUPPORTED/CERTIFIED = 1, FALSIFIED = 0). In the loop its output is mixed
    into the PUCT prior with weight `prior_head_weight`; at the default
    weight 0 it is recorded (`MoveRecord.extra["head_p"]`) and changes nothing.
    """

    FEATURES = HYPOTHESIS_FEATURE_NAMES


# ---------------------------------------------------------------------------
# Loading heads from `EpistemicPolicyGene.extra` (shadow mode)
# ---------------------------------------------------------------------------

HEADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heads")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXTRA_VALUE_HEAD_PATH = "value_head_path"
EXTRA_PRIOR_HEAD_PATH = "prior_head_path"
EXTRA_PRIOR_HEAD_WEIGHT = "prior_head_weight"
EXTRA_VALUE_HEAD_LIVE = "value_head_live"


def resolve_head_path(spec: str) -> str:
    """Absolute paths as given; bare file names against `hae/epistemic/heads/`;
    other relative paths against the repo root, then the working directory."""
    spec = str(spec or "").strip()
    if not spec:
        return ""
    if os.path.isabs(spec):
        return spec
    candidates = []
    if os.sep not in spec and "/" not in spec:
        candidates.append(os.path.join(HEADS_DIR, spec))
    candidates.append(os.path.join(REPO_ROOT, spec))
    candidates.append(os.path.abspath(spec))
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def load_head(spec: str, kind: str) -> LogisticHead:
    """Reads a JSON head of `kind` ('value' | 'prior'); raises on any problem."""
    cls = {"value": LearnedValueHead, "prior": HypothesisPriorHead}[kind]
    path = resolve_head_path(spec)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, Mapping) or not isinstance(data.get("weights"), Mapping):
        raise ValueError(f"{path} is not a head file (no 'weights' mapping)")
    declared = data.get("features")
    if declared is not None and list(declared) != list(cls.FEATURES):
        raise ValueError(f"{path} was trained on {len(declared)} features, {cls.__name__} expects "
                         f"{len(cls.FEATURES)}: feature sets differ")
    head = cls.from_dict(data)
    head.metadata.setdefault("path", path)
    return head


@dataclass
class PolicyHeads:
    value_head: Optional[LearnedValueHead] = None
    prior_head: Optional[HypothesisPriorHead] = None
    prior_head_weight: float = 0.0
    value_head_live: bool = False
    errors: Dict[str, str] = field(default_factory=dict)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def load_policy_heads(extra: Optional[Mapping[str, Any]],
                      logger: Optional[Callable[[str], None]] = None) -> PolicyHeads:
    """Loads whatever heads `EpistemicPolicyGene.extra` configures. Never raises.

    Keys: `value_head_path`, `prior_head_path` (absolute, repo-relative, or a
    bare name resolved against `hae/epistemic/heads/`), `prior_head_weight`
    (0..1, default 0 = shadow only), `value_head_live` (default False = the
    value head is recorded as `extra["head_v"]` but `EpistemicValueFunction`
    keeps the heuristic). A head that fails to load is logged and skipped;
    a misconfigured firm searches exactly as an unconfigured one.

    In a genome file the keys sit directly inside `epistemic_policy` (the
    schema flattens `extra` on write and collects unknown keys on read).
    """
    log = logger or (lambda _msg: None)
    extra = dict(extra or {})
    nested = extra.pop("extra", None)
    if isinstance(nested, Mapping):
        # On disk the gene is flat (`_Model.to_dict` merges `extra` into the
        # record); a genome that nested the dict instead still gets its heads.
        extra = {**nested, **extra}
    heads = PolicyHeads()
    for key, kind, attr in ((EXTRA_VALUE_HEAD_PATH, "value", "value_head"),
                            (EXTRA_PRIOR_HEAD_PATH, "prior", "prior_head")):
        spec = extra.get(key)
        if not spec:
            continue
        try:
            setattr(heads, attr, load_head(str(spec), kind))
            log(f"{kind} head loaded from {resolve_head_path(str(spec))}")
        except Exception as exc:  # bad path, bad JSON, wrong feature set: never fatal
            heads.errors[key] = f"{type(exc).__name__}: {exc}"
            log(f"{kind} head NOT loaded ({spec!r}): {type(exc).__name__}: {exc}")
    try:
        w = float(extra.get(EXTRA_PRIOR_HEAD_WEIGHT, 0.0) or 0.0)
    except (TypeError, ValueError):
        heads.errors[EXTRA_PRIOR_HEAD_WEIGHT] = f"not numeric: {extra.get(EXTRA_PRIOR_HEAD_WEIGHT)!r}"
        w = 0.0
    if w != w:  # NaN
        w = 0.0
    heads.prior_head_weight = max(0.0, min(1.0, w))
    heads.value_head_live = _as_bool(extra.get(EXTRA_VALUE_HEAD_LIVE, False))
    if heads.value_head is None:
        heads.value_head_live = False
    return heads


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
