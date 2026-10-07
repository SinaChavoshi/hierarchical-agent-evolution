"""Flatten V6 epistemic trees into a first V7 value-head dataset.

V7 (experiments/v6_epistemic_search/README.md, section 5) is meant to learn a
value head from certified trajectories. Every `MoveRecord` already stores the
ledger's feature vector before the move and the value before and after it;
this script joins those records to how things actually ended, and adds a
small honesty check on System 1 (stated priors vs. gatekeeper verdicts).
Standard library only (argparse, csv, json, os, sys).

Input: every `*_epistemic_tree.json` under the `--root` directories, as
written by `hae/orchestration/worker.py`:

    {"company_id": str, "generation": int,
     "ledger":   EpistemicState.to_dict(),   # questions, hypotheses, ..., move_log
     "searches": [SearchResult.to_dict() + {"iteration": n}, ...],
     "audit": {...}, "policy": {...}}

`ledger.move_log` is cumulative: the ledger persists across repair iterations
and each iteration's `EpistemicSearchLoop` appends to it; `searches[i].
trajectory` is the slice produced in iteration `searches[i].iteration` (in
every Gen 16 pilot tree the concatenated trajectories equal the move log
exactly). A move's iteration is recovered by matching `(move_index,
state_hash_before)` against the trajectories and left blank if none claims
it. A tree without a ledger falls back to the trajectories (then no terminal
status is known); a bare `EpistemicState.to_dict()` is accepted as a tree
with no `searches`.

Output: one CSV row per move -- tree path, company_id, generation, iteration,
move_index, move_type, agent_role, question_id, hypothesis_id,
forced_low_prior (1/0), delta_u, value_before, value_after, one `f_<name>`
column per feature (`hae.epistemic.value.FEATURE_NAMES`, discovered from the
data so a schema change adds columns instead of dropping them), the
hypothesis' stated prior, the terminal columns and -- only when some move
carries `MoveRecord.extra` (V7 shadow-mode heads) -- one `x_<key>` column per
extra key (`x_head_p`, `x_head_rank`, `x_stated_rank`, `x_prior_head_weight`,
`x_head_v`; empty on moves without it) and -- only when some move carries a
non-empty `MoveRecord.role_id` (V8 organisation-routed searches) -- a trailing
`role_id` column: on PROPOSE/SYNTHESIZE the role the move was routed to, on
RUN_EXPERIMENT the role that *proposed* the hypothesis (the credited role), on
RECRUIT_SPECIALIST the role hired. `recruit_specialist` moves are rows like
any other (`delta_u` 0). Pre-V7 and non-organisation trees produce the same
CSV as before. When the column is present the summary also carries
`moves_by_role` (rows per role_id).

`terminal_outcome` -- exactly how it is derived:
  * if the move names a hypothesis present in the final ledger: that
    hypothesis' `status` at the end of the tree (CERTIFIED, SUPPORTED,
    FALSIFIED, UNTESTABLE or UNVERIFIED), also in `terminal_hypothesis_status`;
  * else, if it names a question present in the final ledger: that question's
    final `status` (CERTIFIED, RESOLVED, EXHAUSTED or OPEN). This is also in
    `terminal_question_status`, filled for *every* move naming a question so
    the question-level label is always available;
  * else empty (an `ask_question` move, or a tree without a ledger).
This is end-of-tree status, not causal credit: a hypothesis SUPPORTED by its
probe in iteration 2 and FALSIFIED by the oracle in iteration 3 reads
FALSIFIED on every row, including the experiment that supported it, and a
question's CERTIFIED says nothing about which move earned it. V7 still has to
define a return (discounted `delta_u`, a per-question oracle label, ...);
this file is the raw material, not the label.

Calibration (stdout and `--json`): hypotheses -- not moves; one hypothesis may
be probed many times -- binned by stated prior into 0.0-0.2 ... 0.8-1.0 (last
bin closed). Among *tested* hypotheses (final status SUPPORTED, FALSIFIED or
CERTIFIED, i.e. `Hypothesis.tested`) the hit rate is the fraction ending
SUPPORTED or CERTIFIED and the Brier score is mean (prior - hit)^2, matching
`calibration_report` in `hae/epistemic/value.py`. UNVERIFIED and UNTESTABLE
hypotheses never got a verdict: counted, not scored. A hypothesis counts once
per tree; a firm re-run in another pass is a different tree.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

DEFAULT_ROOT = "results/hae_gen16_v6_pilot"
TREE_SUFFIX = "_epistemic_tree.json"

# Mirrors hae/epistemic/ledger.py; kept literal so the script runs on a bare
# results directory. tests/test_extract_value_telemetry.py checks for drift.
TESTED_STATUSES = ("SUPPORTED", "FALSIFIED", "CERTIFIED")
POSITIVE_STATUSES = ("SUPPORTED", "CERTIFIED")

HEAD_COLUMNS = ("tree", "company_id", "generation", "iteration", "move_index", "move_type",
                "agent_role", "question_id", "hypothesis_id", "forced_low_prior",
                "delta_u", "value_before", "value_after")
TAIL_COLUMNS = ("hypothesis_prior", "terminal_hypothesis_status",
                "terminal_question_status", "terminal_outcome")
BIN_EDGES = [(i / 5.0, (i + 1) / 5.0) for i in range(5)]


def find_trees(roots: Iterable[str]) -> List[Tuple[str, str]]:
    """(label, path) for every tree file under the roots; label is root-relative."""
    found: List[Tuple[str, str]] = []
    for root in roots:
        base = os.path.basename(os.path.normpath(root))
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(TREE_SUFFIX):
                    path = os.path.join(dirpath, name)
                    found.append((os.path.join(base, os.path.relpath(path, root)), path))
    return sorted(found)


def iter_moves(tree: Dict[str, Any]) -> Iterator[Tuple[Dict[str, Any], Optional[int]]]:
    """Yields (move, iteration) from the cumulative move log, trajectories as fallback."""
    ledger = tree.get("ledger") or {}
    searches = tree.get("searches") or []
    claimed: Dict[Tuple[Any, Any], Any] = {}
    for search in searches:
        for move in search.get("trajectory") or []:
            claimed[(move.get("move_index"), move.get("state_hash_before", ""))] = search.get("iteration")
    log = ledger.get("move_log") or []
    if log:
        for move in log:
            yield move, claimed.get((move.get("move_index"), move.get("state_hash_before", "")))
        return
    for search in searches:
        for move in search.get("trajectory") or []:
            yield move, search.get("iteration")


def flatten_tree(tree: Dict[str, Any], label: str, feature_names: List[str],
                 extra_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """One row per move. Extends `feature_names` (and `extra_names`) in place with any new key.

    `extra_names` collects the keys of `MoveRecord.extra` (V7 shadow telemetry:
    `head_p`, `head_rank`, `stated_rank`, `prior_head_weight`, `head_v`),
    emitted as `x_<key>` columns. Pre-V7 trees have no `extra`, so nothing is
    discovered and their CSV is unchanged.
    """
    ledger = tree.get("ledger") or {}
    hyps = {h.get("hypothesis_id"): h for h in ledger.get("hypotheses") or []}
    questions = {q.get("question_id"): q for q in ledger.get("questions") or []}
    rows: List[Dict[str, Any]] = []
    for move, iteration in iter_moves(tree):
        h = hyps.get(move.get("hypothesis_id") or "")
        q = questions.get(move.get("question_id") or "")
        h_status = str(h.get("status", "")) if h else ""
        q_status = str(q.get("status", "")) if q else ""
        row: Dict[str, Any] = {
            "tree": label,
            "company_id": tree.get("company_id") or ledger.get("company_id", ""),
            "generation": tree.get("generation", ""),
            "iteration": "" if iteration is None else iteration,
            "move_index": move.get("move_index", ""), "move_type": move.get("move_type", ""),
            "agent_role": move.get("agent_role", ""),
            "question_id": move.get("question_id", ""), "hypothesis_id": move.get("hypothesis_id", ""),
            "forced_low_prior": int(bool(move.get("forced_low_prior", False))),
            "delta_u": move.get("delta_u", ""), "value_before": move.get("value_before", ""),
            "value_after": move.get("value_after", ""),
            "hypothesis_prior": h.get("prior", "") if h else "",
            "terminal_hypothesis_status": h_status, "terminal_question_status": q_status,
            "terminal_outcome": h_status or q_status,
        }
        for name, value in (move.get("features") or {}).items():
            if name not in feature_names:
                feature_names.append(name)
            row[f"f_{name}"] = value
        extra = move.get("extra") or {}
        if extra_names is not None and isinstance(extra, dict):
            for name, value in extra.items():
                if name not in extra_names:
                    extra_names.append(name)
                row[f"x_{name}"] = value
        # V8: present on the row only when the move was routed to / credited
        # to a role, so trees without an organisation keep their exact columns.
        role_id = str(move.get("role_id") or "")
        if role_id:
            row["role_id"] = role_id
        rows.append(row)
    return rows


def calibration(hypotheses: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Stated prior vs. gatekeeper verdict over tested hypotheses, binned, plus Brier.

    `brier_of_base_rate` is what a predictor that ignores System 1 and always
    says the pooled hit rate would score: priors are informative only below it.
    """
    bins = [{"bin": f"{lo:.1f}-{hi:.1f}", "tested": 0, "hits": 0, "prior_sum": 0.0} for lo, hi in BIN_EDGES]
    by_status: Dict[str, int] = {}
    tested = hits = 0
    squared_error = 0.0
    for h in hypotheses:
        status = str(h.get("status", ""))
        by_status[status] = by_status.get(status, 0) + 1
        if status not in TESTED_STATUSES:
            continue
        prior = max(0.0, min(1.0, float(h.get("prior", 0.5))))
        hit = 1 if status in POSITIVE_STATUSES else 0
        b = bins[next((i for i, (_lo, hi) in enumerate(BIN_EDGES) if prior < hi), len(BIN_EDGES) - 1)]
        b["tested"], b["hits"], b["prior_sum"] = b["tested"] + 1, b["hits"] + hit, b["prior_sum"] + prior
        tested, hits, squared_error = tested + 1, hits + hit, squared_error + (prior - hit) ** 2
    for b in bins:
        n, prior_sum = b["tested"], b.pop("prior_sum")
        b["hit_rate"] = round(b["hits"] / n, 4) if n else None
        b["mean_prior"] = round(prior_sum / n, 4) if n else None
    base_rate = hits / tested if tested else None
    return {"hypotheses": sum(by_status.values()), "hypotheses_by_status": dict(sorted(by_status.items())),
            "tested": tested, "hits": hits,
            "base_rate": None if base_rate is None else round(base_rate, 4),
            "brier": round(squared_error / tested, 4) if tested else None,
            "brier_of_base_rate": None if base_rate is None else round(base_rate * (1.0 - base_rate), 4),
            "bins": bins}


