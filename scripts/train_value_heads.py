"""Train the first V7 heads from V6 epistemic trees, with grouped out-of-fold evaluation.

Two logistic heads (`hae/epistemic/value.py`), both pure Python:

  * the **state value head** (`LearnedValueHead`, 11 ledger features): one
    row per move, features = the ledger *before* the move (`MoveRecord.
    features`), label = 1 if the move's question ended CERTIFIED at the end
    of that tree. This is the end-of-tree label `scripts/extract_value_
    telemetry.py` documents as `terminal_question_status`; moves that name no
    question (`ask_question`) or a question absent from the final ledger are
    dropped and counted.
  * the **hypothesis prior head** (`HypothesisPriorHead`, 32 features): one
    row per `run_experiment` move whose hypothesis ended with a verdict
    (SUPPORTED or CERTIFIED -> 1, FALSIFIED -> 0; UNTESTABLE and UNVERIFIED
    are excluded), features = `hypothesis_features(question-at-that-time,
    hypothesis, siblings-at-that-time, move.features)`.

Reconstructing "at that time" from a finished tree (every approximation is
reported in the README the script writes):

  * siblings = the question's hypotheses with `created_at <= move.created_at`.
    `Hypothesis.created_at` is set when the proposal is registered and
    `MoveRecord.created_at` when the move is recorded, after the experiment
    ran; experiments create no hypotheses, so this is exact (0 violations in
    the Gen 16 trees). If a tree carries no timestamps the fallback is the
    numeric id rule -- hypotheses whose id number is <= the largest id any
    move up to this one has acted on -- which under-counts siblings proposed
    in the same round; the README says how many rows needed it.
  * the question's `uncertainty` is replayed: `initial_uncertainty`, minus
    each move's `delta_u` in move order (within an iteration that is exactly
    what the gatekeeper's `lower_uncertainty` did), with the oracle's reopen
    rule (`max(u, 0.75 * initial)`) applied when a question that was RESOLVED
    by a verified synthesis receives another move. The final value of the
    ledger would *leak the label* (a certified question has uncertainty 0),
    so this replay is not optional. The replayed terminal value is compared
    with the ledger's for every non-certified question and the match rate is
    reported.
  * `hypothesis_rounds` is the number of proposal rounds on the question in
    the move's iteration before the move (`run()` resets it per iteration).
  * `source_failure` text is the final one (the oracle re-writes it each
    iteration; the failure *class* is what the features use).

Evaluation: GROUP k-fold by `company_id` -- a firm is never split across
folds (both cohort runs of the same genome share a company_id and therefore
a fold), seed-fixed, greedy-balanced by row count. Reported out of fold:
Brier, log-loss and rank AUC (Mann-Whitney, ties averaged) for the head, the
training-fold base rate, and -- prior head -- the stated prior and a
rank-only logistic baseline; -- value head -- `heuristic_value`, which is
not a probability (its Brier/log-loss are shown for scale only). `l2` is
chosen from a grid by OOF log-loss, i.e. on the same folds that are reported,
which is mildly optimistic; the README says so. Fitting uses damped Newton
(`LogisticHead.fit_newton`) so every candidate reaches the optimum of the
same objective `mean log-loss + l2/2 ||w||^2`; `--optimizer gd` reproduces
the V6 gradient-descent fitter instead.

Outputs: `<out-dir>/v7_value_head.json`, `<out-dir>/v7_prior_head.json`
(weights + metadata: rows, positives, folds, OOF metrics, roots, git sha,
trained_at, feature names), `--report` (markdown) and `metrics.json` next to
it. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import math
import os
import random
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from hae.epistemic.ledger import (  # noqa: E402
    CERTIFIED, FALSIFIED, Q_CERTIFIED, SUPPORTED, Hypothesis, Question,
)
from hae.epistemic.value import (  # noqa: E402
    FEATURE_NAMES, HYPOTHESIS_FEATURE_NAMES, HypothesisPriorHead, LearnedValueHead, LogisticHead,
    heuristic_value, hypothesis_features,
)

_SPEC = importlib.util.spec_from_file_location("extract_value_telemetry", os.path.join(_HERE, "extract_value_telemetry.py"))
evt = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(evt)

MOVE_PROPOSE = "propose_hypothesis"
MOVE_EXPERIMENT = "run_experiment"
MOVE_SYNTHESIZE = "synthesize"
REOPEN_FRACTION = 0.75          # hae/epistemic/gatekeeper.py; kept literal, drift is caught by the replay check
POSITIVE_VERDICTS = (SUPPORTED, CERTIFIED)
L2_GRID_DEFAULT = (1e-3, 1e-2, 1e-1)
BINS = 5
EPS = 1e-6

VALUE_FEATURE_DOCS = {
    "resolved_fraction": "share of initial uncertainty already resolved by evidence",
    "certified_ratio": "share of questions the oracle has certified",
    "resolved_ratio": "share of questions patched and awaiting the oracle",
    "supported_ratio": "share of hypotheses that survived a probe",
    "ruled_out_ratio": "share of hypotheses falsified",
    "untested_ratio": "share of hypotheses still untested",
    "evidence_density": "evidence records per hypothesis (capped at 1)",
    "stagnation": "trailing zero-progress moves / 10",
    "open_without_options": "open questions with no live hypothesis",
    "module_isolation": "1 / distinct modules among open questions",
    "mean_open_uncertainty": "mean uncertainty of the open questions",
}
PRIOR_FEATURE_DOCS = {
    "stated_prior": "the proposer's declared confidence",
    "prior_rank": "where that prior ranks among the siblings (0 lowest, 1 highest)",
    "sibling_share": "1 / number of sibling hypotheses",
    "is_lowest_prior": "the least plausible sibling (the Move-37 pick)",
    "probe_lines": "probe length / 40 lines",
    "probe_guarded_import": "probe wraps an import in try/except ImportError",
    "probe_imports_module": "probe imports the module under question",
    "prediction_exit_code": "prediction states an exit code",
    "prediction_marker": "prediction states a stdout/stderr marker",
    "claim_length": "claim length / 200 characters",
    "question_uncertainty": "how much of the question was still open at selection",
    "question_rounds": "proposal rounds already spent on the question this iteration / 3",
    "oracle_seeded": "question came from an oracle failure (vs. asked by a proposer)",
    "fail_import": "oracle failure is an import / missing-module error (benchmark-specific)",
    "fail_name": "oracle failure is a NameError / AttributeError / UnboundLocalError (benchmark-specific)",
    "fail_assertion": "oracle failure is an AssertionError (benchmark-specific)",
    "fail_other": "oracle failure is of another class (benchmark-specific)",
    "mod_harness": "question targets hae/evaluation/harness.py (benchmark-specific)",
    "mod_verification_loop": "question targets hae/evaluation/verification_loop.py (benchmark-specific)",
    "mod_artifacts": "question targets hae/evaluation/artifacts.py (benchmark-specific; absent from Gen 16 trees)",
    "mod_morphogenesis": "question targets hae/genome/morphogenesis.py (benchmark-specific)",
}
PRIOR_FEATURE_DOCS.update({f"s_{k}": f"ledger state: {v}" for k, v in VALUE_FEATURE_DOCS.items()})


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

class Row:
    __slots__ = ("group", "tree", "move_index", "features", "label", "aux")

    def __init__(self, group: str, tree: str, move_index: Any, features: Mapping[str, float],
                 label: int, aux: Optional[Dict[str, Any]] = None) -> None:
        self.group, self.tree, self.move_index = group, tree, move_index
        self.features, self.label, self.aux = dict(features), int(label), dict(aux or {})


def _num_suffix(ident: str) -> int:
    digits = "".join(ch for ch in str(ident) if ch.isdigit())
    return int(digits) if digits else -1


def load_trees(roots: Sequence[str]) -> Tuple[List[Tuple[str, Dict[str, Any]]], int]:
    trees: List[Tuple[str, Dict[str, Any]]] = []
    unreadable = 0
    for label, path in evt.find_trees(roots):
        try:
            with open(path, encoding="utf-8") as fh:
                tree = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"warning: skipping unreadable {path}: {exc}", file=sys.stderr)
            unreadable += 1
            continue
        if isinstance(tree, dict) and "move_log" in tree and "ledger" not in tree:
            tree = {"company_id": tree.get("company_id", ""), "ledger": tree}
        if not isinstance(tree, dict) or not tree.get("ledger"):
            continue
        trees.append((label, tree))
    return trees, unreadable


def build_rows(trees: Sequence[Tuple[str, Dict[str, Any]]]) -> Tuple[List[Row], List[Row], Dict[str, Any]]:
    """Value rows, prior rows and the bookkeeping the README reports."""
    value_rows: List[Row] = []
    prior_rows: List[Row] = []
    book: Dict[str, Any] = {
        "moves": 0, "value_dropped_no_question": 0, "value_dropped_unknown_question": 0,
        "value_dropped_no_features": 0, "experiment_moves": 0, "prior_excluded_by_status": {},
        "prior_sibling_fallback_rows": 0, "prior_hypotheses_with_multiple_rows": 0,
        "replay_reopens": 0, "replay_questions_checked": 0, "replay_questions_matched": 0,
        "replay_questions_matched_with_reopen_allowance": 0, "iteration_unrecovered_moves": 0,
    }
    for label, tree in trees:
        ledger = tree.get("ledger") or {}
        company = str(tree.get("company_id") or ledger.get("company_id") or label)
        questions = {q.get("question_id"): q for q in ledger.get("questions") or []}
        hyps = {h.get("hypothesis_id"): h for h in ledger.get("hypotheses") or []}
        by_question: Dict[str, List[Dict[str, Any]]] = {}
        for h in hyps.values():
            by_question.setdefault(str(h.get("question_id")), []).append(h)
        timestamps_ok = all(float(h.get("created_at") or 0.0) > 0.0 for h in hyps.values())

        u_now = {qid: float(q.get("initial_uncertainty", 1.0)) for qid, q in questions.items()}
        resolved: set = set()
        rounds: Dict[Tuple[str, Any], int] = {}
        max_acted_id = -1
        rows_per_hypothesis: Dict[str, int] = {}
        for move, iteration in evt.iter_moves(tree):
            book["moves"] += 1
            if iteration is None:
                book["iteration_unrecovered_moves"] += 1
            qid = str(move.get("question_id") or "")
            mtype = str(move.get("move_type") or "")
            feats = move.get("features") or {}
            if not qid:
                book["value_dropped_no_question"] += 1
                continue
            q = questions.get(qid)
            if q is None:
                book["value_dropped_unknown_question"] += 1
                continue
            if not feats:
                book["value_dropped_no_features"] += 1
                continue
            # -- state value row
            value_rows.append(Row(company, label, move.get("move_index"), feats,
                                  1 if str(q.get("status")) == Q_CERTIFIED else 0,
                                  {"heuristic": heuristic_value(feats), "value_before": move.get("value_before")}))
            # -- replay the question's uncertainty / rounds up to this move
            if qid in resolved:
                u_now[qid] = max(u_now[qid], float(q.get("initial_uncertainty", 1.0)) * REOPEN_FRACTION)
                resolved.discard(qid)
                book["replay_reopens"] += 1
            u_before = u_now[qid]
            rounds_before = rounds.get((qid, iteration), 0)
            if mtype == MOVE_PROPOSE:
                rounds[(qid, iteration)] = rounds_before + 1
            hid = str(move.get("hypothesis_id") or "")
            if hid:
                max_acted_id = max(max_acted_id, _num_suffix(hid))
            # -- prior row
            if mtype == MOVE_EXPERIMENT:
                book["experiment_moves"] += 1
                h = hyps.get(hid)
                status = str(h.get("status", "")) if h else "(missing)"
                if h is None or status not in POSITIVE_VERDICTS + (FALSIFIED,):
                    book["prior_excluded_by_status"][status] = book["prior_excluded_by_status"].get(status, 0) + 1
                else:
                    sibs_raw = by_question.get(qid, [])
                    if timestamps_ok and float(move.get("created_at") or 0.0) > 0.0:
                        t_move = float(move["created_at"])
                        sibs = [s for s in sibs_raw if float(s.get("created_at") or 0.0) <= t_move]
                    else:
                        sibs = [s for s in sibs_raw if _num_suffix(str(s.get("hypothesis_id"))) <= max_acted_id]
                        book["prior_sibling_fallback_rows"] += 1
                    if all(s.get("hypothesis_id") != hid for s in sibs):
                        sibs = sibs + [h]
                    q_at = Question.from_dict({**q, "uncertainty": u_before, "hypothesis_rounds": rounds_before})
                    hf = hypothesis_features(q_at, Hypothesis.from_dict(h), [Hypothesis.from_dict(s) for s in sibs], feats)
                    prior_rows.append(Row(company, label, move.get("move_index"), hf,
                                          1 if status in POSITIVE_VERDICTS else 0,
                                          {"hypothesis_id": hid, "stated_prior": float(h.get("prior", 0.5)),
                                           "siblings": len(sibs), "terminal_status": status}))
                    rows_per_hypothesis[hid] = rows_per_hypothesis.get(hid, 0) + 1
            # -- advance the replay
            u_now[qid] = max(0.0, u_before - float(move.get("delta_u") or 0.0))
            if mtype == MOVE_SYNTHESIZE and "module check passed" in str(move.get("note") or ""):
                resolved.add(qid)
        book["prior_hypotheses_with_multiple_rows"] += sum(1 for n in rows_per_hypothesis.values() if n > 1)
        # -- replay consistency: the replayed terminal uncertainty vs. the ledger's
        for qid, q in questions.items():
            if str(q.get("status")) == Q_CERTIFIED:
                continue
            book["replay_questions_checked"] += 1
            final = float(q.get("uncertainty", 0.0))
            if abs(u_now[qid] - final) <= 1e-4:
                book["replay_questions_matched"] += 1
                book["replay_questions_matched_with_reopen_allowance"] += 1
            elif qid in resolved and abs(max(u_now[qid], float(q.get("initial_uncertainty", 1.0)) * REOPEN_FRACTION) - final) <= 1e-4:
                book["replay_questions_matched_with_reopen_allowance"] += 1
    return value_rows, prior_rows, book


# ---------------------------------------------------------------------------
# Metrics (pure Python)
# ---------------------------------------------------------------------------

def brier(preds: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    if not preds:
        return None
    return sum((float(p) - float(y)) ** 2 for p, y in zip(preds, labels)) / len(preds)


def log_loss(preds: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    if not preds:
        return None
    total = 0.0
    for p, y in zip(preds, labels):
        p = min(1.0 - EPS, max(EPS, float(p)))
        total -= math.log(p) if y else math.log(1.0 - p)
    return total / len(preds)


def auc(scores: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    """Rank AUC (Mann-Whitney), ties get the average rank."""
    n = len(scores)
    pos = sum(1 for y in labels if y)
    neg = n - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(n), key=lambda i: float(scores[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and float(scores[order[j + 1]]) == float(scores[order[i]]):
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    rank_sum = sum(ranks[i] for i in range(n) if labels[i])
    return (rank_sum - pos * (pos + 1) / 2.0) / float(pos * neg)


def auc_standard_error(a: Optional[float], n_pos: int, n_neg: int) -> Optional[float]:
    """Hanley & McNeil (1982); treats rows as independent, which they are not quite."""
    if a is None or n_pos == 0 or n_neg == 0:
        return None
    q1 = a / (2.0 - a)
    q2 = 2.0 * a * a / (1.0 + a)
    var = (a * (1.0 - a) + (n_pos - 1) * (q1 - a * a) + (n_neg - 1) * (q2 - a * a)) / float(n_pos * n_neg)
    return math.sqrt(max(var, 0.0))


def calibration_table(preds: Sequence[float], labels: Sequence[int], bins: int = BINS) -> List[Dict[str, Any]]:
    table = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(preds) if (lo <= p < hi) or (b == bins - 1 and p == hi)]
        n = len(idx)
        table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": n,
                      "mean_predicted": round(sum(preds[i] for i in idx) / n, 4) if n else None,
                      "observed_rate": round(sum(labels[i] for i in idx) / n, 4) if n else None})
    return table


def quantile_calibration(preds: Sequence[float], labels: Sequence[int], bins: int = BINS) -> List[Dict[str, Any]]:
    """Equal-count bins by predicted probability: readable when all predictions sit in one fixed bin."""
    order = sorted(range(len(preds)), key=lambda i: (preds[i], i))
    n = len(order)
    table = []
    for b in range(bins):
        idx = order[(b * n) // bins:((b + 1) * n) // bins]
        if not idx:
            continue
        ps = [preds[i] for i in idx]
        table.append({"bin": f"q{b + 1}", "n": len(idx), "predicted_range": f"{min(ps):.3f}-{max(ps):.3f}",
                      "mean_predicted": round(sum(ps) / len(idx), 4),
                      "observed_rate": round(sum(labels[i] for i in idx) / len(idx), 4)})
    return table


def metric_block(preds: Sequence[float], labels: Sequence[int]) -> Dict[str, Optional[float]]:
    return {"brier": _r(brier(preds, labels)), "log_loss": _r(log_loss(preds, labels)), "auc": _r(auc(preds, labels))}


def _r(x: Optional[float], digits: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), digits)


# ---------------------------------------------------------------------------
# Grouped cross-validation
# ---------------------------------------------------------------------------

def grouped_folds(rows: Sequence[Row], n_folds: int, seed: int) -> Dict[str, int]:
    """group -> fold. Groups are shuffled by `seed`, then greedily assigned (largest
    first) to the fold with the fewest rows, so a firm never spans two folds."""
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r.group] = counts.get(r.group, 0) + 1
    groups = sorted(counts)
    random.Random(seed).shuffle(groups)
    groups.sort(key=lambda g: -counts[g])   # stable: ties keep the shuffled order
    k = max(1, min(n_folds, len(groups)))
    load = [0] * k
    assignment: Dict[str, int] = {}
    for g in groups:
        f = min(range(k), key=lambda i: (load[i], i))
        assignment[g] = f
        load[f] += counts[g]
    return assignment


def fit_head(head: LogisticHead, samples: Sequence[Tuple[Mapping[str, float], float]], l2: float,
             optimizer: str, epochs: int, lr: float) -> LogisticHead:
    if optimizer == "gd":
        return head.fit(samples, epochs=epochs, lr=lr, l2=l2)
    return head.fit_newton(samples, l2=l2)


def oof_predictions(make_head, rows: Sequence[Row], folds: Mapping[str, int], l2: float,
                    optimizer: str, epochs: int, lr: float,
                    feature_filter: Optional[Sequence[str]] = None) -> List[float]:
    """Out-of-fold predictions, aligned with `rows`. `feature_filter` restricts the
    features seen by the model (used for the rank-only baseline)."""
    preds = [0.0] * len(rows)
    for f in sorted(set(folds.values())):
        train = [(r.features if feature_filter is None else {k: r.features.get(k, 0.0) for k in feature_filter}, r.label)
                 for r in rows if folds[r.group] != f]
        test_idx = [i for i, r in enumerate(rows) if folds[r.group] == f]
        if not train or not test_idx:
            continue
        head = fit_head(make_head(), train, l2, optimizer, epochs, lr)
        for i in test_idx:
            feats = rows[i].features if feature_filter is None else {k: rows[i].features.get(k, 0.0) for k in feature_filter}
            preds[i] = head.predict(feats)
    return preds


def base_rate_predictions(rows: Sequence[Row], folds: Mapping[str, int]) -> List[float]:
    preds = [0.0] * len(rows)
    for f in sorted(set(folds.values())):
        train = [r.label for r in rows if folds[r.group] != f]
        rate = sum(train) / len(train) if train else 0.5
        for i, r in enumerate(rows):
            if folds[r.group] == f:
                preds[i] = rate
    return preds


def per_fold_auc(preds: Sequence[float], rows: Sequence[Row], folds: Mapping[str, int]) -> List[Optional[float]]:
    out = []
    for f in sorted(set(folds.values())):
        idx = [i for i, r in enumerate(rows) if folds[r.group] == f]
        out.append(_r(auc([preds[i] for i in idx], [rows[i].label for i in idx])))
    return out


def feature_std(rows: Sequence[Row], names: Sequence[str]) -> Dict[str, float]:
    out = {}
    n = float(len(rows)) or 1.0
    for k in names:
        vals = [float(r.features.get(k, 0.0)) for r in rows]
        mean = sum(vals) / n
        out[k] = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    return out


def evaluate_head(name: str, make_head, rows: Sequence[Row], n_folds: int, seed: int,
                  l2_grid: Sequence[float], optimizer: str, epochs: int, lr: float,
                  baselines: Mapping[str, Sequence[float]],
                  rank_only_feature: Optional[str] = None) -> Dict[str, Any]:
    labels = [r.label for r in rows]
    n_pos = sum(labels)
    result: Dict[str, Any] = {
        "name": name, "rows": len(rows), "positives": n_pos, "negatives": len(rows) - n_pos,
        "base_rate": _r(n_pos / len(rows)) if rows else None,
        "groups": len({r.group for r in rows}), "folds": 0, "seed": seed, "optimizer": optimizer,
        "l2_grid": {}, "chosen_l2": None, "oof": {}, "baselines": {}, "calibration": [], "per_fold_auc": [],
        "evaluated": False,
    }
    groups = {r.group for r in rows}
    if len(groups) < 2 or n_pos == 0 or n_pos == len(rows):
        result["note"] = "not evaluated out of fold: fewer than 2 firms or a single class"
        return result
    folds = grouped_folds(rows, n_folds, seed)
    k = len(set(folds.values()))
    result["folds"] = k
    result["fold_sizes"] = [sum(1 for r in rows if folds[r.group] == f) for f in range(k)]
    best_l2, best_ll, best_preds = None, None, None
    for l2 in l2_grid:
        preds = oof_predictions(make_head, rows, folds, l2, optimizer, epochs, lr)
        block = metric_block(preds, labels)
        result["l2_grid"][repr(l2)] = block
        if best_ll is None or (block["log_loss"] is not None and block["log_loss"] < best_ll):
            best_l2, best_ll, best_preds = l2, block["log_loss"], preds
    assert best_preds is not None
    result["chosen_l2"] = best_l2
    result["oof"] = metric_block(best_preds, labels)
    result["oof"]["auc_se_hanley_mcneil"] = _r(auc_standard_error(auc(best_preds, labels), n_pos, len(rows) - n_pos))
    result["per_fold_auc"] = per_fold_auc(best_preds, rows, folds)
    result["calibration"] = calibration_table(best_preds, labels)
    result["calibration_quintiles"] = quantile_calibration(best_preds, labels)
    result["oof_predictions"] = [round(p, 6) for p in best_preds]
    result["baselines"]["base_rate"] = metric_block(base_rate_predictions(rows, folds), labels)
    for bname, scores in baselines.items():
        result["baselines"][bname] = metric_block(list(scores), labels)
    if rank_only_feature:
        preds = oof_predictions(make_head, rows, folds, best_l2, optimizer, epochs, lr, feature_filter=[rank_only_feature])
        result["baselines"][f"{rank_only_feature}_only_logistic"] = metric_block(preds, labels)
    result["evaluated"] = True
    return result


def verdict(result: Mapping[str, Any]) -> Dict[str, Any]:
    """Signal out of fold = beats the base-rate Brier AND AUC exceeds 0.5 by a stated margin."""
    if not result.get("evaluated"):
        return {"signal": None, "reason": result.get("note", "not evaluated")}
    oof, base = result["oof"], result["baselines"]["base_rate"]
    se = oof.get("auc_se_hanley_mcneil") or 0.0
    margin = max(0.05, 2.0 * se)
    beats_brier = oof["brier"] is not None and base["brier"] is not None and oof["brier"] < base["brier"]
    auc_ok = oof["auc"] is not None and (oof["auc"] - 0.5) > margin
    folds_above_half = sum(1 for a in result["per_fold_auc"] if a is not None and a > 0.5)
    return {
        "signal": bool(beats_brier and auc_ok),
        "beats_base_rate_brier": bool(beats_brier),
        "brier_gain": _r((base["brier"] or 0.0) - (oof["brier"] or 0.0)),
        "auc_margin_required": _r(margin), "auc_margin_observed": _r((oof["auc"] or 0.5) - 0.5),
        "auc_above_margin": bool(auc_ok),
        "folds_with_auc_above_half": f"{folds_above_half}/{len(result['per_fold_auc'])}",
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(x: Any, digits: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def metrics_table(result: Mapping[str, Any], head_label: str, baseline_labels: Mapping[str, str]) -> List[str]:
    lines = ["| predictor | Brier ↓ | log-loss ↓ | AUC ↑ |", "|---|---|---|---|"]
    o = result["oof"]
    lines.append(f"| **{head_label}** (l2 = {result['chosen_l2']}) | **{_fmt(o['brier'])}** | **{_fmt(o['log_loss'])}** | **{_fmt(o['auc'])}** |")
    for key, label in baseline_labels.items():
        b = result["baselines"].get(key)
        if b:
            auc_cell = "n/a (constant within a fold)" if key == "base_rate" else _fmt(b["auc"])
            lines.append(f"| {label} | {_fmt(b['brier'])} | {_fmt(b['log_loss'])} | {auc_cell} |")
    return lines


def l2_table(result: Mapping[str, Any]) -> List[str]:
    lines = ["| l2 | OOF log-loss | OOF Brier | OOF AUC |", "|---|---|---|---|"]
    for l2, block in result["l2_grid"].items():
        mark = " ← chosen" if float(l2) == float(result["chosen_l2"]) else ""
        lines.append(f"| {l2} | {_fmt(block['log_loss'])} | {_fmt(block['brier'])} | {_fmt(block['auc'])}{mark} |")
    return lines


def calibration_lines(table: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = ["| predicted bin | n | mean predicted | observed rate |", "|---|---|---|---|"]
    for b in table:
        lines.append(f"| {b['bin']} | {b['n']} | {_fmt(b['mean_predicted'])} | {_fmt(b['observed_rate'])} |")
    return lines


def quintile_lines(table: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = ["| quintile of predicted p | n | predicted range | mean predicted | observed rate |", "|---|---|---|---|---|"]
    for b in table:
        lines.append(f"| {b['bin']} | {b['n']} | {b['predicted_range']} | {_fmt(b['mean_predicted'])} | {_fmt(b['observed_rate'])} |")
    return lines


def weight_lines(head: LogisticHead, stds: Mapping[str, float], docs: Mapping[str, str], top: int = 6) -> List[str]:
    items = sorted(head.weights.items(), key=lambda kv: kv[1])
    neg = [kv for kv in items if kv[1] < 0][:top]
    pos = [kv for kv in reversed(items) if kv[1] > 0][:top]
    lines = [f"bias = {head.bias:+.3f} (log-odds at all-zero features)", "",
             "| direction | feature | weight | feature std | weight × std | reads as |", "|---|---|---|---|---|---|"]
    for sign, group in (("+", pos), ("−", neg)):
        for k, w in group:
            lines.append(f"| {sign} | `{k}` | {w:+.3f} | {stds.get(k, 0.0):.3f} | {w * stds.get(k, 0.0):+.3f} | {docs.get(k, '')} |")
    return lines


def verdict_lines(v: Mapping[str, Any], head_label: str) -> List[str]:
    if v.get("signal") is None:
        return [f"**Verdict: {head_label} not evaluated** ({v.get('reason')})."]
    word = "carries signal out of fold" if v["signal"] else "does **not** carry usable signal out of fold"
    return [f"**Verdict: the {head_label} {word}.** "
            f"Base-rate Brier beaten: {'yes' if v['beats_base_rate_brier'] else 'no'} (gain {_fmt(v['brier_gain'])}); "
            f"AUC − 0.5 = {_fmt(v['auc_margin_observed'])} against a required margin of {_fmt(v['auc_margin_required'])} "
            f"(max(0.05, 2 × Hanley–McNeil SE)): {'met' if v['auc_above_margin'] else 'not met'}; "
            f"folds with AUC > 0.5: {v['folds_with_auc_above_half']}."]


def render_report(ctx: Mapping[str, Any]) -> str:
    book, vres, pres = ctx["book"], ctx["value"], ctx["prior"]
    vhead, phead = ctx["value_head"], ctx["prior_head"]
    L: List[str] = []
    L += [f"# V7 heads — first learned state-value and hypothesis-prior heads (shadow mode)", "",
          f"*Generated by `scripts/train_value_heads.py` on {ctx['trained_at']} at git `{ctx['git_sha'][:12]}`; "
          f"command: `{ctx['command']}`.*", "",
          "This is a **measurement**, not a claimed lift. Both heads are logistic regressions in pure Python over the "
          "feature vectors already recorded on every `MoveRecord` (plus, for the prior head, hypothesis-level features "
          "reconstructed from the ledger). They are shipped **in shadow mode**: `EpistemicPolicyGene.extra` can load "
          "them (`value_head_path`, `prior_head_path`), but with the defaults `prior_head_weight = 0` and "
          "`value_head_live = false` they only *record* what they would have said (`MoveRecord.extra`) and change no "
          "decision. The out-of-fold numbers below decide whether it is worth turning them on for a cohort.", "",
          "Which decision could each head change? Today `EpistemicValueFunction` is evaluated only for the "
          "`value_before` / `value_after` telemetry on each move: no selection rule reads it and the stopping rules "
          "use `delta_u` (verified in `hae/epistemic/mcts.py`), so even `value_head_live = true` changes only the "
          "recorded values. The one decision a learned head can change *now* is the PUCT prior `P` over a question's "
          "untested hypotheses, which is what the prior head is for — so its out-of-fold result is the one that "
          "matters for behaviour, and the value head's is a measurement of how predictable certification is from "
          "ledger state.", ""]
    L += ["## 1. Data provenance", "",
          f"- roots: {', '.join(f'`{r}`' for r in ctx['roots'])}",
          f"- trees: **{ctx['trees']}** (`*_epistemic_tree.json`; unreadable skipped: {ctx['unreadable']}), "
          f"firms (`company_id`): **{ctx['groups']}** — the two cohort runs re-ran the same 26 genomes, so one firm "
          f"usually contributes two trees and they always share a fold",
          f"- moves: **{book['moves']}**; iteration unrecovered: {book['iteration_unrecovered_moves']}",
          "- all trees come from **one task family** (`configs/tasks/full_stack_hae.json`, Gen 16 cohort runs 1 and 2); "
          "the failure-class and module one-hots are benchmark-specific by construction.", ""]
    L += ["## 2. Rows", "", "### 2.1 State-value rows (one per move)", "",
          f"- rows: **{vres['rows']}**, positives (question ended CERTIFIED): **{vres['positives']}** "
          f"(base rate {_fmt(vres['base_rate'])})",
          f"- dropped: {book['value_dropped_no_question']} without a question (`ask_question`), "
          f"{book['value_dropped_unknown_question']} naming a question absent from the final ledger, "
          f"{book['value_dropped_no_features']} without a feature vector",
          "- features: `MoveRecord.features` = `epistemic_features(state)` **before** the move (11 features); "
          "label: the move's question's end-of-tree status (`terminal_question_status == CERTIFIED`). This is "
          "end-of-tree credit, not causal credit: every move on a question that was eventually certified is a "
          "positive, including the ones that resolved nothing.", "",
          "### 2.2 Prior-head rows (one per `run_experiment` move with a verdict)", "",
          f"- experiment moves: {book['experiment_moves']}; rows kept: **{pres['rows']}**, positives (hypothesis ended "
          f"SUPPORTED or CERTIFIED): **{pres['positives']}** (base rate {_fmt(pres['base_rate'])})",
          f"- excluded by terminal hypothesis status: " + ", ".join(f"{k} = {v}" for k, v in sorted(book['prior_excluded_by_status'].items())) + " (no verdict, so no label)",
          f"- hypotheses contributing more than one row (probe refused, repaired, re-run): {book['prior_hypotheses_with_multiple_rows']} "
          "— each selection event is a row, so these rows are correlated",
          "- siblings-at-that-time = the question's hypotheses with `created_at <= move.created_at` (hypotheses are "
          "timestamped when registered, moves when recorded after the experiment ran, and experiments register no "
          f"hypotheses, so this is exact); rows that needed the numeric-id fallback: {book['prior_sibling_fallback_rows']}",
          f"- the question's uncertainty at selection is **replayed** (`initial − Σ delta_u`, oracle reopen rule "
          f"`max(u, 0.75·initial)` applied {book['replay_reopens']}× when a RESOLVED question got another move) because the "
          "ledger's final value would leak the label; replayed terminal values match the ledger for "
          f"**{book['replay_questions_matched']}/{book['replay_questions_checked']}** non-certified questions "
          f"({book['replay_questions_matched_with_reopen_allowance']}/{book['replay_questions_checked']} allowing for a "
          "reopen after the question's last move)",
          "- `hypothesis_rounds` = proposal rounds on the question earlier in the same iteration; `source_failure` "
          "is the final oracle text (only its class is used).", ""]
    L += ["## 3. Protocol", "",
          f"- GROUP {vres['folds'] or ctx['n_folds']}-fold by `company_id`, seed {ctx['seed']}, greedy-balanced by rows "
          f"(value-row fold sizes {vres.get('fold_sizes')}; prior-row fold sizes {pres.get('fold_sizes')})",
          f"- optimiser: {ctx['optimizer']} on `mean log-loss + l2/2·‖w‖²` (bias unpenalised); features kept raw in [0, 1] with a bias",
          f"- `l2` chosen from {list(ctx['l2_grid'])} by OOF log-loss **on the same folds that are reported** — the "
          "reported numbers are therefore mildly optimistic (one of three candidates was picked by looking at them)",
          "- AUC is the rank AUC (Mann–Whitney, ties averaged); the Hanley–McNeil SE treats rows as independent, "
          "which they are not (moves of one firm share outcomes), so read the per-fold spread as well", ""]
    # value head
    L += ["## 4. State-value head (`v7_value_head.json`, 11 features)", "", "### 4.1 l2 selection", ""] + l2_table(vres) + [""]
    L += ["### 4.2 Out-of-fold metrics", ""] + metrics_table(vres, "learned value head", {
        "base_rate": "base rate of the training folds (constant)",
        "heuristic_value": "`heuristic_value` (V6 hand-set head; **not a probability** — Brier/log-loss for scale only)"}) + [""]
    L += [f"Per-fold AUC: {', '.join(_fmt(a) for a in vres['per_fold_auc'])}.", "", "### 4.3 Out-of-fold calibration", ""] + calibration_lines(vres["calibration"]) + [""]
    L += ["Same predictions by quintile (equal-count bins):", ""] + quintile_lines(vres.get("calibration_quintiles", [])) + [""]
    L += ["### 4.4 Weights (refit on everything)", ""] + weight_lines(vhead, ctx["value_std"], VALUE_FEATURE_DOCS) + ["",
          "Weights are on raw [0, 1] features; `weight × std` is the comparable size of each term.", ""]
    L += verdict_lines(ctx["value_verdict"], "state-value head") + ["",
          "Reading the signs: the label is *per question* (will **this** question be certified), not a state score. "
          "Negative weights on `certified_ratio` and `ruled_out_ratio` are survivorship — the questions still open in a "
          "mostly-certified ledger are the hard ones — and are also why the V6 `heuristic_value`, which rewards those "
          "very features, ranks *below* chance here: it scores how far the firm has come, not whether the question in hand will close.", ""]
    # prior head
    L += ["## 5. Hypothesis-prior head (`v7_prior_head.json`, 32 features)", "", "### 5.1 l2 selection", ""] + l2_table(pres) + [""]
    L += ["### 5.2 Out-of-fold metrics", ""] + metrics_table(pres, "learned prior head", {
        "base_rate": "base rate of the training folds (constant)",
        "stated_prior": "the proposer's stated prior, as is",
        "prior_rank_only_logistic": "rank-only baseline: logistic regression on `prior_rank` alone, fit in-fold"}) + [""]
    L += [f"Per-fold AUC: {', '.join(_fmt(a) for a in pres['per_fold_auc'])}.", "", "### 5.3 Out-of-fold calibration", ""] + calibration_lines(pres["calibration"]) + [""]
    L += ["Same predictions by quintile (equal-count bins):", ""] + quintile_lines(pres.get("calibration_quintiles", [])) + [""]
    L += ["### 5.4 Weights (refit on everything)", ""] + weight_lines(phead, ctx["prior_std"], PRIOR_FEATURE_DOCS) + ["",
          "Weights are on raw [0, 1] features; `weight × std` is the comparable size of each term. Features with zero "
          "variance in this data (e.g. `mod_artifacts`) keep weight ≈ 0 under l2 and say nothing.", ""]
    L += verdict_lines(ctx["prior_verdict"], "hypothesis-prior head") + [""]
    L += ["## 6. Caveats", "",
          f"- **n = {ctx['trees']} trees from {ctx['groups']} firms, one task family.** The one-hots for failure class "
          "and module are Gen 16 specific; on a different benchmark they are all zero and the head degrades to its "
          "structural features. Nothing here has been shown to transfer.",
          "- Labels are end-of-tree statuses. A hypothesis SUPPORTED by its probe and later FALSIFIED by the oracle is "
          "a negative on its experiment row; a question CERTIFIED for free by a sibling's patch makes every move on it a positive.",
          "- Rows are correlated within a firm and within a question; the grouped folds protect the *firm* level only.",
          "- The l2 choice looked at the reported folds (mild optimism). No feature selection was done after seeing OOF numbers.",
          "- **Shadow mode only.** Nothing in the search changes until a cohort run with `prior_head_weight > 0` shows "
          "the head beats the stated prior on *fresh* firms (`MoveRecord.extra` and the `prior_head_steered` stat are "
          "there to make that comparison from the same trees).", ""]
    L += ["## 7. Using the heads", "", "```json",
          '"epistemic_policy": {"enabled": true,',
          '    "value_head_path": "v7_value_head.json", "prior_head_path": "v7_prior_head.json",',
          '    "prior_head_weight": 0.0, "value_head_live": false}',
          "```", "",
          "The keys sit directly inside `epistemic_policy` in a genome file: the schema keeps unknown keys in "
          "`EpistemicPolicyGene.extra` on read and flattens them back on write (a nested `\"extra\": {...}` is "
          "accepted too). "
          "Bare names resolve against `hae/epistemic/heads/`; repo-relative and absolute paths work too. A head that "
          "fails to load is logged (`[epistemic] <firm> prior head NOT loaded ...`) and the firm searches exactly as an "
          "unconfigured one. With a prior head loaded every experiment move records `extra = {head_p, head_rank, "
          "stated_rank, prior_head_weight}`; with a value head loaded every move records `extra.head_v`; "
          "`scripts/extract_value_telemetry.py` emits them as `x_*` columns. `prior_head_loaded`, `value_head_loaded` "
          "and `prior_head_steered` appear in the search stats.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO, capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def main(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--roots", nargs="+", required=True, help="results roots to scan for *_epistemic_tree.json")
    parser.add_argument("--out-dir", default=os.path.join("hae", "epistemic", "heads"))
    parser.add_argument("--report", default=os.path.join("results", "v7_value_heads", "README.md"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--l2-grid", default=",".join(repr(x) for x in L2_GRID_DEFAULT))
    parser.add_argument("--optimizer", choices=("newton", "gd"), default="newton")
    parser.add_argument("--epochs", type=int, default=2000, help="gd only")
    parser.add_argument("--lr", type=float, default=0.5, help="gd only")
    args = parser.parse_args(list(argv) if argv is not None else None)
    l2_grid = [float(x) for x in args.l2_grid.split(",") if x.strip()]
    command = "python3 scripts/train_value_heads.py " + " ".join(list(argv) if argv is not None else sys.argv[1:])

    trees, unreadable = load_trees(args.roots)
    value_rows, prior_rows, book = build_rows(trees)
    groups = {r.group for r in value_rows} | {r.group for r in prior_rows}

    vres = evaluate_head("value", LearnedValueHead, value_rows, args.folds, args.seed, l2_grid, args.optimizer,
                         args.epochs, args.lr, baselines={"heuristic_value": [r.aux["heuristic"] for r in value_rows]})
    pres = evaluate_head("prior", HypothesisPriorHead, prior_rows, args.folds, args.seed, l2_grid, args.optimizer,
                         args.epochs, args.lr, baselines={"stated_prior": [r.aux["stated_prior"] for r in prior_rows]},
                         rank_only_feature="prior_rank")
    v_verdict, p_verdict = verdict(vres), verdict(pres)

    trained_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = git_sha()

    def refit(make_head, rows: Sequence[Row], res: Mapping[str, Any], label: str, names: Sequence[str]) -> LogisticHead:
        l2 = res["chosen_l2"] if res.get("chosen_l2") is not None else l2_grid[0]
        head = fit_head(make_head(), [(r.features, r.label) for r in rows], l2, args.optimizer, args.epochs, args.lr)
        head.metadata = {
            "kind": label, "version": "v7", "trained_at": trained_at, "git_sha": sha, "roots": list(args.roots),
            "trees": len(trees), "firms": len(groups), "rows": len(rows), "positives": sum(r.label for r in rows),
            "label": ("question ended CERTIFIED at end of tree" if label == "value"
                      else "hypothesis ended SUPPORTED/CERTIFIED (1) vs FALSIFIED (0)"),
            "folds": res.get("folds", 0), "seed": args.seed, "grouping": "company_id",
            "l2": l2, "l2_grid": {k: v for k, v in res.get("l2_grid", {}).items()}, "optimizer": args.optimizer,
            "oof": dict(res.get("oof", {})), "per_fold_auc": list(res.get("per_fold_auc", [])),
            "baselines_oof": dict(res.get("baselines", {})), "calibration_oof": list(res.get("calibration", [])),
            "verdict": v_verdict if label == "value" else p_verdict,
            "feature_names": list(names), "shadow_mode_default": True,
        }
        return head

    vhead = refit(LearnedValueHead, value_rows, vres, "value", FEATURE_NAMES)
    phead = refit(HypothesisPriorHead, prior_rows, pres, "prior", HYPOTHESIS_FEATURE_NAMES)

    os.makedirs(args.out_dir, exist_ok=True)
    paths = {"value_head": os.path.join(args.out_dir, "v7_value_head.json"),
             "prior_head": os.path.join(args.out_dir, "v7_prior_head.json")}
    for key, head in (("value_head", vhead), ("prior_head", phead)):
        with open(paths[key], "w", encoding="utf-8") as fh:
            json.dump(head.to_dict(), fh, indent=1, sort_keys=True)
            fh.write("\n")

    ctx = {
        "roots": list(args.roots), "trees": len(trees), "unreadable": unreadable, "groups": len(groups),
        "book": book, "value": vres, "prior": pres, "value_head": vhead, "prior_head": phead,
        "value_std": feature_std(value_rows, FEATURE_NAMES), "prior_std": feature_std(prior_rows, HYPOTHESIS_FEATURE_NAMES),
        "value_verdict": v_verdict, "prior_verdict": p_verdict, "trained_at": trained_at, "git_sha": sha,
        "command": command, "n_folds": args.folds, "seed": args.seed, "l2_grid": l2_grid, "optimizer": args.optimizer,
    }
    report = render_report(ctx)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(report)
    metrics = {k: v for k, v in ctx.items() if k not in ("value_head", "prior_head")}
    metrics["value"] = {k: v for k, v in vres.items() if k != "oof_predictions"}
    metrics["prior"] = {k: v for k, v in pres.items() if k != "oof_predictions"}
    metrics["heads"] = paths
    with open(os.path.join(os.path.dirname(os.path.abspath(args.report)), "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=1, sort_keys=True, default=str)

    print(f"trees={len(trees)} firms={len(groups)} value_rows={len(value_rows)} (+{vres['positives']}) "
          f"prior_rows={len(prior_rows)} (+{pres['positives']})")
    for label, res, v in (("value", vres, v_verdict), ("prior", pres, p_verdict)):
        if res.get("evaluated"):
            o, b = res["oof"], res["baselines"]["base_rate"]
            print(f"{label:>6}: l2={res['chosen_l2']} OOF brier={o['brier']} (base {b['brier']}) "
                  f"logloss={o['log_loss']} (base {b['log_loss']}) auc={o['auc']} signal={v['signal']}")
        else:
            print(f"{label:>6}: {res.get('note')}")
    print(f"heads: {paths['value_head']}, {paths['prior_head']}; report: {args.report}")
    return {"value": vres, "prior": pres, "book": book, "paths": paths, "report": args.report,
            "value_verdict": v_verdict, "prior_verdict": p_verdict}


if __name__ == "__main__":
    main()
