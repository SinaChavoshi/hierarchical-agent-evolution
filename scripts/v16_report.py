#!/usr/bin/env python3
"""V16 report: per-arm resolves (mean of replicates), paired tests and organization metrics.

  PYTHONPATH=. python3 scripts/v16_report.py --split test --dirs results/v16/test_2026-10-29 [--json]

Replicates are genome ids `<arm>__r<k>`. Per task, an arm's outcome for paired tests is the
majority over replicates (ties count as unresolved), and the mean resolve count is reported too.
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
from typing import Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(dirs: List[str], split: str) -> Tuple[Dict[str, Dict[str, Dict[str, bool]]], Dict[str, str]]:
    """arm -> replicate -> task -> resolved; plus genome_id -> evals dir."""
    arms: Dict[str, Dict[str, Dict[str, bool]]] = collections.defaultdict(dict)
    where: Dict[str, str] = {}
    for d in dirs:
        st_path = os.path.join(d, f"{split}_state.json")
        if not os.path.exists(st_path):
            continue
        for gid, g in json.load(open(st_path)).items():
            m = re.match(r"(.+)__r(\d+)$", gid)
            arm, rep = (m.group(1), m.group(2)) if m else (gid, "1")
            arms[arm][rep] = {t: bool(r.get("resolved")) for t, r in (g.get("results") or {}).items()}
            where[gid] = os.path.join(d, "evals", gid)
    return arms, where


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


def bootstrap_ci(a: Dict[str, float], b: Dict[str, float], n_boot: int = 10000, seed: int = 16) -> Tuple[float, float, float]:
    tasks = sorted(a)
    diffs = [a[t] - b[t] for t in tasks]
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        s = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        stats.append(sum(s) / len(s))
    stats.sort()
    return sum(diffs) / len(diffs), stats[int(0.025 * n_boot)], stats[int(0.975 * n_boot) - 1]


def org_metrics(where: Dict[str, str], arm: str) -> Dict[str, object]:
    hires = collections.Counter()
    staffed = collections.Counter()
    swaps = 0
    runs = 0
    exec_spec = [0, 0]
    exec_gen = [0, 0]
    fals_agree = [0, 0]
    role_dv: Dict[str, List[float]] = collections.defaultdict(list)
    for gid, d in where.items():
        if not (gid == arm or gid.startswith(arm + "__r")):
            continue
        for rec_path in glob.glob(os.path.join(d, "*", "record.json")):
            grade_path = os.path.join(os.path.dirname(rec_path), "grade.json")
            try:
                org = (json.load(open(rec_path)).get("swebench") or {}).get("org")
                resolved = bool(json.load(open(grade_path)).get("resolved"))
            except Exception:
                continue
            if not org:
                continue
            runs += 1
            swaps += len(org.get("swaps") or [])
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
    return {
        "runs": runs,
        "specialist_hire_rate": {l: round(hires[l] / staffed[l], 3) for l in staffed if staffed[l]},
        "swaps_per_run": round(swaps / runs, 3) if runs else 0.0,
        "resolve_rate_first_executor_specialist": [exec_spec[0], exec_spec[1]],
        "resolve_rate_first_executor_generalist": [exec_gen[0], exec_gen[1]],
        "falsifier_agreement_with_hidden_tests": [fals_agree[0], fals_agree[1]],
        "mean_delta_v_per_role": {k: round(sum(v) / len(v), 3) for k, v in role_dv.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", required=True)
    ap.add_argument("--dirs", nargs="+", required=True)
    ap.add_argument("--pairs", nargs="*", default=["C:A", "B:A", "D:E", "D:A"], help="arm1:arm2 paired comparisons")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    splits = json.load(open(os.path.join(REPO_ROOT, "data", "swebench", "v15_splits.json")))
    tasks = list(splits[args.split])
    arms, where = load(args.dirs, args.split)
    out: Dict[str, object] = {"split": args.split, "n_tasks": len(tasks), "arms": {}, "pairs": {}}
    for arm, reps in sorted(arms.items()):
        counts = [sum(r.get(t, False) for t in tasks) for r in reps.values()]
        complete = [len([t for t in tasks if t in r]) for r in reps.values()]
        maj = majority(reps, tasks)
        agree = None
        if len(reps) >= 2:
            rs = list(reps.values())
            agree = sum(1 for t in tasks if len({r.get(t, False) for r in rs}) == 1) / len(tasks)
        out["arms"][arm] = {"runs": counts, "evaluated": complete, "mean": round(sum(counts) / len(counts), 2),
                            "majority": sum(maj.values()), "run_agreement": None if agree is None else round(agree, 3),
                            "org": org_metrics(where, arm)}
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
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{args.split}: {len(tasks)} tasks")
    print(f"{'arm':<10} {'runs':<12} {'mean':>5} {'maj':>4} {'agree':>6}")
    for arm, a in out["arms"].items():
        print(f"{arm:<10} {str(a['runs']):<12} {a['mean']:>5} {a['majority']:>4} {str(a['run_agreement']):>6}")
    for pair, p in out["pairs"].items():
        print(f"{pair}: {p['wins']}:{p['losses']} sign p={p['sign_p']}, Δrate={p['mean_rate_diff']:+.3f} CI95={p['ci95']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
