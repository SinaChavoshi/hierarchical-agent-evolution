#!/usr/bin/env python3
"""V18 Scientific Report Generator: Verifier Calibration + Compute-Matched Showdown.

Generates a sober, pre-registered scientific report comparing:
  - `Flat_1x_V18`: Single flat ReAct rollout with V18 substrate fixes (~150k tokens)
  - `Flat_3x_Select`: 3x parallel `Flat_1x_V18` rollouts (~450k tokens) + blind Candidate-Pool Ranker
  - `Flat_3x_PassAt3`: Oracle union across the 3 `Flat_1x_V18` rollouts (upper bound on 3x flat sampling)
  - `V18_Collab`: Hierarchical Collaborative Harness (4 sub-blackboards, Pre-Edit Builder Huddle,
    Post-Prototype Synthesis Roundtable, Candidate-Pool Red-Team Ranker, ~420k tokens)
  - `V18_Evolved`: Hierarchical Collaborative Harness with evolved roles + offline-calibrated Ranker weights

Includes:
  - Offline Verifier Calibration metrics on 2,525 container-graded candidate patches (`TP`, `FP`, `TN`, `FN`,
    Precision, Recall, Specificity, F1, False-Pass Rate, False-Rejection Rate, AUROC, Top-1 Selection Accuracy)
  - Compute-matched token efficiency (`mean tokens / task` and `resolved / MTok`)
  - Paired bootstrap 95% confidence intervals (10,000 resamples) and exact two-sided McNemar p-values.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v18_splits.json")
DEFAULT_CALIBRATION = os.path.join(REPO_ROOT, "results", "v18", "ranker_calibration.json")


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def paired_bootstrap_ci(
    a_scores: Mapping[str, float],
    b_scores: Mapping[str, float],
    tasks: Sequence[str],
    n_boot: int = 10000,
    seed: int = 18,
) -> Tuple[float, float, float, float]:
    """Return (diff_tasks, ci_lo_tasks, ci_hi_tasks, p_two_sided) for `a - b` across `tasks`."""
    common = [t for t in tasks if t in a_scores and t in b_scores]
    n = len(common)
    if n == 0:
        return (0.0, 0.0, 0.0, 1.0)
    diffs = [float(a_scores[t]) - float(b_scores[t]) for t in common]
    obs = sum(diffs)
    rng = random.Random(seed)
    samples: List[float] = []
    for _ in range(n_boot):
        s = sum(diffs[rng.randrange(n)] for _ in range(n))
        samples.append(s)
    samples.sort()
    lo = samples[int(0.025 * n_boot)]
    hi = samples[int(0.975 * n_boot)]

    # Exact two-sided binomial / McNemar test on discordant pairs
    wins_a = sum(1 for d in diffs if d > 0.5)
    wins_b = sum(1 for d in diffs if d < -0.5)
    disc = wins_a + wins_b
    if disc == 0:
        p_val = 1.0
    else:
        k = min(wins_a, wins_b)
        tail = sum(math.comb(disc, i) for i in range(k + 1)) / float(2 ** disc)
        p_val = min(1.0, 2.0 * tail)
    return (round(obs, 2), round(lo, 2), round(hi, 2), round(p_val, 4))


def summarize_arm(
    arm_label: str,
    rep_dicts: Sequence[Mapping[str, Any]],
    tasks: Sequence[str],
    eval_dirs: Sequence[str] = (),
) -> Dict[str, Any]:
    """Aggregate resolution, token cost, and V18 collaboration telemetry across replicates."""
    per_task_res: Dict[str, float] = {}
    per_task_tok: Dict[str, float] = {}
    rep_totals: List[int] = []

    for rd in rep_dicts:
        results = rd.get("results") or {}
        rep_wins = 0
        for t in tasks:
            r = results.get(t)
            if not r or r.get("status") != "ok":
                continue
            if r.get("resolved"):
                rep_wins += 1
        rep_totals.append(rep_wins)

    for t in tasks:
        vals = []
        toks = []
        for rd in rep_dicts:
            r = (rd.get("results") or {}).get(t)
            if r and r.get("status") == "ok":
                vals.append(1.0 if r.get("resolved") else 0.0)
                toks.append(float(r.get("tokens") or 0.0))
        if vals:
            per_task_res[t] = sum(vals) / len(vals)
            per_task_tok[t] = sum(toks) / len(toks)

    n_done = len(per_task_res)
    mean_resolved = round(sum(per_task_res.values()), 2)
    pass_rate = round(100.0 * mean_resolved / max(1, n_done), 1)
    mean_tokens = round(sum(per_task_tok.values()) / max(1, n_done), 0)
    total_mtok = sum(per_task_tok.values()) / 1_000_000.0
    res_per_mtok = round(mean_resolved / max(1e-6, total_mtok), 2)

    # Collaboration & sub-blackboard telemetry from record.json files
    bb_entries = 0
    by_channel: Dict[str, int] = collections.Counter()
    huddle_overrides = 0
    synthesis_runs = 0
    pool_candidates = 0
    n_records = 0

    for ed in eval_dirs:
        for t in tasks:
            rec_p = os.path.join(ed, t, "record.json")
            if not os.path.exists(rec_p):
                continue
            try:
                rec = _load_json(rec_p)
                org = (rec.get("swebench") or {}).get("org") or {}
                if not org:
                    continue
                n_records += 1
                bb = org.get("blackboard") or {}
                bb_entries += int(bb.get("entries_count") or 0)
                for ch, cnt in (bb.get("by_channel") or {}).items():
                    by_channel[ch] += int(cnt or 0)
                huddle_overrides += int(org.get("contract_overrides") or 0)
                synthesis_runs += int(org.get("synthesis_runs") or 0)
                pool_candidates += len(org.get("candidate_pool") or [])
            except Exception:
                pass

    return {
        "arm": arm_label,
        "tasks_completed": n_done,
        "tasks_total": len(tasks),
        "mean_resolved": mean_resolved,
        "pass_rate_pct": pass_rate,
        "rep_totals": rep_totals,
        "mean_tokens_per_task": int(mean_tokens),
        "resolved_per_mtok": res_per_mtok,
        "per_task_resolved": per_task_res,
        "collab_telemetry": {
            "records": n_records,
            "mean_bb_entries": round(bb_entries / max(1, n_records), 1),
            "by_channel": dict(by_channel),
            "contract_overrides": huddle_overrides,
            "synthesis_runs": synthesis_runs,
            "mean_pool_candidates": round(pool_candidates / max(1, n_records), 2),
        },
    }


def generate_report(
    dirs: Sequence[str],
    splits_path: str = DEFAULT_SPLITS,
    split_name: str = "eval_24_stratified",
    calibration_path: str = DEFAULT_CALIBRATION,
    out_md: str = "",
) -> str:
    splits = _load_json(splits_path)
    tasks = list(splits[split_name])
    cal = _load_json(calibration_path) if os.path.exists(calibration_path) else {}

    saved_all: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for d in dirs:
        st_p = os.path.join(d, "state.json")
        if os.path.exists(st_p):
            st = _load_json(st_p)
            for gid, gd in st.items():
                saved_all[gid] = (gd, os.path.join(d, "evals", gid))

    # Group replicates by base arm name
    groups: Dict[str, List[Tuple[Dict[str, Any], str]]] = collections.defaultdict(list)
    for gid, (gd, edir) in sorted(saved_all.items()):
        base = gid.split("__r", 1)[0]
        groups[base].append((gd, edir))

    summaries: Dict[str, Dict[str, Any]] = {}
    for base, items in groups.items():
        summaries[base] = summarize_arm(
            base,
            [it[0] for it in items],
            tasks,
            [it[1] for it in items],
        )

    # Also compute Flat_3x_PassAt3 (Oracle Union across Flat_1x_V18 replicates) if >=2 replicates exist
    if "Flat_1x_V18" in groups and len(groups["Flat_1x_V18"]) >= 2:
        rep_dicts = [it[0] for it in groups["Flat_1x_V18"]]
        union_res: Dict[str, float] = {}
        union_tok: Dict[str, float] = {}
        for t in tasks:
            rs = [(rd.get("results") or {}).get(t) for rd in rep_dicts]
            rs = [r for r in rs if r and r.get("status") == "ok"]
            if rs:
                union_res[t] = 1.0 if any(r.get("resolved") for r in rs) else 0.0
                union_tok[t] = sum(float(r.get("tokens") or 0.0) for r in rs)
        n_u = len(union_res)
        u_wins = round(sum(union_res.values()), 2)
        u_tok = round(sum(union_tok.values()) / max(1, n_u), 0)
        u_mtok = sum(union_tok.values()) / 1_000_000.0
        summaries["Flat_3x_PassAt3"] = {
            "arm": "Flat_3x_PassAt3 (Oracle Union)",
            "tasks_completed": n_u,
            "tasks_total": len(tasks),
            "mean_resolved": u_wins,
            "pass_rate_pct": round(100.0 * u_wins / max(1, n_u), 1),
            "rep_totals": [int(u_wins)],
            "mean_tokens_per_task": int(u_tok),
            "resolved_per_mtok": round(u_wins / max(1e-6, u_mtok), 2),
            "per_task_resolved": union_res,
            "collab_telemetry": {"records": 0},
        }

    lines: List[str] = [
        f"# V18 Evaluation Report (`{split_name}`, $N = {len(tasks)}$ Fresh SWE-bench Verified Tasks)",
        "",
        "## 1. Offline Verifier & Candidate-Pool Ranker Calibration ($n = 2,525$ Container-Graded Candidate Patches)",
        "",
    ]
    if cal:
        cm = cal["confusion_matrix"]
        bg = cal["binary_gate_vs_ranker"]
        cw = cal["calibrated_weights"]
        lines.extend([
            "| Metric | V17 Binary Red-Team Gate | V18 Default Pool Ranker | V18 Calibrated Pool Ranker |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Container-Graded Patches ($n$)** | {cal['total_labelled_patches']} ({cal['total_task_runs']} runs) | {cal['total_labelled_patches']} | {cal['total_labelled_patches']} |",
            f"| **Confusion Matrix (`TP / FP / TN / FN`)** | `{cm['TP']} / {cm['FP']} / {cm['TN']} / {cm['FN']}` | Continuous score | Continuous score |",
            f"| **Precision (`TP / (TP + FP)`)** | {100*cm['precision']:.1f}% (FPR {100*cm['false_pass_rate']:.1f}%) | — | — |",
            f"| **Recall (`TP / (TP + FN)`)** | {100*cm['recall']:.1f}% (FRR {100*cm['false_rejection_rate']:.1f}%) | **100.0%** (0 pool discards) | **100.0%** (0 pool discards) |",
            f"| **Specificity (`TN / (TN + FP)`)** | {100*cm['specificity']:.1f}% | — | — |",
            f"| **Patch Ranking AUROC** | `{bg['v17_binary_gate_auroc']:.3f}` | `{bg['v18_default_ranker_auroc']:.3f}` | **`{bg['v18_calibrated_ranker_auroc']:.3f}`** |",
            f"| **Resolvable-Run Recall / Top-1 Acc ($n={bg['all_resolvable_runs']}$)** | {100*bg['v17_binary_gate_run_recall']:.1f}% | **{100*bg['v18_default_ranker_all_resolvable_top1_acc']:.1f}%** | **{100*bg['v18_calibrated_ranker_all_resolvable_top1_acc']:.1f}%** |",
            f"| **Discordant Multi-Patch Top-1 Acc ($n={bg['discordant_multi_candidate_runs']}$)** | {100*bg['v17_binary_gate_discordant_top1_acc']:.1f}% | **{100*bg['v18_default_ranker_discordant_top1_acc']:.1f}%** | **{100*bg['v18_calibrated_ranker_discordant_top1_acc']:.1f}%** |",
            "",
            f"- **Calibrated Ranker Weights**: `ranker_falsifier_weight={cw['ranker_falsifier_weight']}`, `ranker_simplicity_weight={cw['ranker_simplicity_weight']}`, `ranker_synthesis_bonus={cw['ranker_synthesis_bonus']}`.",
            "",
        ])

    lines.extend([
        f"## 2. Compute-Matched Showdown on Fresh `{split_name}` ($N = {len(tasks)}$ Tasks)",
        "",
        "| Arm | Tasks Completed | Resolved ($/ N$) | Pass Rate (%) | Replicate Totals | Mean Tokens / Task | Efficiency (`Resolved / MTok`) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])
    order = ["Flat_1x_V18", "Flat_3x_Select", "Flat_3x_PassAt3", "V18_Collab", "V18_Evolved"]
    for k in order + [k for k in summaries if k not in order]:
        if k not in summaries:
            continue
        s = summaries[k]
        lines.append(
            f"| **{s['arm']}** | `{s['tasks_completed']}/{s['tasks_total']}` | **`{s['mean_resolved']:.2f}`** | "
            f"`{s['pass_rate_pct']:.1f}%` | `{s['rep_totals']}` | `{s['mean_tokens_per_task']:,}` | `{s['resolved_per_mtok']:.2f}` |"
        )

    lines.extend([
        "",
        "## 3. Paired Bootstrap 95% Confidence Intervals & Exact McNemar Tests",
        "",
        "| Hypothesis Comparison | Net Task Diff ($\Delta$) | 95% Bootstrap CI (Tasks) | Exact McNemar $p$ | Statistical Verdict ($\alpha = 0.05$) |",
        "| :--- | :---: | :---: | :---: | :--- |",
    ])
    pairs = [
        ("H1 (`Flat_3x_Select` vs `Flat_1x_V18`)", "Flat_3x_Select", "Flat_1x_V18"),
        ("H2 (`V18_Collab` vs `Flat_3x_Select`)", "V18_Collab", "Flat_3x_Select"),
        ("H2b (`V18_Collab` vs `Flat_1x_V18`)", "V18_Collab", "Flat_1x_V18"),
        ("H3 (`V18_Evolved` vs `V18_Collab`)", "V18_Evolved", "V18_Collab"),
        ("H3b (`V18_Evolved` vs `Flat_3x_Select`)", "V18_Evolved", "Flat_3x_Select"),
        ("H3c (`V18_Evolved` vs `Flat_1x_V18`)", "V18_Evolved", "Flat_1x_V18"),
    ]
    for label, a_k, b_k in pairs:
        if a_k in summaries and b_k in summaries:
            d_val, lo, hi, p_val = paired_bootstrap_ci(
                summaries[a_k]["per_task_resolved"],
                summaries[b_k]["per_task_resolved"],
                tasks,
            )
            verdict = "Significant ($p < 0.05$)" if p_val < 0.05 else "Not statistically significant (null not rejected)"
            lines.append(f"| {label} | `{d_val:+.2f}` | `[{lo:+.2f}, {hi:+.2f}]` | `{p_val:.4f}` | {verdict} |")

    lines.extend([
        "",
        "## 4. V18 Collaborative Sub-Blackboard & Roundtable Telemetry",
        "",
        "| Arm | Mean Blackboard Entries | `#recon-map` | `#builders-room` | `#redteam-chamber` | `#verdicts-and-repros` | Builder Huddle Overrides | Synthesis Roundtables | Mean Pool Candidates |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])
    for k in ("V18_Collab", "V18_Evolved"):
        if k not in summaries:
            continue
        ct = summaries[k]["collab_telemetry"]
        ch = ct.get("by_channel") or {}
        lines.append(
            f"| **{k}** | `{ct.get('mean_bb_entries', 0)}` | `{ch.get('recon-map', 0)}` | "
            f"`{ch.get('builders-room', 0)}` | `{ch.get('redteam-chamber', 0)}` | "
            f"`{ch.get('verdicts-and-repros', 0)}` | `{ct.get('contract_overrides', 0)}` | "
            f"`{ct.get('synthesis_runs', 0)}` | `{ct.get('mean_pool_candidates', 0)}` |"
        )

    md = "\n".join(lines) + "\n"
    if out_md:
        os.makedirs(os.path.dirname(os.path.abspath(out_md)), exist_ok=True)
        with open(out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dirs", nargs="+", required=True)
    ap.add_argument("--splits", default=DEFAULT_SPLITS)
    ap.add_argument("--split", default="eval_24_stratified")
    ap.add_argument("--calibration", default=DEFAULT_CALIBRATION)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    md = generate_report(args.dirs, args.splits, args.split, args.calibration, args.out)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
