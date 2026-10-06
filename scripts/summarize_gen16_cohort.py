"""Summarise the Generation 16 V6 cohort (26 firm-runs) against the Gen 15 baseline.

Reads, for every replica named in `configs/generation_16_population.json`
(`cohort_design.lineages[*].replicas`, ids `<lineage>__s<k>`):

  <root>/outputs/<cid>/generation_16/<cid>_result.json          (scorecard)
  <root>/outputs/<cid>/generation_16/<cid>_epistemic_tree.json  (ledger + searches + audit)
  <root>/pod_logs/pod_<index>_*.log                             (harvested pod log; git-ignored)

and writes `<root>/generation_16_cohort_summary.json` plus a committable
per-run trace (`<root>/traces/<cid>_epistemic_trace.txt`, the `[epistemic]`
lines of the pod log) and prints three markdown tables: per lineage (the unit
of comparison -- mean/min/max over seeds against the lineage's Gen 15 `V5`
trajectory), per run, and the V6 mechanics telemetry (frontier, synthesis
modes, no-op retries, tabu, refused/repaired probes, calibration).

Two honesty notes baked into the output:
  * the held-out trajectory is the number of held-out tests passed per
    iteration, taken from `iterations_history` in the scorecard (the pod log
    is the fallback); `V5` and `V6` *net fitness* are not comparable (different
    composites), the trajectory is;
  * `epistemic_integrity` on the scorecard was computed in the pod with the
    discipline term as of the image; `integrity_a3` recomputes it with the
    current `hae.evaluation.judge` (A3: refused/repaired probes charged) from
    the same audit, so both are shown.

Usage:
    python3 scripts/summarize_gen16_cohort.py [--root results/hae_gen16_v6_cohort]
        [--population configs/generation_16_population.json] [--gpu-node-hours H]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
from typing import Any, Dict, List, Optional

from hae.evaluation.judge import EPISTEMIC_WEIGHTS, epistemic_integrity_score

ITER_RE = re.compile(r"\[Iteration (\d+)/(\d+)\] \[self-hosting-benchmark\] (?:(\d+)/50 held-out tests|submission mutates sys\.path)")
DETAIL_RE = re.compile(r"(\d+)/(\d+) held-out tests")
TRACE_RE = re.compile(r"\[Iteration \d+/\d+\]|\[epistemic\]|\[V6 EPISTEMIC\]|EPISTEMIC AUDIT|FINAL NET FITNESS|"
                      r"WORKER_EXIT|Traceback|Error:|hypothesis packet|proposer")
# Per-search loop stats worth pooling per run (summed over iterations 2..N).
SEARCH_STAT_KEYS = (
    "moves_used", "proposal_rounds", "hypotheses_accepted", "tabu_rejections", "duplicate_rejections",
    "experiments", "supported", "falsified", "inconclusive", "rejected_probes", "probe_repairs",
    "forced_low_prior_picks", "forced_low_prior_wins", "syntheses", "syntheses_verified", "syntheses_unwritten",
    "syntheses_by_plan", "syntheses_authored", "synthesis_noop_retries", "synthesis_noop_recoveries",
    "frontier_admissions", "questions_deferred", "questions_asked", "proposer_errors",
)
AUDIT_KEYS = (
    "questions", "questions_open", "questions_resolved", "questions_certified", "questions_exhausted",
    "questions_deferred", "frontier_admissions", "hypotheses", "hypotheses_tested", "hypotheses_supported",
    "hypotheses_falsified", "hypotheses_untestable", "probes_refused", "probe_repairs", "settled_facts",
    "ruled_out", "evidence_backed_fraction", "status_inconsistencies", "calibration_brier",
    "calibration_tested", "low_prior_wins", "forced_low_prior_picks", "forced_low_prior_wins",
    "proposed_total", "tabu_rejections", "duplicate_rejections", "moves_used", "budget_moves",
    "syntheses", "syntheses_verified", "resolved_fraction",
)


def trajectory_from_history(history: Any) -> List[int]:
    out = []
    for it in history or []:
        m = DETAIL_RE.search(str(it.get("detail", "")))
        out.append(int(m.group(1)) if m else (50 if it.get("passed") else 0))
    return out


def trajectory_from_log(path: Optional[str]) -> List[int]:
    if not path or not os.path.exists(path):
        return []
    txt = open(path, errors="replace").read()
    return [int(m.group(3)) if m.group(3) else 0 for m in ITER_RE.finditer(txt)]


def write_trace(log_path: Optional[str], trace_path: str) -> Optional[str]:
    """Pod logs are git-ignored and carry a base64 tarball; keep the search's own lines as evidence."""
    if not log_path or not os.path.exists(log_path):
        return None
    keep = []
    for line in open(log_path, errors="replace"):
        if "OUTPUT_TGZ_B64 BEGIN" in line:
            break
        if TRACE_RE.search(line):
            keep.append(line.rstrip()[:400])
    os.makedirs(os.path.dirname(trace_path), exist_ok=True)
    with open(trace_path, "w") as f:
        f.write("\n".join(keep) + "\n")
    return trace_path


