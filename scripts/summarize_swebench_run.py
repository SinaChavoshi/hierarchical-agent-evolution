#!/usr/bin/env python3
"""Summarise SWE-bench run directories (results/swebench/<run>/<instance_id>/record.json) as a table.

    PYTHONPATH=. python3 scripts/summarize_swebench_run.py results/swebench/smoke_2026-10-08 [--json]

One row per instance: stop reason, self-oracle verdict, moves, syntheses (accepted / reverted),
probes (supported / falsified / untestable), turn-0 team, recruits (library / synthesised),
patch size and changed files, tokens, wall time. Reads only `record.json`; never the dataset.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List


def _count_moves(search_blocks: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"moves": 0, "propose": 0, "experiment": 0, "synthesize": 0, "synth_ok": 0, "synth_reverted": 0,
           "recruit": 0, "question": 0}
    for block in search_blocks or []:
        for mv in block.get("trajectory", []) or []:
            kind = str(mv.get("move") or mv.get("kind") or "")
            out["moves"] += 1
            if "PROPOSE" in kind:
                out["propose"] += 1
            elif "EXPERIMENT" in kind:
                out["experiment"] += 1
            elif "SYNTH" in kind:
                out["synthesize"] += 1
                summary = str(mv.get("summary") or mv.get("note") or "")
                if "reverted" in summary or "failed" in summary:
                    out["synth_reverted"] += 1
                elif "passed" in summary or float(mv.get("delta_u") or 0) > 0:
                    out["synth_ok"] += 1
            elif "RECRUIT" in kind:
                out["recruit"] += 1
            elif "QUESTION" in kind:
                out["question"] += 1
    return out


def _verdicts(ledger: Dict[str, Any]) -> Dict[str, int]:
    out = {"supported": 0, "falsified": 0, "untestable": 0, "open": 0}
    hyps = ledger.get("hypotheses") or {}
    items = hyps.values() if isinstance(hyps, dict) else hyps
    for h in items:
        status = str(h.get("status", "")).upper()
        key = status.lower() if status.lower() in out else "open"
        out[key] += 1
    return out


def summarize(run_dir: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(run_dir)):
        path = os.path.join(run_dir, name, "record.json")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        swe = rec.get("swebench") or {}
        org = rec.get("org") or {}
        history = rec.get("org_history") or []
        team0 = []
        if history:
            team0 = list((history[0].get("audit") or {}).get("team_names") or [])
        recruits = org.get("recruit_log") or []
        n_lib = sum(1 for r in recruits if str(r.get("source", r.get("mode", ""))).startswith("lib"))
        n_syn = sum(1 for r in recruits if "synth" in str(r.get("source", r.get("mode", ""))))
        moves = _count_moves(rec.get("epistemic_searches") or [])
        verdicts = _verdicts(rec.get("epistemic_ledger") or {})
        rows.append({
            "instance_id": rec.get("instance_id"),
            "stop": swe.get("stop"),
            "self_oracle": swe.get("resolved_by_self_oracle"),
            "iterations": len(swe.get("iterations") or []),
            "moves_used": swe.get("moves_used"),
            "propose": moves["propose"], "experiment": moves["experiment"],
            "synth_ok": moves["synth_ok"], "synth_reverted": moves["synth_reverted"],
            "supported": verdicts["supported"], "falsified": verdicts["falsified"],
            "untestable": verdicts["untestable"],
            "team0": team0, "team_final": [r.get("name") for r in org.get("active_roles") or []],
            "recruits_library": n_lib, "recruits_synthesized": n_syn,
            "root_module": swe.get("root_module"), "changed_files": swe.get("changed_files"),
            "patch_chars": swe.get("patch_chars"), "tokens": rec.get("token_usage"),
            "elapsed_min": round(float(rec.get("elapsed_seconds") or 0) / 60.0, 1),
        })
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = summarize(args.run_dir)
    if args.json:
        json.dump(rows, sys.stdout, indent=1, default=str)
        print()
        return 0
    cols = ["instance_id", "stop", "self_oracle", "moves_used", "propose", "experiment", "synth_ok",
            "synth_reverted", "supported", "falsified", "untestable", "recruits_library", "recruits_synthesized",
            "patch_chars", "tokens", "elapsed_min"]
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r.get(c)) for c in cols))
    for r in rows:
        print(f"\n{r['instance_id']}: root={r['root_module']} changed={r['changed_files']}")
        print(f"  team0={r['team0']}\n  final={r['team_final']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
