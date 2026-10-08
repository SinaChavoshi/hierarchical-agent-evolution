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


_STAT_KEYS = ("moves_used", "proposal_rounds", "hypotheses_accepted", "experiments", "supported", "falsified",
              "inconclusive", "rejected_probes", "probe_repairs", "syntheses", "syntheses_verified",
              "syntheses_unwritten", "recruits", "recruits_from_library", "recruits_synthesized", "recruit_declined",
              "questions_asked", "tabu_rejections", "duplicate_rejections")


def _sum_stats(search_blocks: List[Dict[str, Any]]) -> Dict[str, int]:
    """The loop's own counters (`SearchResult.stats`), summed over the iterations of one instance."""
    out = {k: 0 for k in _STAT_KEYS}
    for block in search_blocks or []:
        stats = block.get("stats") or {}
        for k in _STAT_KEYS:
            v = stats.get(k)
            if isinstance(v, (int, float)):
                out[k] += int(v)
    return out


def _moves_by_type(search_blocks: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for block in search_blocks or []:
        for mv in block.get("trajectory", []) or []:
            kind = str(mv.get("move_type") or "")
            out[kind] = out.get(kind, 0) + 1
    return out


def _synthesis_outcomes(search_blocks: List[Dict[str, Any]]) -> Dict[str, int]:
    """Syntheses that stayed (module check passed) vs. were reverted, from the synthesize moves' notes."""
    kept = reverted = 0
    for block in search_blocks or []:
        for mv in block.get("trajectory", []) or []:
            if str(mv.get("move_type") or "") != "synthesize":
                continue
            note = str(mv.get("note") or "")
            if "reverted" in note or "failed" in note:
                reverted += 1
            elif "passed" in note or float(mv.get("delta_u") or 0) > 0:
                kept += 1
    return {"kept": kept, "reverted": reverted}


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
        team0 = list((history[0].get("audit") or {}).get("team_names") or []) if history else []
        # Each iteration binds a fresh OrgState, so recruits are summed over the history.
        recruits = [r for h in history for r in ((h.get("org") or {}).get("recruit_log") or [])]
        n_lib = sum(1 for r in recruits if str(r.get("source", "")).startswith("lib"))
        n_syn = sum(1 for r in recruits if "synth" in str(r.get("source", "")))
        searches = rec.get("epistemic_searches") or []
        stats = _sum_stats(searches)
        synth = _synthesis_outcomes(searches)
        grade_path = os.path.join(run_dir, name, "grade.json")
        grade: Dict[str, Any] = {}
        if os.path.exists(grade_path):
            with open(grade_path, "r", encoding="utf-8") as fh:
                grade = json.load(fh)
        preflight = swe.get("preflight") or {}
        rows.append({
            "instance_id": rec.get("instance_id"),
            "stop": swe.get("stop"),
            "self_oracle": swe.get("resolved_by_self_oracle"),
            "graded_resolved": grade.get("resolved") if grade else None,
            "env_import": (None if not preflight else ("ok" if preflight.get("ok") else "FAILED")),
            "iterations": len(swe.get("iterations") or []),
            "moves_used": swe.get("moves_used"),
            "moves_by_type": _moves_by_type(searches),
            "experiments": stats["experiments"], "supported": stats["supported"],
            "falsified": stats["falsified"], "rejected_probes": stats["rejected_probes"],
            "probe_repairs": stats["probe_repairs"],
            "synth_kept": synth["kept"], "synth_reverted": synth["reverted"],
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
    cols = ["instance_id", "stop", "self_oracle", "graded_resolved", "env_import", "moves_used", "experiments", "supported",
            "falsified", "rejected_probes", "probe_repairs", "synth_kept", "synth_reverted", "recruits_library",
            "recruits_synthesized", "patch_chars", "tokens", "elapsed_min"]
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r.get(c)) for c in cols))
    for r in rows:
        print(f"\n{r['instance_id']}: root={r['root_module']} changed={r['changed_files']} moves={r['moves_by_type']}")
        print(f"  team0={r['team0']}\n  final={r['team_final']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
