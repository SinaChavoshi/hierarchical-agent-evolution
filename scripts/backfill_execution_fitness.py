"""Re-scores every archived generation against the real execution harness.

The published gates were heuristics: Build matched filenames, Smoke counted
files, and Telemetry substring-searched text that included the CEO's prose. Only
Tests executed anything. This script replays each archived workspace through
`src/execution_harness.py`, which actually parses, imports, and runs the code.

No re-runs and no inference cost are required: every scorecard retains its full
`run_output["workspace_files"]` path->content map.

Usage:
    PYTHONPATH=. python3 scripts/backfill_execution_fitness.py [--limit N] [--gen 10]
"""

import argparse
import glob
import json
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.execution_harness import ExecutionHarness, FAILED, PASSED, SKIPPED  # noqa: E402

EXPERIMENTS = [
    ("Gen 5", "experiments/exp-007-parallel-gen5"),
    ("Gen 6", "experiments/exp-008-parallel-gen6"),
    ("Gen 7", "experiments/exp-009-parallel-gen7"),
    ("Gen 8", "experiments/exp-010-parallel-gen8"),
    ("Gen 9", "experiments/exp-011-parallel-gen9"),
    ("Gen 10", "experiments/exp-012-parallel-gen10"),
]

OUT_PATH = "experiments/execution_grounded_fitness.json"
LEGACY_GATES = ("build_passed", "smoke_passed", "test_passed", "telemetry_passed")


def rescore_firm(harness, card):
    bundle = card.get("run_output", {}).get("workspace_files", {}) or {}
    gross = card.get("gross_score") or 0.0
    legacy_penalty = card.get("verification", {}).get("score_penalty", 0.0)

    t0 = time.time()
    report = harness.verify_bundle(bundle)
    elapsed = round(time.time() - t0, 2)

    legacy = card.get("verification", {})
    return {
        "company_id": card.get("company_id"),
        "gross_score": round(gross, 2),
        "legacy_penalty": legacy_penalty,
        "legacy_net": card.get("overall_score"),
        "legacy_gates": {g.replace("_passed", ""): bool(legacy.get(g)) for g in LEGACY_GATES},
        "harness_penalty": report.score_penalty,
        "harness_net": round(gross - report.score_penalty, 2),
        "harness_gates": {g.name: g.status for g in report.gates},
        "authored_files": report.authored_files,
        "python_files": report.python_files,
        "gate_detail": {g.name: g.detail[:220] for g in report.gates},
        "rescore_seconds": elapsed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max firms per generation")
    ap.add_argument("--gen", type=str, default=None, help="only this generation, e.g. '10'")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    harness = ExecutionHarness(timeout_s=args.timeout)
    results = []

    for gen_id, exp_dir in EXPERIMENTS:
        if args.gen and gen_id.split()[-1] != args.gen:
            continue
        cards = sorted(glob.glob(os.path.join(exp_dir, "scorecards", "*.json")))
        if args.limit:
            cards = cards[:args.limit]
        if not cards:
            continue

        print(f"\n=== {gen_id} ({len(cards)} firms) ===", flush=True)
        firms = []
        for path in cards:
            card = json.load(open(path))
            firm = rescore_firm(harness, card)
            firms.append(firm)
            gates = " ".join(
                f"{n[:3]}:{'P' if s == PASSED else 'F' if s == FAILED else '-'}"
                for n, s in firm["harness_gates"].items())
            print(f"  {firm['company_id']:<24} "
                  f"legacy {firm['legacy_net']:>6.2f} -> harness {firm['harness_net']:>6.2f}  "
                  f"[{gates}]  {firm['rescore_seconds']}s", flush=True)

        def rate(gate, status=PASSED):
            return sum(1 for f in firms if f["harness_gates"].get(gate) == status)

        results.append({
            "generation": gen_id,
            "firm_count": len(firms),
            "legacy_net_avg": round(st.mean(f["legacy_net"] for f in firms), 2),
            "legacy_net_max": round(max(f["legacy_net"] for f in firms), 2),
            "harness_net_avg": round(st.mean(f["harness_net"] for f in firms), 2),
            "harness_net_max": round(max(f["harness_net"] for f in firms), 2),
            "harness_penalty_avg": round(st.mean(f["harness_penalty"] for f in firms), 2),
            "authored_avg": round(st.mean(f["authored_files"] for f in firms), 2),
            "gate_pass": {g: rate(g) for g in
                          ("syntax", "build", "smoke", "tests", "telemetry")},
            "gate_skip": {g: rate(g, SKIPPED) for g in
                          ("syntax", "build", "smoke", "tests", "telemetry")},
            "firms": sorted(firms, key=lambda f: -f["harness_net"]),
        })

    with open(OUT_PATH, "w") as fh:
        json.dump(results, fh, indent=2)

    print(f"\n\nSaved execution-grounded fitness to {OUT_PATH}\n")
    hdr = (f"{'Gen':<8}{'Legacy avg':>12}{'Harness avg':>13}{'Legacy max':>12}"
           f"{'Harness max':>13}{'Syn':>6}{'Bld':>6}{'Smk':>6}{'Tst':>6}{'Tel':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        gp = r["gate_pass"]
        n = r["firm_count"]
        print(f"{r['generation']:<8}{r['legacy_net_avg']:>12.2f}{r['harness_net_avg']:>13.2f}"
              f"{r['legacy_net_max']:>12.2f}{r['harness_net_max']:>13.2f}"
              f"{gp['syntax']:>3}/{n:<2}{gp['build']:>3}/{n:<2}{gp['smoke']:>3}/{n:<2}"
              f"{gp['tests']:>3}/{n:<2}{gp['telemetry']:>3}/{n:<2}")


if __name__ == "__main__":
    main()
