"""Summarise the Gen 16 V6 pilot (passes 1-3) against the Gen 15 baseline.

Reads results/hae_gen16_v6_pilot/{outputs,r2/outputs,r3/outputs}/<firm>/generation_16/*.json
and the pod logs, and writes results/hae_gen16_v6_pilot/generation_16_pilot_summary.json
plus a markdown table on stdout.
"""
from __future__ import annotations

import glob
import json
import os
import re

ROOT = "results/hae_gen16_v6_pilot"
POP = json.load(open("configs/generation_16_pilot_population.json"))
GEN15 = POP["pilot_design"]["gen15_reference"]
ITER_RE = re.compile(r"\[Iteration (\d+)/(\d+)\] \[self-hosting-benchmark\] (?:(\d+)/50 held-out tests|submission mutates sys\.path)")


def trajectory_from_log(path):
    if not path or not os.path.exists(path):
        return []
    txt = open(path, errors="replace").read()
    return [int(m.group(3)) if m.group(3) else 0 for m in ITER_RE.finditer(txt)]


TRACE_RE = re.compile(r"\[Iteration \d+/\d+\]|\[epistemic\]|\[V6 EPISTEMIC\]|EPISTEMIC AUDIT|FINAL NET FITNESS|"
                      r"WORKER_EXIT|Traceback|Error:|hypothesis packet|proposer")


def write_trace(log_path, trace_path):
    """Pod logs are git-ignored (*.log) and carry a base64 tarball; keep a small
    committable trace of what the search did, as evidence for the findings."""
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


def load_pass(sub: str):
    base = os.path.join(ROOT, sub) if sub else ROOT
    out = {}
    pop_ids = [g["company_id"] for g in POP["population"]]
    for i, cid in enumerate(pop_ids):
        rp = os.path.join(base, "outputs", cid, "generation_16", f"{cid}_result.json")
        tp = os.path.join(base, "outputs", cid, "generation_16", f"{cid}_epistemic_tree.json")
        logs = sorted(glob.glob(os.path.join(base, "pod_logs", f"pod_{i}_*.log")))
        entry = {"company_id": cid, "gen15_trajectory": GEN15.get(cid), "log": logs[-1] if logs else None}
        entry["trajectory"] = trajectory_from_log(entry["log"])
        entry["trace"] = write_trace(entry["log"], os.path.join(base, "traces", f"{cid}_epistemic_trace.txt"))
        if os.path.exists(rp):
            r = json.load(open(rp))
            entry.update({
                "fitness": r.get("fitness_score"), "gross": r.get("gross_score"),
                "execution_integrity": r.get("execution_integrity"),
                "epistemic_integrity": r.get("epistemic_integrity"),
                "epistemic_efficiency": r.get("epistemic_efficiency"),
                "cost_usd": (r.get("opex") or {}).get("estimated_cost_usd"),
                "elapsed_s": r.get("elapsed_seconds"),
                "tokens": (r.get("token_usage") or {}).get("total_tokens") if isinstance(r.get("token_usage"), dict) else r.get("token_usage"),
                "iterations_used": r.get("iterations_used"),
            })
        if os.path.exists(tp):
            t = json.load(open(tp))
            a = t.get("audit") or {}
            entry["audit"] = {k: a.get(k) for k in (
                "questions", "questions_open", "questions_resolved", "questions_certified",
                "hypotheses", "hypotheses_tested", "hypotheses_supported", "hypotheses_falsified",
                "hypotheses_untestable", "probes_refused", "probe_repairs",
                "settled_facts", "ruled_out", "calibration_brier", "calibration_tested",
                "low_prior_wins", "forced_low_prior_picks", "forced_low_prior_wins",
                "syntheses", "syntheses_verified", "moves_used", "budget_moves")}
            entry["searches"] = [{"iteration": s.get("iteration"), "moves": s.get("moves_used"),
                                  "stop": s.get("stop_reason"), "dU": s.get("delta_u_total"),
                                  "synthesized": s.get("synthesized_paths")} for s in t.get("searches") or []]
            # Reasons hypotheses were tested / what the gatekeeper decided
            led = t.get("ledger") or {}
            hyps = led.get("hypotheses") or {}
            hyps = list(hyps.values()) if isinstance(hyps, dict) else hyps
            entry["hypothesis_outcomes"] = [
                {"q": h.get("question_id"), "prior": h.get("prior"), "status": h.get("status"),
                 "claim": (h.get("claim") or "")[:110]} for h in hyps][:40]
        out[cid] = entry
    return out


def main():
    summary = {"generation": 16, "experiment": "V6 epistemic-search pilot",
               "pass_1": load_pass(""), "pass_2": load_pass("r2"), "pass_3": load_pass("r3")}
    os.makedirs(ROOT, exist_ok=True)
    with open(os.path.join(ROOT, "generation_16_pilot_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    for name in ("pass_1", "pass_2", "pass_3"):
        print(f"\n### {name}")
        print("| firm | Gen 15 (V5) | this pass | net fitness | hyps tested (S/F) | refused/repaired | "
              "forced picks (wins) | syntheses (verified) | settled / ruled out | cost |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for cid, e in summary[name].items():
            a = e.get("audit") or {}
            if not e.get("trajectory") and not a:
                continue
            print(f"| {cid} | {e.get('gen15_trajectory')} | {e.get('trajectory')} | {e.get('fitness')} | "
                  f"{a.get('hypotheses_tested')} ({a.get('hypotheses_supported')}/{a.get('hypotheses_falsified')}) | "
                  f"{a.get('probes_refused')}/{a.get('probe_repairs')} | "
                  f"{a.get('forced_low_prior_picks')} ({a.get('forced_low_prior_wins')}) | "
                  f"{a.get('syntheses')} ({a.get('syntheses_verified')}) | "
                  f"{a.get('settled_facts')} / {a.get('ruled_out')} | ${e.get('cost_usd') or 0:.3f} |")


if __name__ == "__main__":
    main()