def pooled_search_stats(searches: List[Dict[str, Any]]) -> Dict[str, int]:
    pooled: Dict[str, int] = {}
    for s in searches or []:
        for k, v in (s.get("stats") or {}).items():
            if k in SEARCH_STAT_KEYS and isinstance(v, (int, float)):
                pooled[k] = pooled.get(k, 0) + int(v)
    return pooled


def load_run(root: str, cid: str, index: int) -> Dict[str, Any]:
    rp = os.path.join(root, "outputs", cid, "generation_16", f"{cid}_result.json")
    tp = os.path.join(root, "outputs", cid, "generation_16", f"{cid}_epistemic_tree.json")
    logs = sorted(glob.glob(os.path.join(root, "pod_logs", f"pod_{index}_*.log")))
    entry: Dict[str, Any] = {"company_id": cid, "index": index, "log": logs[-1] if logs else None,
                             "has_result": os.path.exists(rp), "has_tree": os.path.exists(tp)}
    entry["trace"] = write_trace(entry["log"], os.path.join(root, "traces", f"{cid}_epistemic_trace.txt"))
    traj: List[int] = []
    if os.path.exists(rp):
        r = json.load(open(rp))
        traj = trajectory_from_history(r.get("iterations_history"))
        tu = r.get("token_usage")
        entry.update({
            "net": r.get("fitness_score"), "gross": r.get("gross_score"),
            "execution_integrity": r.get("execution_integrity"),
            "epistemic_integrity": r.get("epistemic_integrity"),
            "epistemic_efficiency": r.get("epistemic_efficiency"),
            "cost_usd": (r.get("opex") or {}).get("estimated_cost_usd"),
            "elapsed_s": r.get("elapsed_seconds"),
            "tokens": tu.get("total_tokens") if isinstance(tu, dict) else tu,
            "iterations_used": r.get("iterations_used"),
            "evaluation_failed": r.get("evaluation_failed"),
        })
    if not traj:
        traj = trajectory_from_log(entry["log"])
    entry["trajectory"] = traj
    entry["final_tests"] = traj[-1] if traj else None
    entry["best_tests"] = max(traj) if traj else None
    entry["converged_at"] = next((i + 1 for i, t in enumerate(traj) if t >= 50), None)
    if os.path.exists(tp):
        t = json.load(open(tp))
        audit = t.get("audit") or {}
        entry["audit"] = {k: audit.get(k) for k in AUDIT_KEYS}
        entry["search_stats"] = pooled_search_stats(t.get("searches") or [])
        entry["searches"] = [{"iteration": s.get("iteration"), "moves": s.get("moves_used"), "stop": s.get("stop_reason"),
                              "dU": s.get("delta_u_total"), "synthesized": s.get("synthesized_paths")}
                             for s in t.get("searches") or []]
        a3 = epistemic_integrity_score(audit)
        entry["integrity_a3"] = a3
        if a3 is not None and entry.get("execution_integrity") is not None:
            entry["gross_a3"] = round(EPISTEMIC_WEIGHTS["execution_integrity"] * float(entry["execution_integrity"])
                                      + EPISTEMIC_WEIGHTS["epistemic_integrity"] * a3
                                      + EPISTEMIC_WEIGHTS["epistemic_efficiency"] * float(entry.get("epistemic_efficiency") or 0.0), 2)
        led = t.get("ledger") or {}
        hyps = led.get("hypotheses") or {}
        hyps = list(hyps.values()) if isinstance(hyps, dict) else hyps
        entry["hypothesis_status_counts"] = {}
        for h in hyps:
            st = h.get("status", "?")
            entry["hypothesis_status_counts"][st] = entry["hypothesis_status_counts"].get(st, 0) + 1
    return entry