def default_out_path(roots: List[str]) -> str:
    return os.path.join("results", os.path.basename(os.path.normpath(roots[0])), "value_telemetry.csv")


def render_summary(summary: Dict[str, Any]) -> str:
    cal = summary["calibration"]

    def fmt(value: Any, digits: int = 3) -> str:
        return "-" if value is None else f"{value:.{digits}f}"

    lines = [
        f"trees: {summary['trees']} under {', '.join(summary['roots'])}"
        f" (without ledger: {summary['trees_without_ledger']}, unreadable: {summary['trees_unreadable']})",
        f"moves: {summary['moves']} (" + ", ".join(f"{k}={v}" for k, v in summary["moves_by_type"].items()) + ")",
        f"  forced_low_prior: {summary['moves_forced_low_prior']}; delta_u>0: {summary['moves_with_progress']};"
        f" iteration unrecovered: {summary['moves_iteration_unrecovered']}",
        f"hypotheses: {cal['hypotheses']} (" + ", ".join(f"{k}={v}" for k, v in cal["hypotheses_by_status"].items()) + ")",
        "calibration over tested hypotheses (hit = SUPPORTED or CERTIFIED at end of tree):",
        f"  {'prior bin':<10}{'tested':>8}{'hits':>6}{'hit_rate':>10}{'mean_prior':>12}",
    ]
    for b in cal["bins"]:
        lines.append(f"  {b['bin']:<10}{b['tested']:>8}{b['hits']:>6}{fmt(b['hit_rate']):>10}{fmt(b['mean_prior']):>12}")
    lines.append(f"  {'all':<10}{cal['tested']:>8}{cal['hits']:>6}{fmt(cal['base_rate']):>10}"
                 f"   brier={fmt(cal['brier'], 4)} (always-say-base-rate: {fmt(cal['brier_of_base_rate'], 4)})")
    lines.append(f"csv: {summary['csv']} ({summary['moves']} rows, {len(summary['columns'])} columns)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", action="append", help=f"directory to scan (repeatable; default {DEFAULT_ROOT})")
    parser.add_argument("--out", help="CSV path (default results/<root basename>/value_telemetry.csv)")
    parser.add_argument("--json", help="also write the summary as JSON here")
    args = parser.parse_args(argv)
    roots = args.root or [DEFAULT_ROOT]
    out_path = args.out or default_out_path(roots)

    rows: List[Dict[str, Any]] = []
    feature_names: List[str] = []
    extra_names: List[str] = []
    hypotheses: List[Dict[str, Any]] = []
    trees = without_ledger = unreadable = 0
    for label, path in find_trees(roots):
        try:
            with open(path, encoding="utf-8") as fh:
                tree = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"warning: skipping unreadable {path}: {exc}", file=sys.stderr)
            unreadable += 1
            continue
        if isinstance(tree, dict) and "move_log" in tree and "ledger" not in tree:
            tree = {"company_id": tree.get("company_id", ""), "ledger": tree}
        trees += 1
        if not tree.get("ledger"):
            without_ledger += 1
        hypotheses.extend((tree.get("ledger") or {}).get("hypotheses") or [])
        rows.extend(flatten_tree(tree, label, feature_names, extra_names))

    has_roles = any("role_id" in row for row in rows)
    columns = (list(HEAD_COLUMNS) + [f"f_{n}" for n in feature_names] + list(TAIL_COLUMNS)
               + [f"x_{n}" for n in extra_names] + (["role_id"] if has_roles else []))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(rows)

    by_type: Dict[str, int] = {}
    for row in rows:
        by_type[row["move_type"]] = by_type.get(row["move_type"], 0) + 1
    summary = {
        "roots": roots, "csv": out_path, "columns": columns,
        "trees": trees, "trees_without_ledger": without_ledger, "trees_unreadable": unreadable,
        "moves": len(rows), "moves_by_type": dict(sorted(by_type.items())),
        "moves_forced_low_prior": sum(r["forced_low_prior"] for r in rows),
        "moves_with_progress": sum(1 for r in rows if float(r["delta_u"] or 0.0) > 0.0),
        "moves_iteration_unrecovered": sum(1 for r in rows if r["iteration"] == ""),
        "calibration": calibration(hypotheses),
    }
    if has_roles:
        by_role: Dict[str, int] = {}
        for row in rows:
            if row.get("role_id"):
                by_role[row["role_id"]] = by_role.get(row["role_id"], 0) + 1
        summary["moves_by_role"] = dict(sorted(by_role.items()))
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
    print(render_summary(summary))
    if has_roles:
        print("moves by role (V8): " + ", ".join(f"{k}={v}" for k, v in summary["moves_by_role"].items()))
    return summary


if __name__ == "__main__":
    main()
