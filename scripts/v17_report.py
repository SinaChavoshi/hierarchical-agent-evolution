#!/usr/bin/env python3
"""V17 report: per-arm resolves, paired tests, organization metrics, and Red-Team confusion/emergence analysis.

Examples:
  PYTHONPATH=. python3 scripts/v17_report.py --split test --dirs results/v17/test_2026-10-30
  PYTHONPATH=. python3 scripts/v17_report.py --split train --dirs results/v17/train_arms --evolve-dir results/v17/evolve
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import random
import re
import sys
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v17_splits.json")
FALLBACK_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v15_splits.json")


def load(dirs: List[str], split: str) -> Tuple[Dict[str, Dict[str, Dict[str, bool]]], Dict[str, str], Dict[str, Dict]]:
    """arm -> replicate -> task -> resolved; genome_id -> evals dir; arm -> sample genome dict."""
    arms: Dict[str, Dict[str, Dict[str, bool]]] = collections.defaultdict(dict)
    where: Dict[str, str] = {}
    meta: Dict[str, Dict] = {}
    for d in dirs:
        st_path = os.path.join(d, f"{split}_state.json")
        if not os.path.exists(st_path):
            continue
        for gid, g in json.load(open(st_path, encoding="utf-8")).items():
            m = re.match(r"(.+)__r(\d+)$", gid)
            arm, rep = (m.group(1), m.group(2)) if m else (gid, "1")
            arms[arm][rep] = {t: bool(r.get("resolved")) for t, r in (g.get("results") or {}).items()}
            where[gid] = os.path.join(d, "evals", gid)
            meta.setdefault(arm, g)
    return arms, where, meta


def majority(reps: Dict[str, Dict[str, bool]], tasks: List[str]) -> Dict[str, bool]:
    n = max(1, len(reps))
    return {t: sum(r.get(t, False) for r in reps.values()) * 2 > n for t in tasks}


def sign_test(a: Dict[str, bool], b: Dict[str, bool]) -> Tuple[int, int, float]:
    wins = sum(1 for t in a if a[t] and not b.get(t))
    losses = sum(1 for t in a if b.get(t) and not a[t])
    n = wins + losses
    if n == 0:
        return wins, losses, 1.0
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return wins, losses, min(1.0, 2 * p)


def bootstrap_ci(a: Dict[str, float], b: Dict[str, float], n_boot: int = 10000, seed: int = 17) -> Tuple[float, float, float]:
    tasks = sorted(a)
    diffs = [a[t] - b[t] for t in tasks]
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        s = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        stats.append(sum(s) / len(s))
    stats.sort()
    return sum(diffs) / len(diffs), stats[int(0.025 * n_boot)], stats[int(0.975 * n_boot) - 1]


def org_and_redteam_metrics(where: Dict[str, str], arm: str, g_meta: Dict[str, Any]) -> Dict[str, Any]:
    hires = collections.Counter()
    staffed = collections.Counter()
    swaps = 0
    runs = 0
    exec_spec = [0, 0]
    exec_gen = [0, 0]
    fals_agree = [0, 0]
    tp = fp = tn = fn = 0
    verdicts_total = 0
    edit_blocks = 0
    repro_restores = 0
    cand_total = 0
    dup_total = 0
    bb_entries = 0
    bb_questions = 0
    bb_answers = 0
    bb_failing = 0
    tokens_list: List[int] = []
    role_dv: Dict[str, List[float]] = collections.defaultdict(list)

    for gid, d in where.items():
        if not (gid == arm or gid.startswith(arm + "__r")):
            continue
        for rec_path in glob.glob(os.path.join(d, "*", "record.json")):
            task_dir = os.path.dirname(rec_path)
            grade_path = os.path.join(task_dir, "grade.json")
            vg_path = os.path.join(task_dir, "verdicts_grade.json")
            try:
                rec = json.load(open(rec_path, encoding="utf-8"))
                org = (rec.get("swebench") or {}).get("org")
                resolved = bool(json.load(open(grade_path, encoding="utf-8")).get("resolved"))
                tokens_list.append(int(rec.get("tokens_used") or 0))
            except Exception:
                continue
            if not org:
                continue
            runs += 1
            swaps += len(org.get("swaps") or [])
            edit_blocks += int(org.get("edit_blocks_refused") or 0)
            repro_restores += int(org.get("repro_restorations") or 0)
            cand_total += int(org.get("candidate_patches_total") or 0)
            dup_total += int(org.get("duplicate_patches") or 0)
            bb = org.get("blackboard") or {}
            bb_entries += int(bb.get("entries_count") or 0)
            bb_questions += int(bb.get("questions_asked") or 0)
            bb_answers += int(bb.get("questions_answered") or 0)
            bb_failing += int(bb.get("failing_cases_shared") or 0)
            for layer, m in (org.get("team") or {}).items():
                staffed[layer] += 1
                hires[layer] += 0 if m.get("is_generalist") else 1
            node_roles = org.get("node_roles") or {}
            gen_ids = {m.get("role_id") for m in (org.get("team") or {}).values() if m.get("is_generalist")}
            b1 = node_roles.get("b1")
            if b1:
                tgt = exec_gen if b1 in gen_ids else exec_spec
                tgt[0] += int(resolved)
                tgt[1] += 1
            for e in org.get("episodes") or []:
                if e.get("role_id"):
                    role_dv[e["role_id"]].append(float(e.get("delta_v") or 0))
            last = [e for e in org.get("episodes") or [] if e.get("falsifier_ok") is not None]
            if last:
                fals_agree[0] += int(bool(last[-1]["falsifier_ok"]) == resolved)
                fals_agree[1] += 1

            verdicts = org.get("verdicts") or []
            verdicts_total += len(verdicts)
            if os.path.exists(vg_path):
                try:
                    vg = json.load(open(vg_path, encoding="utf-8"))
                    cm = vg.get("confusion") or {}
                    tp += int(cm.get("tp", 0))
                    fp += int(cm.get("fp", 0))
                    tn += int(cm.get("tn", 0))
                    fn += int(cm.get("fn", 0))
                    continue
                except Exception:
                    pass
            if verdicts:
                passed = bool(verdicts[-1].get("passed"))
                if passed and resolved:
                    tp += 1
                elif passed and not resolved:
                    fp += 1
                elif not passed and not resolved:
                    tn += 1
                else:
                    fn += 1

    fpr = round(fp / (fp + tn), 3) if (fp + tn) > 0 else None
    sens = round(tp / (tp + fn), 3) if (tp + fn) > 0 else None
    spec = round(tn / (tn + fp), 3) if (tn + fp) > 0 else None
    dup_rate = round(dup_total / cand_total, 3) if cand_total > 0 else 0.0
    return {
        "runs": runs,
        "mean_tokens": int(sum(tokens_list) / len(tokens_list)) if tokens_list else 0,
        "specialist_hire_rate": {l: round(hires[l] / staffed[l], 3) for l in staffed if staffed[l]},
        "swaps_per_run": round(swaps / runs, 3) if runs else 0.0,
        "resolve_rate_first_executor_specialist": [exec_spec[0], exec_spec[1]],
        "resolve_rate_first_executor_generalist": [exec_gen[0], exec_gen[1]],
        "falsifier_agreement_with_hidden_tests": [fals_agree[0], fals_agree[1]],
        "redteam": {
            "config": g_meta.get("redteam") or {},
            "verdicts_total": verdicts_total,
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
            "false_pass_rate": fpr,
            "sensitivity": sens,
            "specificity": spec,
            "edit_blocks_refused": edit_blocks,
            "repro_restorations": repro_restores,
            "candidate_patches_total": cand_total,
            "duplicate_patches": dup_total,
            "duplicate_patch_rate": dup_rate,
        },
        "blackboard": {
            "entries_total": bb_entries,
            "questions_asked": bb_questions,
            "questions_answered": bb_answers,
            "failing_cases_shared": bb_failing,
        },
        "mean_delta_v_per_role": {k: round(sum(v) / len(v), 3) for k, v in role_dv.items()},
    }


def summarize_evolution(evolve_dir: str, tasks: List[str]) -> Dict[str, Any]:
    st_path = os.path.join(evolve_dir, "state.json")
    if not os.path.exists(st_path):
        return {}
    from hae.evolution.genome import HarnessGenome
    st = json.load(open(st_path, encoding="utf-8"))
    by_gen: Dict[int, List[HarnessGenome]] = collections.defaultdict(list)
    for gid, gd in (st.get("genomes") or {}).items():
        g = HarnessGenome.from_dict(gd)
        if g.evaluated_on(tasks):
            by_gen[g.generation].append(g)
    traj = []
    for gen, gs in sorted(by_gen.items()):
        gs.sort(key=lambda x: x.fitness(tasks, redteam_weight=0.3), reverse=True)
        best = gs[0]
        strict_features = sum(1 for k in ("no_edit", "lock_repro_test", "skeptic_default", "evidence_tests", "continuous_build")
                              if best.redteam.get(k)) + (1 if int(best.redteam.get("panel_size", 1)) > 1 else 0)
        traj.append({
            "generation": gen,
            "n_promoted": len(gs),
            "best_id": best.genome_id,
            "origin": best.origin,
            "resolves": sum(best.results[t].resolved for t in tasks),
            "rt_score": round(best.red_team_score(tasks), 3),
            "confusion": best.red_team_confusion(tasks),
            "strict_features_active": strict_features,
            "redteam": best.redteam,
        })
    return {"generations": traj}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", required=True)
    ap.add_argument("--dirs", nargs="+", required=True)
    ap.add_argument("--splits", default=DEFAULT_SPLITS)
    ap.add_argument("--evolve-dir", default="")
    ap.add_argument(
        "--pairs",
        nargs="*",
        default=["S:A17", "B17:S", "Q17:B17", "Q17:S", "R:A17", "R:S", "R:Q17", "R:Rr", "R:V12"],
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sp_path = args.splits if os.path.exists(args.splits) else FALLBACK_SPLITS
    splits = json.load(open(sp_path, encoding="utf-8"))
    tasks = list(splits[args.split])
    arms, where, meta = load(args.dirs, args.split)
    out: Dict[str, Any] = {"split": args.split, "n_tasks": len(tasks), "arms": {}, "pairs": {}}
    for arm, reps in sorted(arms.items()):
        counts = [sum(r.get(t, False) for t in tasks) for r in reps.values()]
        complete = [len([t for t in tasks if t in r]) for r in reps.values()]
        maj = majority(reps, tasks)
        agree = None
        if len(reps) >= 2:
            rs = list(reps.values())
            agree = sum(1 for t in tasks if len({r.get(t, False) for r in rs}) == 1) / len(tasks)
        out["arms"][arm] = {
            "runs": counts,
            "evaluated": complete,
            "mean": round(sum(counts) / len(counts), 2),
            "majority": sum(maj.values()),
            "run_agreement": None if agree is None else round(agree, 3),
            "org": org_and_redteam_metrics(where, arm, meta.get(arm, {})),
        }
    for pair in args.pairs:
        a, b = pair.split(":")
        if a not in arms or b not in arms:
            continue
        ma, mb = majority(arms[a], tasks), majority(arms[b], tasks)
        w, l, p = sign_test(ma, mb)
        mean_a = {t: sum(r.get(t, False) for r in arms[a].values()) / len(arms[a]) for t in tasks}
        mean_b = {t: sum(r.get(t, False) for r in arms[b].values()) / len(arms[b]) for t in tasks}
        d, lo, hi = bootstrap_ci(mean_a, mean_b)
        out["pairs"][pair] = {"wins": w, "losses": l, "sign_p": round(p, 4),
                              "mean_rate_diff": round(d, 4), "ci95": [round(lo, 4), round(hi, 4)]}
    if args.evolve_dir:
        out["evolution"] = summarize_evolution(args.evolve_dir, tasks)
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{args.split}: {len(tasks)} tasks")
    print(f"{'arm':<8} {'runs':<10} {'mean':>5} {'maj':>4} {'agree':>6} {'FPR':>6} {'dup%':>6} {'TP/FP/TN/FN':<15} {'bb(e/q)':<9} {'tokens':>8}")
    for arm, a in out["arms"].items():
        rt = a["org"]["redteam"]
        bb = a["org"]["blackboard"]
        cm = rt["confusion"]
        cm_s = f"{cm['tp']}/{cm['fp']}/{cm['tn']}/{cm['fn']}"
        fpr_s = f"{rt['false_pass_rate']:.2f}" if rt["false_pass_rate"] is not None else "  - "
        dup_s = f"{rt['duplicate_patch_rate']*100:.1f}%"
        bb_s = f"{bb['entries_total']}/{bb['questions_answered']}"
        print(f"{arm:<8} {str(a['runs']):<10} {a['mean']:>5} {a['majority']:>4} "
              f"{str(a['run_agreement']):>6} {fpr_s:>6} {dup_s:>6} {cm_s:<15} {bb_s:<9} {a['org']['mean_tokens']:>8}")
    for pair, p in out["pairs"].items():
        print(f"{pair}: {p['wins']}:{p['losses']} sign p={p['sign_p']}, Δrate={p['mean_rate_diff']:+.3f} CI95={p['ci95']}")
    if out.get("evolution", {}).get("generations"):
        print("\nRed-Team Evolution Trajectory:")
        for row in out["evolution"]["generations"]:
            print(f"  gen {row['generation']} ({row['best_id']}, {row['origin']}): resolves={row['resolves']} "
                  f"rt_score={row['rt_score']} cm={row['confusion']} strict_genes={row['strict_features_active']}/6")
    return 0


if __name__ == "__main__":
    sys.exit(main())