def _mean(xs: List[float]) -> Optional[float]:
    xs = [float(x) for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def summarise_lineage(name: str, design: Dict[str, Any], runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    done = [r for r in runs if r.get("trajectory")]
    finals = [r["final_tests"] for r in done if r.get("final_tests") is not None]
    g15 = design.get("gen15_trajectory") or []
    pooled: Dict[str, int] = {}
    for r in done:
        for k, v in (r.get("search_stats") or {}).items():
            pooled[k] = pooled.get(k, 0) + int(v)
    return {
        "lineage": name, "role": design.get("role"), "seeds_planned": design.get("seeds"),
        "runs_with_results": len(done), "runs_total": len(runs),
        "gen15_trajectory": g15, "gen15_final": g15[-1] if g15 else None,
        "v6_final_tests": finals, "v6_final_mean": _mean(finals),
        "v6_final_min": min(finals) if finals else None, "v6_final_max": max(finals) if finals else None,
        "v6_converged_runs": sum(1 for r in done if r.get("converged_at")),
        "v6_converged_at": [r.get("converged_at") for r in done],
        "v6_first_pass": [r["trajectory"][0] for r in done if r.get("trajectory")],
        "net_mean": _mean([r.get("net") for r in done]),
        "gross_a3_mean": _mean([r.get("gross_a3") for r in done]),
        "tokens_mean": _mean([r.get("tokens") for r in done]),
        "cost_usd_mean": _mean([r.get("cost_usd") for r in done]),
        "elapsed_s_mean": _mean([r.get("elapsed_s") for r in done]),
        "pooled_search_stats": pooled,
        "brier": [(r.get("audit") or {}).get("calibration_brier") for r in done],
    }


def fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="results/hae_gen16_v6_cohort")
    ap.add_argument("--population", default="configs/generation_16_population.json")
    ap.add_argument("--gpu-node-hours", type=float, default=None,
                    help="GPU node-hours actually billed (from the cluster timeline), for the cost table")
    args = ap.parse_args()

    pop = json.load(open(args.population))
    lineages = pop["cohort_design"]["lineages"]
    index_of = {g["company_id"]: i for i, g in enumerate(pop["population"])}

    runs: Dict[str, Dict[str, Any]] = {}
    per_lineage: Dict[str, Dict[str, Any]] = {}
    for name, design in lineages.items():
        lruns = []
        for cid in design["replicas"]:
            run = load_run(args.root, cid, index_of[cid])
            run["lineage"] = name
            runs[cid] = run
            lruns.append(run)
        per_lineage[name] = summarise_lineage(name, design, lruns)

    done = [r for r in runs.values() if r.get("trajectory")]
    totals = {
        "runs_total": len(runs), "runs_with_results": len(done),
        "runs_with_scorecard": sum(1 for r in runs.values() if r.get("has_result")),
        "tokens_total": sum(int(r.get("tokens") or 0) for r in done),
        "cost_usd_total": round(sum(float(r.get("cost_usd") or 0.0) for r in done), 4),
        "elapsed_s_max": max((float(r.get("elapsed_s") or 0.0) for r in done), default=0.0),
        "elapsed_s_mean": _mean([r.get("elapsed_s") for r in done]),
        "gpu_node_hours": args.gpu_node_hours,
        "converged_runs": sum(1 for r in done if r.get("converged_at")),
    }
    pooled_all: Dict[str, int] = {}
    for r in done:
        for k, v in (r.get("search_stats") or {}).items():
            pooled_all[k] = pooled_all.get(k, 0) + int(v)
    totals["pooled_search_stats"] = pooled_all

    summary = {"generation": 16, "experiment": "V6 epistemic-search cohort", "root": args.root,
               "design": pop["cohort_design"], "totals": totals, "lineages": per_lineage, "runs": runs}
    os.makedirs(args.root, exist_ok=True)
    with open(os.path.join(args.root, "generation_16_cohort_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print("### Per lineage (unit of comparison; V6 = mean/min/max of the final held-out tests over seeds)")
    print("| lineage | role | Gen 15 (V5) trajectory | V6 final tests per seed | V6 mean (min–max) | converged (at iter) | "
          "first pass per seed | net (in-pod) | tokens | cost |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for name, L in per_lineage.items():
        rng = f"{fmt(L['v6_final_mean'])} ({fmt(L['v6_final_min'])}–{fmt(L['v6_final_max'])})" if L["v6_final_tests"] else "—"
        print(f"| `{name}` | {L['role']} | `{L['gen15_trajectory']}` | `{L['v6_final_tests']}` | {rng} | "
              f"{L['v6_converged_runs']}/{L['runs_with_results']} {L['v6_converged_at']} | `{L['v6_first_pass']}` | "
              f"{fmt(L['net_mean'], 2)} | {fmt(L['tokens_mean'], 0)} | ${fmt(L['cost_usd_mean'], 3)} |")

    print("\n### Per run")
    print("| run | trajectory | net | gross | gross (A3) | exec | integrity (in-pod / A3) | efficiency | iters | tokens | cost | min |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for cid, r in runs.items():
        if not r.get("trajectory") and not r.get("has_result"):
            print(f"| `{cid}` | (no results yet) | | | | | | | | | | |")
            continue
        print(f"| `{cid}` | `{r['trajectory']}` | {fmt(r.get('net'), 2)} | {fmt(r.get('gross'), 2)} | {fmt(r.get('gross_a3'), 2)} | "
              f"{fmt(r.get('execution_integrity'))} | {fmt(r.get('epistemic_integrity'))} / {fmt(r.get('integrity_a3'))} | "
              f"{fmt(r.get('epistemic_efficiency'))} | {fmt(r.get('iterations_used'))} | {fmt(r.get('tokens'))} | "
              f"${fmt(r.get('cost_usd'), 3)} | {fmt((r.get('elapsed_s') or 0) / 60.0, 0)} |")

    print("\n### V6 mechanics (pooled over the search iterations of each run)")
    print("| run | moves | frontier adm. / deferred | hyps acc. (tabu, dup) | exp. (S/F) | refused / repaired | "
          "syntheses: plan / authored / total (verified) | no-op retries (recovered) | forced low-prior (wins) | Brier (n) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for cid, r in runs.items():
        s = r.get("search_stats")
        if not s:
            continue
        a = r.get("audit") or {}
        print(f"| `{cid}` | {s.get('moves_used', 0)} | {s.get('frontier_admissions', 0)} / {s.get('questions_deferred', 0)} | "
              f"{s.get('hypotheses_accepted', 0)} ({s.get('tabu_rejections', 0)}, {s.get('duplicate_rejections', 0)}) | "
              f"{s.get('experiments', 0)} ({s.get('supported', 0)}/{s.get('falsified', 0)}) | "
              f"{s.get('rejected_probes', 0)} / {s.get('probe_repairs', 0)} | "
              f"{s.get('syntheses_by_plan', 0)} / {s.get('syntheses_authored', 0)} / {s.get('syntheses', 0)} ({s.get('syntheses_verified', 0)}) | "
              f"{s.get('synthesis_noop_retries', 0)} ({s.get('synthesis_noop_recoveries', 0)}) | "
              f"{s.get('forced_low_prior_picks', 0)} ({s.get('forced_low_prior_wins', 0)}) | "
              f"{fmt(a.get('calibration_brier'), 3)} ({fmt(a.get('calibration_tested'))}) |")

    print("\n### Totals")
    print(json.dumps({k: v for k, v in totals.items() if k != "pooled_search_stats"}, indent=1))
    print("pooled search stats:", json.dumps(pooled_all))


if __name__ == "__main__":
    main()
