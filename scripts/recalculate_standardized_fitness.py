"""
Standardized Physical Execution Fitness Recalculator.

Applies a uniform physical execution and sandbox verification standard across
all evolutionary generations (Gen 0 to Gen 7+).

If code was not physically tested or executed in a live sandbox scratchpad,
unverified gates (Smoke, Live Tests) are penalized as unexecutable, providing
an accurate empirical representation of true software quality and usability over time.
"""

import os
import re
import glob
import json
from typing import Dict, Any, List, Tuple

EXPERIMENTS = [
    ("Gen 0", "Baseline Pilot (1-Agent / Prose)", None),
    ("Gen 1", "Parallel Multi-Agent Foundation", "experiments/exp-003-parallel-gen1"),
    ("Gen 2", "Cross-Functional Convergence", "experiments/exp-004-parallel-gen2"),
    ("Gen 3", "Autonomous Specialization & Multi-Region", "experiments/exp-005-parallel-gen3"),
    ("Gen 4", "Consortiums & Autonomous Teleological OKRs", "experiments/exp-006-parallel-gen4"),
    ("Gen 5", "Active Container Scratchpad Sandbox", "experiments/exp-007-parallel-gen5"),
    ("Gen 6", "Industrial Hardening & Modular Architecture", "experiments/exp-008-parallel-gen6"),
    ("Gen 7", "Universal Multi-Platform Portability", "experiments/exp-009-parallel-gen7"),
    ("Gen 8", "Closed-Loop Sandbox Test Verification & Self-Repair", "experiments/exp-010-parallel-gen8"),
]

def recalculate():
    results = []

    for g_id, label, exp_dir in EXPERIMENTS:
        if exp_dir is None:
            # Gen 0: pure prose baseline, 0 files, no sandbox, 0/4 gates passed
            results.append({
                "generation": g_id,
                "label": label,
                "raw_champion": 50.25,
                "raw_average": 42.10,
                "corrected_champion": 25.25, # 50.25 - 25.00 (4 failed execution gates)
                "corrected_average": 20.00,
                "physical_sandbox": False,
                "tests_physically_executed": False,
                "firm_count": 1,
                "avg_files": 0.0,
                "max_files": 0
            })
            continue

        sc_files = sorted(glob.glob(os.path.join(exp_dir, "scorecards", "*.json")))
        if not sc_files:
            continue

        raw_scores = []
        corr_scores = []
        files_counts = []
        live_sandbox_count = 0
        tests_executed_count = 0

        for f in sc_files:
            with open(f, "r") as fp:
                d = json.load(fp)

            raw_net = d.get("net_fitness", d.get("overall_score", 0.0))
            raw_gross = d.get("fitness_score", d.get("gross_score", raw_net))
            verif = d.get("deterministic_verification", d.get("verification", {}))
            details = verif.get("details", "")

            is_live_sandbox = "[Live Sandbox]" in details
            if is_live_sandbox:
                live_sandbox_count += 1

            m = re.search(r"(\d+)\s+files", details)
            if m:
                files_count = int(m.group(1))
            else:
                files_count = verif.get("files_found_count", 0)
            files_counts.append(files_count)

            # Standardized 4-Gate Execution Assessment:
            # Gate 1: Build (pyproject/setup + runtime module)
            build_pass = verif.get("build_passed", False)
            # Gate 2: Smoke (requires physical files on disk / live runnable structure)
            smoke_pass = is_live_sandbox and verif.get("smoke_passed", False)
            # Gate 3: Telemetry (observability / tracing / span instrumentation)
            telemetry_pass = verif.get("telemetry_passed", False)
            # Gate 4: Test Gate (requires live pytest physical execution AND passing)
            test_pass = is_live_sandbox and verif.get("test_passed", False)

            if is_live_sandbox:
                tests_executed_count += 1

            passed_gates = sum([build_pass, smoke_pass, telemetry_pass, test_pass])
            std_penalty = (4 - passed_gates) * 6.25

            raw_pen = verif.get("score_penalty", 0.0)
            est_gross = raw_gross if raw_gross else (raw_net + raw_pen)
            eff_bonus = raw_net - (est_gross - raw_pen) if raw_gross else 0.0

            corr_net = round(max(0.0, est_gross - std_penalty + eff_bonus), 2)

            raw_scores.append(raw_net)
            corr_scores.append(corr_net)

        results.append({
            "generation": g_id,
            "label": label,
            "raw_champion": round(max(raw_scores), 2),
            "raw_average": round(sum(raw_scores) / len(raw_scores), 2),
            "corrected_champion": round(max(corr_scores), 2),
            "corrected_average": round(sum(corr_scores) / len(corr_scores), 2),
            "physical_sandbox": (live_sandbox_count == len(sc_files)),
            "tests_physically_executed": (tests_executed_count == len(sc_files)),
            "firm_count": len(sc_files),
            "avg_files": round(sum(files_counts) / len(files_counts), 1),
            "max_files": max(files_counts) if files_counts else 0
        })

    return results

if __name__ == "__main__":
    res = recalculate()
    out_path = "experiments/standardized_execution_fitness.json"
    with open(out_path, "w") as f:
        json.dump(res, f, indent=2)
    print(f"Saved standardized execution fitness to {out_path}\n")
    print(f"{'Gen':<6} | {'Raw Champ':<10} | {'Corr Champ':<11} | {'Sandbox':<8} | {'Avg Files':<10} | {'Max Files':<10} | {'Label'}")
    print("-" * 95)
    for r in res:
        print(f"{r['generation']:<6} | {r['raw_champion']:<10.2f} | {r['corrected_champion']:<11.2f} | {str(r['physical_sandbox']):<8} | {r['avg_files']:<10.1f} | {r['max_files']:<10} | {r['label']}")
